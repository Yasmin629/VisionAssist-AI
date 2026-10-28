"""
ScoreCalculator - combines internal OpenCV feature checks into ONE clean
result: authenticity_score, confidence, final_result, recommendation, and
a pass/fail list for exactly 5 security features. No raw OpenCV metrics
(blur, brightness, glare, edge density, perspective, background, color
consistency, focus score, etc.) are ever exposed outside this module -
they are inputs only.
"""

from __future__ import annotations

import statistics

# Internal scoring weights. Only these feed the authenticity score.
# blur/brightness/glare are combined into one internal "image_quality"
# signal - used for scoring reliability only, never shown to the user.
#
# security_thread and watermark carry almost all the weight - they're
# the most reliable, lighting-robust checks. portrait/text_clarity/
# serial_number are still shown to the user as pass/fail checkmarks
# (informational, for their own inspection) but barely move the actual
# score anymore - real-world testing showed even a well-lit, clearly
# genuine note commonly fails 1-2 of those edge/corner checks just from
# ordinary camera framing, which previously dragged real notes down into
# "Suspicious" even when the two most meaningful checks read cleanly.
WEIGHTS = {
    "security_thread": 0.45,
    "watermark": 0.35,
    "image_quality": 0.12,  # composite of blur + brightness + glare
    "text_clarity": 0.05,   # RBI / Reserve Bank text region
    "portrait": 0.02,
    "serial_number": 0.01,
}

# The only 5 features ever shown to the user, in this order.
DISPLAY_ORDER = [
    ("security_thread", "Security Thread"),
    ("watermark", "Watermark"),
    ("text_clarity", "RBI Text"),
    ("serial_number", "Serial Number"),
    ("portrait", "Gandhi Portrait"),
]

# A feature "passes" (shown with a check mark) once its internal score
# clears this bar. Deliberately lenient - these are visibility proxies,
# not authenticity proof, so even a moderate reading is treated as a
# pass. (Previously 50 - lowered after real-world testing showed genuine
# notes with ordinary glare/cropping still under-read on the edge/corner
# checks even after widening their regions in feature_analyzer.py.)
FEATURE_PASS_THRESHOLD = 28.0

# Quality gate: if the photo itself is too poor, don't score anything -
# say so plainly ("Unable to Verify") instead of guessing "Likely Fake".
QUALITY_GATE_CHECKS = ["blur", "brightness", "glare"]
QUALITY_GATE_THRESHOLD = 35.0

GENUINE_SCORE_THRESHOLD = 50.0

RECOMMENDATIONS = {
    "Genuine Currency Detected": "Visible security features (security thread, watermark, portrait, and print clarity) match standard genuine currency features. Note: This is an indicative check, not a legal authentication.",
    "Potential Fake Currency Detected": "Warning: Key security features (such as security thread, watermark, or engraved details) are missing, faded, or inconsistent. Physical verification recommended before accepting. Note: This is an indicative check, not a legal authentication.",
    "Unable to Verify": "Image quality is too low (blurry, dim, or glare) to evaluate security features accurately. Please hold the note steady in good lighting and try again.",
    "No Note Detected": "Hold a currency note steady in the camera view to check its authenticity.",
}

# Confidence and authenticity score are always reported in this band -
# never 100% (never claim certainty), never below 60% (a scan that ran
# at all reflects a minimum baseline of readable evidence).
SCORE_MIN, SCORE_MAX = 60.0, 99.0
CONFIDENCE_MIN, CONFIDENCE_MAX = 60.0, 99.0


class ScoreCalculator:
    def calculate(self, feature_results: dict) -> dict:
        """feature_results: {check_name: {score, confidence, status, remarks}}
        raw checks straight from FeatureAnalyzer - "blur", "brightness",
        "glare", "security_thread", "watermark", "portrait", "text_clarity",
        "serial_number". Anything else is ignored."""

        # --- Quality gate ---
        quality_raw = [feature_results[name]["score"] for name in QUALITY_GATE_CHECKS if name in feature_results]
        avg_quality = sum(quality_raw) / len(quality_raw) if quality_raw else 100.0

        blur_score = feature_results.get("blur", {}).get("score", 100.0)
        brightness_score = feature_results.get("brightness", {}).get("score", 100.0)
        glare_score = feature_results.get("glare", {}).get("score", 100.0)

        # A severe defect in any single image-quality factor (e.g. excessive blur, pitch darkness)
        # or poor overall quality prevents reliable verification of microscopic security features.
        if avg_quality < QUALITY_GATE_THRESHOLD or blur_score < 25.0 or brightness_score < 20.0 or glare_score < 15.0:
            return {
                "authenticity_score": None,
                "confidence": None,
                "final_result": "Unable to Verify",
                "recommendation": RECOMMENDATIONS["Unable to Verify"],
                "features": self._features(feature_results),
            }

        # --- Weighted authenticity score ---
        metrics = {}
        for name in WEIGHTS:
            if name == "image_quality":
                metrics[name] = avg_quality
            elif name in feature_results:
                metrics[name] = feature_results[name]["score"]

        weight_total = sum(WEIGHTS[n] for n in metrics)
        raw_score = (sum(metrics[n] * WEIGHTS[n] for n in metrics) / weight_total) if weight_total else 0.0

        features_list = self._features(feature_results)
        passed_count = sum(1 for f in features_list if f["passed"])
        st_passed = any(f["label"] == "Security Thread" and f["passed"] for f in features_list)
        wm_passed = any(f["label"] == "Watermark" and f["passed"] for f in features_list)

        # Genuine requires raw_score >= GENUINE_SCORE_THRESHOLD and at least 3 of 5 features passed
        # (including security thread or watermark), or at least 4 of 5 features passed.
        is_genuine = (
            (raw_score >= GENUINE_SCORE_THRESHOLD and passed_count >= 3 and (st_passed or wm_passed))
            or (passed_count >= 4 and raw_score >= 45.0)
        )
        if is_genuine:
            final_result = "Genuine Currency Detected"
        else:
            final_result = "Potential Fake Currency Detected"

        authenticity_score = self._clamp_reported(raw_score, SCORE_MIN, SCORE_MAX)
        confidence = self._confidence(metrics)

        return {
            "authenticity_score": authenticity_score,
            "confidence": confidence,
            "final_result": final_result,
            "recommendation": RECOMMENDATIONS[final_result],
            "features": features_list,
        }

    @staticmethod
    def _clamp_reported(value: float, low: float, high: float) -> float:
        """Classification uses the true raw_score so 'Likely Fake' still
        means what it says even below 60 internally, but the number
        shown to the user always stays within [low, high] - never 100,
        never a discouragingly-precise near-zero value."""
        return round(max(low, min(high, value)), 1)

    @staticmethod
    def _confidence(metrics: dict) -> float:
        """How much the checks agree with each other, scaled by overall
        capture quality - NOT a genuineness confidence. Always reported
        between CONFIDENCE_MIN and CONFIDENCE_MAX."""
        values = list(metrics.values())
        if len(values) < 2:
            base = values[0] if values else 70.0
            return round(max(CONFIDENCE_MIN, min(CONFIDENCE_MAX, base)), 0)

        spread = statistics.pstdev(values)
        agreement = max(0.0, min(100.0, 100 - spread * 1.3))
        quality_factor = max(0.5, min(1.0, (metrics.get("image_quality", 70.0) / 100.0) ** 0.5))
        confidence = agreement * quality_factor
        return round(max(CONFIDENCE_MIN, min(CONFIDENCE_MAX, confidence)), 0)

    @staticmethod
    def _features(feature_results: dict) -> list:
        """Exactly 5 items: {label, passed}. No scores, no status text,
        no raw OpenCV metric ever included here."""
        out = []
        for key, label in DISPLAY_ORDER:
            if key in feature_results:
                passed = bool(feature_results[key]["score"] >= FEATURE_PASS_THRESHOLD)
            else:
                passed = False
            out.append({"label": label, "passed": passed})
        return out