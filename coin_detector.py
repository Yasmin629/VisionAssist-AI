"""
Stable Indian Coin Detection using YOLOv8.

Supports:
- YOLO object-detection models
- YOLO classification models
- Confidence filtering
- No-coin rejection
- Stable class-name mapping
- Clear Flask-compatible output

IMPORTANT:
A classification model can still predict a class when there is no coin.
For best "no coin" rejection, use a YOLO detection model trained with
proper background/no-coin images.
"""

from __future__ import annotations

import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any, Optional, Tuple

import cv2
import numpy as np

try:
    from ultralytics import YOLO
except Exception:
    YOLO = None


# ============================================================
# CONFIGURATION
# ============================================================

ROOT = Path(__file__).resolve().parent

MODEL_CANDIDATES = [
    ROOT / "model" / "coins" / "coin_best.pt",
    ROOT / "model" / "coin_best.pt",
    ROOT / "model" / "coins_best.pt",
    ROOT / "model" / "coins.pt",
    ROOT / "model" / "coin_model.pt",
    ROOT / "runs" / "detect" / "train" / "weights" / "best.pt",
]

# Do NOT use a very low threshold.
# Weak predictions are usually the reason for false coin detections.
DETECTION_CONFIDENCE = 0.35

# Classification models are ALWAYS forced to pick a class (no "none of
# these" option), so this needs to be meaningfully stricter than the
# detection threshold above - but 0.80 (the previous value here) is too
# strict: measured against this project's own trained coin_best.pt files
# across many earlier tests, genuine correct predictions commonly land
# in the 0.35-0.70 range, especially without a dedicated "no_coin" class
# in training. At 0.80, most correct detections were likely being
# silently rejected as "no coin".
CLASSIFICATION_CONFIDENCE = 0.45

# Minimum gap required between the top prediction and the runner-up.
# Rejects ambiguous/tied predictions on background noise or blurry coins.
CLASSIFICATION_MARGIN = 0.18

# Class names that mean "this isn't a coin at all" if the model was
# trained with such a class (see retrain_coin_classifier.ipynb Section 7).
# Trusted regardless of confidence - a confident "no_coin" IS the correct
# answer, not a rejection case.
NO_COIN_CLASS_NAMES = {"no_coin", "background", "none", "empty"}

# Minimum bounding-box area relative to the complete image.
# Very tiny detections are usually noise.
MIN_BOX_AREA_RATIO = 0.05

# Maximum reasonable coin box area.
MAX_BOX_AREA_RATIO = 0.95

# If a detection is extremely close to the image edge,
# it may be an incomplete/background detection.
EDGE_MARGIN = 5


# ============================================================
# COIN NAME NORMALIZATION
# ============================================================

COIN_ALIASES = {
    "1": "₹1",
    "01": "₹1",
    "1.0": "₹1",
    "rs1": "₹1",
    "rs.1": "₹1",
    "₹1": "₹1",
    "one": "₹1",
    "one rupee": "₹1",
    "1_rupee": "₹1",
    "1 rupee": "₹1",
    "1 rupee coin": "₹1",
    "coin_1": "₹1",
    "coin1": "₹1",

    "2": "₹2",
    "02": "₹2",
    "2.0": "₹2",
    "rs2": "₹2",
    "rs.2": "₹2",
    "₹2": "₹2",
    "two": "₹2",
    "two rupees": "₹2",
    "2_rupee": "₹2",
    "2 rupee": "₹2",
    "2 rupee coin": "₹2",
    "coin_2": "₹2",
    "coin2": "₹2",

    "5": "₹5",
    "05": "₹5",
    "5.0": "₹5",
    "rs5": "₹5",
    "rs.5": "₹5",
    "₹5": "₹5",
    "five": "₹5",
    "five rupees": "₹5",
    "5_rupee": "₹5",
    "5 rupee": "₹5",
    "5 rupee coin": "₹5",
    "coin_5": "₹5",
    "coin5": "₹5",

    "10": "₹10",
    "010": "₹10",
    "10.0": "₹10",
    "rs10": "₹10",
    "rs.10": "₹10",
    "₹10": "₹10",
    "ten": "₹10",
    "ten rupees": "₹10",
    "10_rupee": "₹10",
    "10 rupee": "₹10",
    "10 rupee coin": "₹10",
    "coin_10": "₹10",
    "coin10": "₹10",

    "20": "₹20",
    "020": "₹20",
    "20.0": "₹20",
    "rs20": "₹20",
    "rs.20": "₹20",
    "₹20": "₹20",
    "twenty": "₹20",
    "twenty rupees": "₹20",
    "20_rupee": "₹20",
    "20 rupee": "₹20",
    "20 rupee coin": "₹20",
    "coin_20": "₹20",
    "coin20": "₹20",
}


def normalize_coin_name(name: Any) -> str:
    """
    Convert model class names into canonical '₹X' format.
    Checks exact matches and multi-digit values first so that
    10/20 are never mistakenly matched by 1/2 substrings.
    """
    text = str(name).strip().lower()

    if text in COIN_ALIASES:
        return COIN_ALIASES[text]

    cleaned = (
        text.replace("rupees", "")
        .replace("rupee", "")
        .replace("rs.", "")
        .replace("rs", "")
        .replace("coin", "")
        .replace("_", " ")
        .replace("-", " ")
        .strip()
    )

    if cleaned in COIN_ALIASES:
        return COIN_ALIASES[cleaned]

    # Check whole number tokens first - descending order
    digits = re.findall(r"\d+", text)
    if digits:
        for val in ("20", "10", "5", "2", "1"):
            if val in digits:
                return f"₹{val}"

    # Word-boundary matching
    for val in ("20", "10", "5", "2", "1"):
        if re.search(r"\b" + val + r"\b", cleaned) or val == cleaned:
            return f"₹{val}"

    return str(name)


# ============================================================
# MODEL LOADING
# ============================================================

_yolo_model: Optional[Any] = None
_model_path: Optional[Path] = None
_engine = "unavailable"
_model_task = "unknown"


def _find_model() -> Optional[Path]:
    for path in MODEL_CANDIDATES:
        if path.exists():
            return path

    return None


def _load_model() -> None:
    global _yolo_model
    global _model_path
    global _engine
    global _model_task

    if YOLO is None:
        print("ERROR: ultralytics is not installed.")
        return

    weights = _find_model()

    if weights is None:
        print("ERROR: Coin YOLO model not found.")
        print("Checked:")

        for path in MODEL_CANDIDATES:
            print("  ", path)

        return

    try:
        print("=" * 60)
        print("Loading coin model:")
        print(weights)

        _yolo_model = YOLO(str(weights))

        _model_path = weights
        _engine = "yolo"

        _model_task = getattr(_yolo_model, "task", "unknown")

        print("Coin model loaded successfully.")
        print("Task:", _model_task)
        print("Classes:", getattr(_yolo_model, "names", {}))
        print("=" * 60)

    except Exception as exc:
        print("ERROR loading coin model:")
        print(exc)

        _yolo_model = None
        _model_path = None
        _engine = "unavailable"
        _model_task = "unknown"


_load_model()


# ============================================================
# MODEL STATUS
# ============================================================

def model_status() -> dict:
    if _yolo_model is None:
        return {
            "engine": "unavailable",
            "label": "Coin model not trained",
            "model_path": None,
            "task": "unknown",
        }

    return {
        "engine": "yolo",
        "label": "Coin detection model ready",
        "model_path": str(_model_path) if _model_path else None,
        "task": _model_task,
        "classes": getattr(_yolo_model, "names", {}),
        "confidence_threshold": DETECTION_CONFIDENCE,
    }


# ============================================================
# IMAGE VALIDATION
# ============================================================

def _read_image(image_path: str) -> Optional[np.ndarray]:
    image = cv2.imread(str(image_path))

    if image is None:
        return None

    if image.size == 0:
        return None

    return image


def _image_is_valid(image: np.ndarray) -> bool:
    """
    Reject extremely dark/bright or unusable frames.
    This is NOT coin classification.
    It only prevents obviously bad camera frames.
    """

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    mean = float(np.mean(gray))

    if mean < 15:
        return False

    if mean > 245:
        return False

    return True


def extract_coin_roi(image: np.ndarray) -> Tuple[np.ndarray, bool, Optional[Tuple[int, int, int, int]], str, str]:
    """
    Check if a circular/coin-like object is present in the frame.
    Returns:
        (roi_image, has_coin, bounding_box, guidance, guidance_text)
    - If the image itself is already a direct/standalone coin photo (square w==h or portrait h>w, aspect <= 1.40),
      returns (image, True, (0, 0, w, h), "centered", "Hold steady"). This preserves the natural feature scale of ₹1, ₹2, ₹5, ₹10, ₹20 coins.
    - If a coin is detected inside a wider camera scene (webcam landscape w > h, e.g. 640x480, 1280x720, 1920x1080),
      detects the coin circle via HoughCircles, determines whether it is centered or needs left/right adjustment,
      and crops with generous padding (40%) to match the training dataset scale.
    - If no coin is found, returns (image, False, None, "none", "Hold coin in camera view").
    """
    if image is None or image.size == 0:
        return image, False, None, "none", "Hold coin in camera view"

    h, w = image.shape[:2]
    aspect = max(w, h) / float(max(1, min(w, h)))
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    if np.std(gray) < 18:
        return image, False, None, "none", "Hold coin in camera view"

    # 1. Standalone dataset / macro photo:
    # Dataset images are square (0.90 <= w/h <= 1.10) or portrait (h > w and aspect <= 1.40).
    # Real camera/webcam feeds are landscape (w > h, e.g. 640x480, 1280x720, 1920x1080).
    if (0.90 <= w / float(h) <= 1.10) or (h > w and aspect <= 1.40):
        cy, cx = h // 2, w // 2
        r_center = int(min(w, h) * 0.3)
        center_patch = gray[max(0, cy - r_center):min(h, cy + r_center), max(0, cx - r_center):min(w, cx + r_center)]
        if center_patch.size > 0 and np.std(center_patch) > 16.0:
            return image, True, (0, 0, w, h), "centered", "Hold steady"
        return image, False, None, "none", "Hold coin in camera view"

    # 2. Webcam camera frame (w > h, landscape)
    scale = 480.0 / float(h)
    sw, sh = max(1, int(w * scale)), 480
    small_gray = cv2.resize(gray, (sw, sh))
    blurred = cv2.GaussianBlur(small_gray, (9, 9), 2)

    # Coin radius range in webcam view (26px to 92px at 480p height)
    min_r = max(26, int(480 * 0.055))
    max_r = int(480 * 0.19)

    # param2=46 requires a distinct, well-defined circle, preventing empty room textures/shadows
    # from triggering continuous false circle candidates.
    circles = cv2.HoughCircles(
        blurred, cv2.HOUGH_GRADIENT, dp=1.2, minDist=35,
        param1=85, param2=46, minRadius=min_r, maxRadius=max_r
    )

    in_zone_cands = []
    if circles is not None:
        gx = cv2.Sobel(blurred, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(blurred, cv2.CV_32F, 0, 1, ksize=3)
        mag = np.sqrt(gx**2 + gy**2)

        for sc in circles[0]:
            scx, scy, sr = int(sc[0]), int(sc[1]), int(sc[2])
            # Constrain candidates to central reticle zone matching the UI targeting box
            if not (0.20 * sw <= scx <= 0.80 * sw and 0.18 * sh <= scy <= 0.82 * sh):
                continue

            angles = np.linspace(0, 2 * np.pi, 32, endpoint=False)
            pxs = np.clip((scx + sr * np.cos(angles)).astype(int), 0, sw - 1)
            pys = np.clip((scy + sr * np.sin(angles)).astype(int), 0, sh - 1)
            rim_vals = mag[pys, pxs]
            if np.mean(rim_vals) >= 22.0 and np.mean(rim_vals > 18.0) >= 0.35:
                orig_cx = int(scx / scale)
                orig_cy = int(scy / scale)
                orig_r = int(sr / scale)
                dist_center = float(np.hypot(scx - sw/2, scy - sh/2))
                in_zone_cands.append((orig_cx, orig_cy, orig_r, dist_center))

    if not in_zone_cands:
        return image, False, None, "none", "Hold coin in camera view"

    # Pick candidate closest to center
    in_zone_cands.sort(key=lambda c: c[3])
    cx, cy, r, _ = in_zone_cands[0]

    # Horizontal position normalized
    norm_x = float(cx) / float(max(1, w))
    if norm_x < 0.35:
        guidance = "move_right"
        guidance_text = "Move right"
    elif norm_x > 0.65:
        guidance = "move_left"
        guidance_text = "Move left"
    else:
        guidance = "centered"
        guidance_text = "Hold steady"

    # Generous 40% padding so the coin occupies ~72% of crop, perfectly matching training dataset scale
    pad = int(r * 0.40)
    x1, y1 = max(0, cx - r - pad), max(0, cy - r - pad)
    x2, y2 = min(w, cx + r + pad), min(h, cy + r + pad)
    crop = image[y1:y2, x1:x2]
    return crop, True, (x1, y1, x2, y2), guidance, guidance_text


# ============================================================
# OBJECT DETECTION & CLASSIFICATION
# ============================================================

def _predict_detection(
    image_path: str,
) -> Tuple[Optional[str], float, Optional[Tuple[int, int, int, int]], str, str]:

    if _yolo_model is None:
        return None, 0.0, None, "none", "Model unavailable"

    image = _read_image(image_path)

    if image is None:
        return None, 0.0, None, "none", "Invalid image"

    if not _image_is_valid(image):
        return None, 0.0, None, "none", "Invalid image"

    height, width = image.shape[:2]
    image_area = float(width * height)

    # ========================================================
    # CLASSIFICATION MODEL PATH
    # ========================================================
    if _model_task == "classify":
        crop_image, has_coin, coin_bbox, guidance, guidance_text = extract_coin_roi(image)
        if not has_coin:
            return None, 0.0, None, "none", "Hold coin in camera view"

        # If coin is not centered, return spatial guidance
        if guidance != "centered":
            direction_word = "right" if guidance == "move_right" else "left"
            return f"Move coin {direction_word}", 0.50, coin_bbox, guidance, guidance_text

        temp_crop_path = None
        input_source = str(image_path)
        if coin_bbox and (coin_bbox != (0, 0, width, height)):
            temp_crop_path = os.path.join(tempfile.gettempdir(), f"coin_crop_{uuid.uuid4().hex}.jpg")
            cv2.imwrite(temp_crop_path, crop_image)
            input_source = temp_crop_path

        try:
            # Use 224 imgsz matching yolov8n-cls training resolution
            results = _yolo_model.predict(
                source=input_source,
                imgsz=224,
                verbose=False,
            )
        except Exception as exc:
            print("Coin prediction error:", exc)
            return None, 0.0, None, "none", "Prediction error"
        finally:
            if temp_crop_path and os.path.exists(temp_crop_path):
                try:
                    os.remove(temp_crop_path)
                except OSError:
                    pass

        if not results:
            return None, 0.0, None, "none", "Hold coin steady"

        result = results[0]
        probs = getattr(result, "probs", None)
        if probs is None:
            return None, 0.0, None, "none", "Hold coin steady"

        try:
            confidence = float(probs.top1conf)
            class_id = int(probs.top1)
        except Exception:
            return None, 0.0, None, "none", "Hold coin steady"

        names = getattr(result, "names", {})
        raw_name = names.get(class_id, str(class_id)) if isinstance(names, dict) else str(class_id)

        runner_up_name = None
        runner_up_conf = 0.0
        try:
            top5_ids = probs.top5
            top5_confs = probs.top5conf.tolist()
            if len(top5_ids) > 1:
                runner_up_id = int(top5_ids[1])
                runner_up_conf = float(top5_confs[1])
                runner_up_name = names.get(runner_up_id, str(runner_up_id)) if isinstance(names, dict) else str(runner_up_id)
        except Exception:
            pass

        margin = confidence - runner_up_conf
        print(f"Coin model (classification): top prediction = {raw_name} at {confidence:.3f}, "
              f"runner-up = {runner_up_name} at {runner_up_conf:.3f}, margin = {margin:.3f}")

        if str(raw_name).strip().lower().replace(" ", "_") in NO_COIN_CLASS_NAMES:
            return None, confidence, None, "none", "No coin detected"

        if confidence < CLASSIFICATION_CONFIDENCE:
            return None, confidence, None, "centered", "Hold steady"

        if margin < CLASSIFICATION_MARGIN:
            print(f"Coin model (classification): margin too small ({margin:.3f} < {CLASSIFICATION_MARGIN}) - treating as uncertain.")
            return None, confidence, None, "centered", "Hold steady"

        coin_name = normalize_coin_name(raw_name)
        return coin_name, confidence, coin_bbox, "centered", "Hold steady"

    # ========================================================
    # OBJECT DETECTION MODEL PATH
    # ========================================================
    try:
        results = _yolo_model.predict(
            source=str(image_path),
            conf=DETECTION_CONFIDENCE,
            iou=0.45,
            imgsz=640,
            max_det=5,
            verbose=False,
        )
    except Exception as exc:
        print("Coin prediction error:", exc)
        return None, 0.0, None, "none", "Prediction error"

    if not results:
        return None, 0.0, None, "none", "Hold coin in view"

    result = results[0]
    boxes = getattr(result, "boxes", None)
    if boxes is not None:
        if len(boxes) == 0:
            return None, 0.0, None, "none", "Hold coin in view"

        best_candidate = None
        for i in range(len(boxes)):
            try:
                confidence = float(boxes.conf[i].item())
                class_id = int(boxes.cls[i].item())
                xyxy = boxes.xyxy[i].cpu().numpy()
                x1, y1, x2, y2 = map(int, xyxy)
            except Exception:
                continue

            if confidence < DETECTION_CONFIDENCE:
                continue

            x1 = max(0, min(x1, width - 1))
            y1 = max(0, min(y1, height - 1))
            x2 = max(0, min(x2, width - 1))
            y2 = max(0, min(y2, height - 1))

            box_width = max(0, x2 - x1)
            box_height = max(0, y2 - y1)
            if box_width <= 0 or box_height <= 0:
                continue

            box_area = float(box_width * box_height)
            area_ratio = box_area / image_area

            if area_ratio < MIN_BOX_AREA_RATIO or area_ratio > MAX_BOX_AREA_RATIO:
                continue

            names = getattr(result, "names", {})
            raw_name = names.get(class_id, str(class_id)) if isinstance(names, dict) else str(class_id)
            coin_name = normalize_coin_name(raw_name)

            candidate = (confidence, coin_name, (x1, y1, x2, y2), area_ratio)
            if best_candidate is None or confidence > best_candidate[0]:
                best_candidate = candidate

        if best_candidate is None:
            return None, 0.0, None, "none", "Hold coin in view"

        confidence, coin_name, bbox, _ = best_candidate
        if confidence < DETECTION_CONFIDENCE:
            return None, 0.0, None, "none", "Hold coin in view"

        # Guidance calculation
        bx1, by1, bx2, by2 = bbox
        cx = (bx1 + bx2) / 2.0
        norm_x = cx / float(max(1, width))
        if norm_x < 0.35:
            guidance = "move_right"
            guidance_text = "Move right"
            return f"Move coin right", confidence, bbox, guidance, guidance_text
        elif norm_x > 0.65:
            guidance = "move_left"
            guidance_text = "Move left"
            return f"Move coin left", confidence, bbox, guidance, guidance_text
        else:
            guidance = "centered"
            guidance_text = "Hold steady"

        return coin_name, confidence, bbox, guidance, guidance_text

    return None, 0.0, None, "none", "Hold coin in view"


# ============================================================
# PUBLIC PREDICTION FUNCTION
# ============================================================

def predict_coin(image_path: str):
    """
    Main function used by Flask.

    Returns:
        prediction, confidence

    Examples:
        ("₹5", 0.91)
        ("No coin detected", 0.0)
        ("Coin model not trained", 0.0)
    """

    if _yolo_model is None:
        return "Coin model not trained", 0.0

    prediction, confidence, bbox, guidance, guidance_text = _predict_detection(image_path)

    if prediction is None:
        print(f"Coin model: no accepted prediction (best confidence seen: {confidence:.3f})")
        return "No coin detected", float(confidence)

    return prediction, float(confidence)


# ============================================================
# DETAILED FUNCTION
# ============================================================

def predict_coin_detailed(image_path: str) -> dict:
    """
    Detailed output including denomination, confidence, bounding box, and spatial guidance.
    """

    if _yolo_model is None:
        return {
            "success": False,
            "coin": "Coin model not trained",
            "confidence": 0.0,
            "bbox": None,
            "guidance": "none",
            "guidance_text": "Model not trained"
        }

    prediction, confidence, bbox, guidance, guidance_text = _predict_detection(image_path)

    if prediction is None:
        return {
            "success": False,
            "coin": "No coin detected",
            "confidence": round(float(confidence) * 100, 2),
            "bbox": None,
            "guidance": guidance,
            "guidance_text": guidance_text
        }

    is_success = not str(prediction).startswith(("No coin", "Move coin", "Coin model not"))
    return {
        "success": is_success,
        "coin": prediction,
        "confidence": round(float(confidence) * 100, 2),
        "bbox": list(bbox) if bbox else None,
        "guidance": guidance,
        "guidance_text": guidance_text
    }