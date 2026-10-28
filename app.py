from currency_detector import COIN_SHAPE_SENTINEL, detect_note
from flask import Flask, render_template, request, jsonify, send_from_directory
from chatbot import chatbot_response
from predict import model_status, predict_currency_detailed
from fake_detector import analyze_note
from coin_detector import predict_coin, predict_coin_detailed, model_status as coin_model_status
import os
import re
import json
import uuid
import tempfile
import traceback
from datetime import datetime
import cv2
import numpy as np


app = Flask(__name__)


# Upload folder
UPLOAD_FOLDER = "uploads"

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER


# Data folder (settings + history persistence)
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")

DEFAULT_SECRET_COLOR = "purple"


def load_settings():
    if not os.path.exists(SETTINGS_FILE):
        default = {
            "secret_color": DEFAULT_SECRET_COLOR,
            "language": "en",
            "theme": "dark",
            "high_contrast": False,
            "voice_rate": 0.9,
            "voice_volume": 1.0,
            "voice_pitch": 1.0,
            "camera_resolution": "720"
        }
        save_settings(default)
        return default
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            data.setdefault("high_contrast", False)
            data.setdefault("voice_rate", 0.9)
            data.setdefault("voice_volume", 1.0)
            data.setdefault("voice_pitch", 1.0)
            data.setdefault("camera_resolution", "720")
            return data
    except Exception:
        return {
            "secret_color": DEFAULT_SECRET_COLOR,
            "language": "en",
            "theme": "dark",
            "high_contrast": False,
            "voice_rate": 0.9,
            "voice_volume": 1.0
        }


def save_settings(data):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_history():
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_history(entries):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)


def add_history(entry_type, label, confidence):
    settings = load_settings()
    history = load_history()
    history.insert(0, {
        "type": entry_type,
        "label": label,
        "confidence": confidence,
        "time": datetime.now().strftime("%d %b %Y, %I:%M %p"),
        "language": settings.get("language", "en")
    })
    history = history[:50]
    save_history(history)
    return history


def extract_value(text):
    match = re.search(r"\d+", text or "")
    return int(match.group()) if match else 0


# Home page
@app.route("/")
def home():
    return render_template("index.html")


@app.route("/health", methods=["GET"])
def health():
    """Expose the active inference engine for the dashboard and deployment checks."""
    return jsonify({"status": "ok", "model": model_status(), "coin_model": coin_model_status()})


@app.route("/sw.js")
def service_worker():
    return send_from_directory("static", "sw.js", mimetype="application/javascript")


@app.route("/manifest.json")
def manifest():
    return send_from_directory("static", "manifest.json", mimetype="application/manifest+json")


# =========================================================
# VOICE SECURE ACCESS AUTHENTICATION
# =========================================================

COLOR_TRANSLATIONS = {
    "purple": {"hi": ["बैंगनी", "बैंगनी रंग"], "kn": ["ನೇರಳೆ", "ನೇರಳೆ ಬಣ್ಣ", "ಪರ್ಪಲ್"]},
    "red": {"hi": ["लाल", "लाल रंग"], "kn": ["ಕೆಂಪು", "ಕೆಂಪು ಬಣ್ಣ", "ರೆಡ್"]},
    "blue": {"hi": ["नीला", "नीला रंग"], "kn": ["ನೀಲಿ", "ನೀಲಿ ಬಣ್ಣ", "ಬ್ಲೂ"]},
    "green": {"hi": ["हरा", "हरा रंग"], "kn": ["ಹಸಿರು", "ಹಸಿರು ಬಣ್ಣ", "ಗ್ರೀನ್"]},
    "yellow": {"hi": ["पीला", "पीला रंग"], "kn": ["ಹಳದಿ", "ಹಳದಿ ಬಣ್ಣ", "ಯೆಲ್ಲೋ"]},
    "orange": {"hi": ["नारंगी", "नारंगी रंग"], "kn": ["ಕಿತ್ತಳೆ", "ಕಿತ್ತಳೆ ಬಣ್ಣ", "ಆರೆಂಜ್"]},
    "pink": {"hi": ["गुलाबी", "गुलाबी रंग"], "kn": ["ಗುಲಾಬಿ", "ಗುಲಾಬಿ ಬಣ್ಣ", "ಪಿಂಕ್"]},
    "black": {"hi": ["काला", "काला रंग"], "kn": ["ಕಪ್ಪು", "ಕಪ್ಪು ಬಣ್ಣ", "ಬ್ಲ್ಯಾಕ್"]},
    "white": {"hi": ["सफेद", "सफेद रंग"], "kn": ["ಬಿಳಿ", "ಬಿಳಿ ಬಣ್ಣ", "ವೈಟ್"]},
    "brown": {"hi": ["भूरा", "भूरा रंग"], "kn": ["ಕಂದು", "ಕಂದು ಬಣ್ಣ", "ಬ್ರೌನ್"]},
}

# Common everyday English synonyms for the same color - e.g. someone might
# naturally say "violet" instead of "purple". Merged into the canonical
# color's accepted forms below.
COLOR_ENGLISH_SYNONYMS = {
    "purple": ["violet", "lavender", "mauve"],
    "red": ["crimson", "maroon", "scarlet"],
    "blue": ["navy", "azure", "cyan"],
    "green": ["olive", "emerald"],
    "yellow": ["gold", "golden"],
    "orange": ["amber"],
    "pink": ["magenta", "rose"],
    "black": ["ebony"],
    "white": ["ivory"],
    "brown": ["tan", "beige", "chestnut"],
}


# Reverse lookup: any Hindi spoken/typed form -> its canonical English
# color name. Built once at startup so a secret color saved in Hindi or
# Hindi script still matches correctly no matter which language the app
# is currently running in.
_COLOR_FORM_TO_CANONICAL = {}
for _canonical, _translations in COLOR_TRANSLATIONS.items():
    _COLOR_FORM_TO_CANONICAL[_canonical] = _canonical
    for _words in _translations.values():
        for _w in _words:
            _COLOR_FORM_TO_CANONICAL[_w.strip()] = _canonical
for _canonical, _synonyms in COLOR_ENGLISH_SYNONYMS.items():
    for _syn in _synonyms:
        _COLOR_FORM_TO_CANONICAL[_syn] = _canonical


def _canonicalize_color(raw_color: str) -> str:
    """Map any English/Hindi color word to its canonical English form.
    If the word isn't a recognized color in any language, return it unchanged
    (so custom secret colors still work)."""
    cleaned = raw_color.strip()
    return _COLOR_FORM_TO_CANONICAL.get(cleaned, _COLOR_FORM_TO_CANONICAL.get(cleaned.lower(), cleaned.lower()))


def _accepted_color_forms(canonical_color: str) -> set:
    """All spoken forms (English + Hindi + common English synonyms) that
    should count as a match for a given canonical (English) secret color."""
    canonical_color = _canonicalize_color(canonical_color)
    forms = {canonical_color.strip().lower()}
    translations = COLOR_TRANSLATIONS.get(canonical_color.strip().lower())
    if translations:
        for words in translations.values():
            forms.update(w.strip() for w in words)
    forms.update(COLOR_ENGLISH_SYNONYMS.get(canonical_color.strip().lower(), []))
    return forms


@app.route("/verify_secret_color", methods=["POST"])
def verify_secret_color():

    data = request.get_json(force=True, silent=True) or {}
    color = str(data.get("color", "")).strip().lower()

    settings = load_settings()
    expected = str(settings.get("secret_color", DEFAULT_SECRET_COLOR)).strip().lower()

    accepted_forms = _accepted_color_forms(expected)

    if color and any(form in color for form in accepted_forms):
        return jsonify({"success": True})

    return jsonify({"success": False, "message": "Secret color not recognized"})


# =========================================================
# SETTINGS
# =========================================================

@app.route("/settings", methods=["GET"])
def get_settings():
    settings = load_settings()
    return jsonify({
        "language": settings.get("language", "en"),
        "theme": settings.get("theme", "dark"),
        "secret_color": settings.get("secret_color", DEFAULT_SECRET_COLOR),
        "high_contrast": settings.get("high_contrast", False),
        "voice_rate": settings.get("voice_rate", 0.9),
        "voice_volume": settings.get("voice_volume", 1.0),
        "voice_pitch": settings.get("voice_pitch", 1.0),
        "camera_resolution": settings.get("camera_resolution", "720")
    })


@app.route("/settings/language", methods=["POST"])
def set_language():
    data = request.get_json(force=True, silent=True) or {}
    language = data.get("language", "en")
    if language not in ("en", "hi", "kn"):
        language = "en"
    settings = load_settings()
    settings["language"] = language
    save_settings(settings)
    return jsonify({"success": True})


@app.route("/settings/theme", methods=["POST"])
def set_theme():
    data = request.get_json(force=True, silent=True) or {}
    settings = load_settings()
    settings["theme"] = data.get("theme", "dark")
    save_settings(settings)
    return jsonify({"success": True})


@app.route("/settings/voice", methods=["POST"])
def set_voice():
    data = request.get_json(force=True, silent=True) or {}
    settings = load_settings()
    settings["voice_rate"] = float(data.get("voice_rate", settings.get("voice_rate", 0.9)))
    settings["voice_volume"] = float(data.get("voice_volume", settings.get("voice_volume", 1.0)))
    settings["voice_pitch"] = float(data.get("voice_pitch", settings.get("voice_pitch", 1.0)))
    save_settings(settings)
    return jsonify({"success": True})


@app.route("/settings/camera_resolution", methods=["POST"])
def set_camera_resolution():
    data = request.get_json(force=True, silent=True) or {}
    settings = load_settings()
    settings["camera_resolution"] = str(data.get("camera_resolution", "720"))
    save_settings(settings)
    return jsonify({"success": True})


@app.route("/settings/reset", methods=["POST"])
def reset_settings():
    default = {
        "secret_color": DEFAULT_SECRET_COLOR,
        "language": "en",
        "theme": "dark",
        "high_contrast": False,
        "voice_rate": 0.9,
        "voice_volume": 1.0,
        "voice_pitch": 1.0,
        "camera_resolution": "720"
    }
    save_settings(default)
    return jsonify({"success": True, "settings": default})


@app.route("/settings/contrast", methods=["POST"])
def set_contrast():
    data = request.get_json(force=True, silent=True) or {}
    settings = load_settings()
    settings["high_contrast"] = bool(data.get("high_contrast", False))
    save_settings(settings)
    return jsonify({"success": True})


@app.route("/settings/secret_color", methods=["POST"])
def change_secret_color():

    data = request.get_json(force=True, silent=True) or {}
    new_color_raw = str(data.get("secret_color", "")).strip()

    if not new_color_raw or not new_color_raw.replace(" ", "").isalpha():
        return jsonify({"success": False, "message": "Secret color must contain letters only"})

    # Whether typed in English or Hindi, store the canonical
    # English form so the color keeps working no matter which language
    # the app is later switched to.
    new_color = _canonicalize_color(new_color_raw)

    settings = load_settings()
    settings["secret_color"] = new_color
    save_settings(settings)

    return jsonify({"success": True})


# =========================================================
# HISTORY
# =========================================================

@app.route("/history", methods=["GET"])
def get_history():
    return jsonify({"history": load_history()})


@app.route("/history/clear", methods=["POST"])
def clear_history():
    save_history([])
    return jsonify({"success": True})


@app.route("/history/counting_session", methods=["POST"])
def log_counting_session():
    data = request.get_json(force=True, silent=True) or {}
    breakdown = data.get("breakdown", {})
    total = data.get("total", 0)
    label = ", ".join(f"{name} x{count}" for name, count in breakdown.items())
    add_history("counting_session", f"{label} (Total Rs {total})", 100.0)
    return jsonify({"success": True})


# =========================================================
# SINGLE NOTE DETECTION (camera capture)
# =========================================================

@app.route('/detect', methods=['POST'])
def detect():

    path = None
    inference_path = None
    try:
        if 'image' not in request.files:
            return jsonify({
                "success": False,
                "message": "No image uploaded"
            })

        file = request.files['image']

        filename = str(uuid.uuid4()) + ".jpg"
        path = os.path.join(app.config["UPLOAD_FOLDER"], filename)

        file.save(path)

        # Predict directly on the full frame for maximum YOLO accuracy
        result = predict_currency_detailed(path)
        prediction, confidence = result["currency"], result["confidence"]
        guidance = result.get("guidance", "none")
        guidance_text = result.get("guidance_text", "")

        # Fallback to enhanced contour crop if full frame saw no object
        if prediction.startswith("No currency detected"):
            try:
                inference_path = detect_note(path)
                if inference_path and inference_path != path and os.path.exists(inference_path):
                    fallback_res = predict_currency_detailed(inference_path)
                    if not fallback_res["currency"].startswith("No currency detected"):
                        result = fallback_res
                        prediction, confidence = result["currency"], result["confidence"]
                        guidance = result.get("guidance", guidance)
                        guidance_text = result.get("guidance_text", guidance_text)
            except Exception:
                pass

        if not prediction.startswith(("Unable", "Please", "No currency detected", "Move note", "Hold steady")):
            add_history("scan", prediction, round(confidence * 100, 2))

        return jsonify({
            "success": True,
            "currency": prediction,
            "confidence": round(confidence * 100, 2),
            "bbox": result.get("bbox"),
            "guidance": guidance,
            "guidance_text": guidance_text,
            "inference_ms": result.get("inference_ms", 0),
            "engine": model_status()["engine"]
        })

    except Exception as e:
        traceback.print_exc()

        return jsonify({
            "success": False,
            "message": str(e)
        }), 500
    finally:
        # Camera captures are only needed for this one prediction - don't
        # keep them on disk afterward.
        for temp_path in {path, inference_path}:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass



# =========================================================
# FAKE NOTE DETECTION (indicative heuristic check)
# =========================================================

@app.route("/detect_fake", methods=["POST"])
def detect_fake():

    path = None
    inference_path = None
    temp_crop = None
    try:
        if 'image' not in request.files:
            return jsonify({"success": False, "message": "No image uploaded"})

        file = request.files['image']

        filename = str(uuid.uuid4()) + ".jpg"
        path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        file.save(path)

        # 1. Try isolating the physical banknote using contour detection first
        inference_path = detect_note(path)
        if inference_path == COIN_SHAPE_SENTINEL:
            return jsonify({
                "success": False,
                "message": "Unable to detect a note - this looks like a coin. Please use the Coin Scanner instead."
            })

        # 2. Predict denomination on the isolated note crop
        currency_result = predict_currency_detailed(inference_path)
        raw_currency = currency_result.get("currency", "")
        detected_curr = currency_result.get("detected_currency")

        denomination = None
        if detected_curr and not detected_curr.startswith(("Unable", "No currency", "Hold steady", "Move note", "Coin")):
            denomination = detected_curr
        elif raw_currency and not raw_currency.startswith(("Unable", "No currency", "Hold steady", "Move note", "Coin")):
            denomination = raw_currency

        # 3. Fallback: if not recognized on contour crop, try full frame with YOLO bbox cropping
        if denomination is None and inference_path != path:
            raw_res = predict_currency_detailed(path)
            raw_curr = raw_res.get("detected_currency") or raw_res.get("currency", "")
            if not raw_curr.startswith(("Unable", "No currency", "Hold steady", "Move note", "Coin")):
                denomination = raw_curr
                currency_result = raw_res
                inference_path = path

        # If YOLO located a box on the chosen frame and it's full frame, crop to the box
        if inference_path == path and currency_result.get("bbox"):
            try:
                x1, y1, x2, y2 = currency_result["bbox"]
                raw_img = cv2.imread(path)
                if raw_img is not None:
                    h_img, w_img = raw_img.shape[:2]
                    pad_x = int(0.04 * (x2 - x1))
                    pad_y = int(0.04 * (y2 - y1))
                    cx1 = max(0, x1 - pad_x)
                    cy1 = max(0, y1 - pad_y)
                    cx2 = min(w_img, x2 + pad_x)
                    cy2 = min(h_img, y2 + pad_y)
                    if (cx2 - cx1) > 60 and (cy2 - cy1) > 60:
                        crop = raw_img[cy1:cy2, cx1:cx2]
                        temp_crop = os.path.join(tempfile.gettempdir(), f"yolo_crop_{uuid.uuid4().hex}.jpg")
                        cv2.imwrite(temp_crop, crop)
                        inference_path = temp_crop
            except Exception as crop_err:
                print(f"YOLO bbox crop notice: {crop_err}")

        if denomination is None:
            # Truly no note detected
            return jsonify({
                "success": True,
                "currency": "No Note Detected",
                "currency_confidence": None,
                "authenticity_score": None,
                "final_result": "No Note Detected",
                "confidence": None,
                "recommendation": "Hold a currency note steady in the camera view to check its authenticity.",
                "features": [],
                "verdict": "No Note Detected",
                "model_available": False,
            })

        result = analyze_note(inference_path, denomination=denomination)
        final_result = result.get("final_result", "Unable to Verify")

        history_note = f"{final_result} - {denomination}"
        add_history("fake_check", history_note, result.get("confidence") or 0)

        return jsonify({
            "success": True,
            "currency": denomination,
            "currency_confidence": round(currency_result.get("confidence", 0) * 100, 2),
            "authenticity_score": result.get("authenticity_score"),
            "final_result": final_result,
            "confidence": result.get("confidence"),
            "recommendation": result.get("recommendation"),
            "features": result.get("features", []),
            "verdict": result.get("verdict", final_result),
            "model_available": result.get("model_available", False),
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        for temp_path in {path, inference_path, temp_crop}:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass


@app.route("/detect_coin", methods=["POST"])
def detect_coin():

    path = None
    try:
        if 'image' not in request.files:
            return jsonify({"success": False, "message": "No image uploaded"})

        file = request.files['image']
        filename = str(uuid.uuid4()) + ".jpg"
        path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        file.save(path)

        result = predict_coin_detailed(path)
        coin_label = result["coin"]
        coin_confidence = result["confidence"]
        guidance = result.get("guidance", "none")
        guidance_text = result.get("guidance_text", "")

        print("================================")
        print("Coin :", coin_label)
        print("Confidence:", coin_confidence)
        print("Guidance:", guidance)
        print("================================")

        # Only a genuinely detected (not rejected/unavailable/positioning) coin gets logged
        if not coin_label.startswith(("Coin not detected", "No coin detected", "Coin model not trained", "Coin detection model not found", "Move coin", "Hold steady")):
            add_history("coin", coin_label, round(coin_confidence, 2))

        return jsonify({
            "success": True,
            "coin": coin_label,
            "confidence": round(coin_confidence, 2),
            "bbox": result.get("bbox"),
            "guidance": guidance,
            "guidance_text": guidance_text,
            "engine": coin_model_status()["engine"]
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


# =========================================================
# CHATBOT
# =========================================================

@app.route("/chat", methods=["POST"])
def chat():

    data = request.get_json(force=True, silent=True) or {}

    question = data.get("message", "")
    lang = data.get("lang", "en")

    answer = chatbot_response(question, lang)

    return jsonify({
        "reply": answer
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() in ("true", "1")
    app.run(host="0.0.0.0", port=port, debug=debug)