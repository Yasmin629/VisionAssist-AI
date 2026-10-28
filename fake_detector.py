"""
Fake currency check - single clean result, offline only
(Python + OpenCV + NumPy + Flask; no TensorFlow, no second YOLO model,
no fake-currency dataset).

Two-tier design:
  - If a real trained classifier exists at model/fake_best.pt, its
    verdict/confidence is authoritative for "Authenticity Result" /
    "Confidence".
  - Normally no such model exists (training one needs a labeled genuine
    vs counterfeit dataset, which this project deliberately does not
    use), so the result falls back entirely to the OpenCV feature-check
    score from feature_analyzer.py / score_calculator.py.

Only ONE result is ever produced per scan:
    authenticity_score, confidence, final_result, recommendation,
    features (exactly 5: Security Thread, Watermark, RBI Text,
    Serial Number, Gandhi Portrait - pass/fail only, no raw scores).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2

from utils.feature_analyzer import FeatureAnalyzer
from utils.score_calculator import ScoreCalculator

ROOT = Path(__file__).resolve().parent
FAKE_MODEL_CANDIDATES = [
    ROOT / "model" / "fake_best.pt",
    ROOT / "model" / "fake_detector.pt",
    ROOT / "model" / "fake_currency.pt",
    ROOT / "model" / "fake.pt",
]
FAKE_MODEL_NOT_FOUND_MESSAGE = "Fake Detection Model Missing (Using Computer Vision Feature Check)"

_fake_model: Any = None
_engine = "unavailable"
_active_model_path: Path | None = None
_feature_analyzer = FeatureAnalyzer(ROOT / "dataset")
_score_calculator = ScoreCalculator()


def _load_model() -> None:
    global _fake_model, _engine, _active_model_path
    for candidate in FAKE_MODEL_CANDIDATES:
        if candidate.exists():
            try:
                from ultralytics import YOLO
                _fake_model = YOLO(str(candidate))
                _engine = "yolo"
                _active_model_path = candidate
                print(f"Loaded fake currency detection model from {candidate.name}")
                return
            except Exception as error:
                print(f"{candidate.name} found but could not be loaded: {error}")


_load_model()


def model_status() -> dict[str, str]:
    if _engine == "yolo" and _active_model_path:
        return {"engine": "yolo", "label": f"Fake-detection model ready ({_active_model_path.name})"}
    return {"engine": "unavailable", "label": FAKE_MODEL_NOT_FOUND_MESSAGE}


def _classify_genuineness(image_path: str):
    """Returns (verdict, confidence) from the trained model if available,
    supporting both classification and object detection architectures."""
    if _engine != "yolo" or _fake_model is None:
        return None
    try:
        results = _fake_model.predict(image_path, verbose=False)
        if not results:
            return None
        r = results[0]

        # Classification task (has .probs)
        if getattr(r, "probs", None) is not None and r.probs is not None:
            probs = r.probs
            top_idx = int(probs.top1)
            confidence = float(probs.top1conf)
            label = str(r.names[top_idx]).lower()
            verdict = (
                "Genuine Currency Detected"
                if any(w in label for w in ("genuine", "real", "authentic"))
                else "Potential Fake Currency Detected"
            )
            return verdict, confidence

        # Detection task (has .boxes)
        if getattr(r, "boxes", None) is not None and len(r.boxes) > 0:
            top_box = sorted(r.boxes, key=lambda b: float(b.conf[0]), reverse=True)[0]
            cls_id = int(top_box.cls[0])
            confidence = float(top_box.conf[0])
            label = str(r.names.get(cls_id, "")).lower()
            verdict = (
                "Genuine Currency Detected"
                if any(w in label for w in ("genuine", "real", "authentic"))
                else "Potential Fake Currency Detected"
            )
            return verdict, confidence

    except Exception as error:
        print(f"Error during fake classification inference: {error}")
        return None

    return None


def _collect_feature_results(image) -> dict:
    """Only the checks the scorer actually uses - nothing else is computed."""
    return {
        "blur": _feature_analyzer.check_blur(image),
        "brightness": _feature_analyzer.check_brightness(image),
        "glare": _feature_analyzer.check_glare(image),
        "security_thread": _feature_analyzer.check_security_thread_region(image),
        "watermark": _feature_analyzer.check_watermark_region(image),
        "portrait": _feature_analyzer.check_portrait_clarity(image),
        "text_clarity": _feature_analyzer.check_text_clarity(image),
        "serial_number": _feature_analyzer.check_serial_number_region(image),
    }


def analyze_note(image_path: str, denomination: str | None = None) -> dict:
    """Returns the single clean result card with standardized verdicts:
    'Genuine Currency Detected', 'Potential Fake Currency Detected',
    or 'Unable to Verify'."""

    image = cv2.imread(image_path)
    if image is None:
        return {
            "authenticity_score": None,
            "confidence": None,
            "final_result": "Unable to Verify",
            "recommendation": "Could not read the image clearly. Please retake the photo with better lighting.",
            "features": [],
            "verdict": "Unable to Verify",
            "model_available": False,
        }

    feature_results = _collect_feature_results(image)
    score_data = _score_calculator.calculate(feature_results)

    result = {
        "authenticity_score": score_data["authenticity_score"],
        "confidence": score_data["confidence"],
        "final_result": score_data["final_result"],
        "recommendation": score_data["recommendation"],
        "features": score_data["features"],
    }

    verdict_result = _classify_genuineness(image_path)
    if verdict_result:
        # A trained classifier/detector is authoritative when available.
        verdict, model_confidence = verdict_result
        capped_conf = round(max(60.0, min(99.0, model_confidence * 100)), 0)
        result["final_result"] = verdict
        result["confidence"] = capped_conf
        result["verdict"] = verdict
        result["model_available"] = True
    else:
        result["verdict"] = score_data["final_result"]
        result["model_available"] = False

    return result