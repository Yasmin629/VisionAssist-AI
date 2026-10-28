"""Currency inference: YOLO11 is the ONLY detection engine.

There is deliberately no fallback classifier. If model/best.pt doesn't
exist, predict_currency() returns an honest "YOLO model not found" message
instead of guessing - see training/README.md for how to produce best.pt.

Low confidence results deliberately return an uncertain answer instead of
a potentially wrong denomination.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from currency_detector import COIN_SHAPE_SENTINEL, is_blurry

ROOT = Path(__file__).resolve().parent
YOLO_MODEL_PATH = ROOT / "model" / "best.pt"
MARGIN_THRESHOLD = 0.08          # top-1 vs top-2 gap; lowered from 0.15 to avoid false ties
YOLO_MIN_CONFIDENCE = 0.45       # tuned for reliable real-world detection in room lighting
YOLO_IOU_THRESHOLD = 0.50        # NMS overlap threshold
MIN_ASPECT_RATIO = 1.05          # allows angled and folded notes
MAX_ASPECT_RATIO = 4.2
MODEL_NOT_FOUND_MESSAGE = "YOLO model not found. Please train the model."

_yolo_model: Any = None
_engine = "unavailable"


def _load_model() -> None:
    global _yolo_model, _engine
    if not YOLO_MODEL_PATH.exists():
        return
    try:
        from ultralytics import YOLO
        _yolo_model = YOLO(str(YOLO_MODEL_PATH))
        _engine = "yolo11"
    except Exception as error:
        print(f"model/coin_best.pt found but YOLO could not be loaded: {error}")


_load_model()


def model_status() -> dict[str, str]:
    if _engine == "yolo11":
        return {"engine": "yolo11", "label": "YOLO11 detector ready"}
    return {"engine": "unavailable", "label": MODEL_NOT_FOUND_MESSAGE}


def _unknown(confidence: float = 0.0) -> dict:
    return {
        "currency": "No currency detected.",
        "confidence": confidence,
        "bbox": None,
        "inference_ms": 0,
        "guidance": "none",
        "guidance_text": "Hold note in front of camera"
    }


def _blurry() -> dict:
    return {
        "currency": "Unable to recognize the currency. Please hold the note steady and try again.",
        "confidence": 0.0,
        "bbox": None,
        "inference_ms": 0,
        "guidance": "none",
        "guidance_text": "Hold note steady"
    }


def _too_close_to_call(best_conf: float, second_conf: float) -> bool:
    return (best_conf - second_conf) < MARGIN_THRESHOLD


def _display_name_for_yolo_class(name: str) -> str:
    cleaned = name.strip()
    if cleaned.isdigit():
        return f"Rs {cleaned}"
    return cleaned


def predict_currency_detailed(image_path: str) -> dict:
    """Full result: currency, confidence, bounding box, spatial guidance, and inference time."""
    if image_path == COIN_SHAPE_SENTINEL:
        return {
            "currency": "Unable to detect a note - this looks like a coin. Please use the Coin Scanner instead.",
            "confidence": 0.0,
            "bbox": None,
            "inference_ms": 0,
            "guidance": "none",
            "guidance_text": "Coin detected - use Coin Scanner"
        }

    if _engine != "yolo11":
        return {
            "currency": MODEL_NOT_FOUND_MESSAGE,
            "confidence": 0.0,
            "bbox": None,
            "inference_ms": 0,
            "guidance": "none",
            "guidance_text": "Model not found"
        }

    if is_blurry(image_path, threshold=40.0):
        result = _blurry()
        return result

    start = time.perf_counter()
    # Query with a low confidence gate (0.22) so we can locate the note and guide positioning
    results = _yolo_model.predict(image_path, conf=0.22, iou=YOLO_IOU_THRESHOLD, verbose=False)
    inference_ms = round((time.perf_counter() - start) * 1000)

    if not results or results[0].boxes is None or len(results[0].boxes) == 0:
        result = _unknown()
        result["inference_ms"] = inference_ms
        return result

    boxes = results[0].boxes
    names = results[0].names
    confidences = boxes.conf.tolist()
    class_ids = boxes.cls.tolist()
    xyxy = boxes.xyxy.tolist()

    ranked = sorted(zip(confidences, class_ids, xyxy), key=lambda triple: triple[0], reverse=True)
    best_conf, best_class_id, best_box = ranked[0]

    orig_shape = getattr(results[0], "orig_shape", (480, 640))
    orig_h, orig_w = orig_shape[0], orig_shape[1]
    x1, y1, x2, y2 = best_box
    cx = (x1 + x2) / 2.0
    norm_x = cx / float(max(1, orig_w))
    box_w, box_h = max(1.0, x2 - x1), max(1.0, y2 - y1)
    aspect_ratio = max(box_w, box_h) / min(box_w, box_h)

    # Determine spatial guidance based on horizontal center
    if norm_x < 0.35:
        guidance = "move_right"
        guidance_text = "Move right"
    elif norm_x > 0.65:
        guidance = "move_left"
        guidance_text = "Move left"
    else:
        guidance = "centered"
        guidance_text = "Hold steady"

    label = names[int(best_class_id)]
    detected_curr = _display_name_for_yolo_class(str(label))

    if aspect_ratio < MIN_ASPECT_RATIO or aspect_ratio > MAX_ASPECT_RATIO:
        result = _unknown(best_conf)
        result["inference_ms"] = inference_ms
        result["guidance"] = guidance
        result["guidance_text"] = "Hold note flatter"
        result["detected_currency"] = detected_curr
        return result

    # When not centered, guide the user to center the note
    if guidance != "centered":
        direction_word = "right" if guidance == "move_right" else "left"
        return {
            "currency": f"Move note {direction_word}",
            "detected_currency": detected_curr,
            "confidence": float(best_conf),
            "bbox": [round(v) for v in best_box],
            "inference_ms": inference_ms,
            "guidance": guidance,
            "guidance_text": guidance_text
        }

    # Held centered: check if confidence is sufficient for final announcement
    if best_conf < YOLO_MIN_CONFIDENCE:
        return {
            "currency": "Hold steady",
            "detected_currency": detected_curr,
            "confidence": float(best_conf),
            "bbox": [round(v) for v in best_box],
            "inference_ms": inference_ms,
            "guidance": "centered",
            "guidance_text": "Hold steady"
        }

    if len(ranked) > 1:
        second_conf, second_class_id, _ = ranked[1]
        if second_class_id != best_class_id and _too_close_to_call(best_conf, second_conf):
            return {
                "currency": "Unable to confirm between two similar denominations. Please recapture closer and flatter.",
                "detected_currency": detected_curr,
                "confidence": best_conf,
                "bbox": [round(v) for v in best_box],
                "inference_ms": inference_ms,
                "guidance": "centered",
                "guidance_text": "Hold steady"
            }

    return {
        "currency": detected_curr,
        "detected_currency": detected_curr,
        "confidence": float(best_conf),
        "bbox": [round(v) for v in best_box],
        "inference_ms": inference_ms,
        "guidance": "centered",
        "guidance_text": "Hold steady"
    }


def predict_currency(image_path: str) -> tuple[str, float]:
    """Backward-compatible simple form (currency, confidence) for callers
    that don't need the bounding box or timing."""
    result = predict_currency_detailed(image_path)
    return result["currency"], result["confidence"]