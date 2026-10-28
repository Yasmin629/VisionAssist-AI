import sys
import os
from pathlib import Path
import json
import io
import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(str(ROOT))

print("=== 1. Testing Note/Currency Detector ===")
from predict import predict_currency_detailed, model_status
print("Currency model status:", model_status())
res_note = predict_currency_detailed("static/images/note.png")
print("Detection on note.png:", res_note)

print("\n=== 2. Testing Coin Detector ===")
from coin_detector import predict_coin, model_status as coin_status
print("Coin model status:", coin_status())

dataset_dir = ROOT / "dataset" / "Indian Coins Image Dataset"
for folder in sorted(dataset_dir.iterdir()):
    if not folder.is_dir():
        continue
    img = next(folder.rglob("*.jpg"))
    pred, conf = predict_coin(str(img))
    print(f"Coin {folder.name} -> {pred} ({conf:.2f})")

# Test rejection on non-coin inputs
black_img = str(ROOT / "uploads" / "test_black.jpg")
pred_blank, conf_blank = predict_coin(black_img)
print(f"Blank image rejection -> {pred_blank} ({conf_blank})")

user_room_path = ROOT / "uploads" / "028b5190-587a-4ab2-8a29-b294a3757b47.jpg"
if user_room_path.exists():
    pred_room, conf_room = predict_coin(str(user_room_path))
    print(f"Room without coin rejection -> {pred_room} ({conf_room})")

    # Test coins held in simulated webcam feed (720p)
    import cv2
    room_img = cv2.imread(str(user_room_path))
    for denom in ["1 Rupee Coin", "2 Rupee Coin", "5 Rupee Coin", "10 Rupee Coin", "20 Rupee Coin"]:
        f_d = dataset_dir / denom
        if f_d.exists():
            c_sample = cv2.imread(str(next(f_d.rglob("*.jpg"))))
            c_sim = cv2.resize(c_sample, (200, 200))
            sim_frame = room_img.copy()
            sim_frame[360 - 100:360 + 100, 640 - 100:640 + 100] = c_sim
            sim_path = ROOT / "uploads" / f"sim_{denom.replace(' ', '_')}.jpg"
            cv2.imwrite(str(sim_path), sim_frame)
            try:
                p_sim, c_sim_conf = predict_coin(str(sim_path))
                print(f"Webcam frame with {denom} -> {p_sim} ({c_sim_conf:.2f})")
            finally:
                if sim_path.exists():
                    sim_path.unlink()

print("\n=== 3. Testing Flask App Routes ===")
from app import app
client = app.test_client()

# Health
h = client.get("/health")
print("/health ->", h.status_code, h.get_json())

# Detect note
with open(ROOT / "static" / "images" / "note.png", "rb") as f:
    r_det = client.post("/detect", data={"image": (io.BytesIO(f.read()), "note.png")}, content_type="multipart/form-data")
print("/detect (Currency Note) ->", r_det.status_code, r_det.get_json())

# Detect all 5 coin denominations via Flask /detect_coin route
for denom in ["1 Rupee Coin", "2 Rupee Coin", "5 Rupee Coin", "10 Rupee Coin", "20 Rupee Coin"]:
    folder = dataset_dir / denom
    if folder.exists():
        img_coin = next(folder.rglob("*.jpg"))
        with open(img_coin, "rb") as f:
            r_coin = client.post("/detect_coin", data={"image": (io.BytesIO(f.read()), "coin.jpg")}, content_type="multipart/form-data")
        res_data = r_coin.get_json()
        print(f"/detect_coin ({denom}) ->", r_coin.status_code, res_data.get("coin"))

# Settings
s = client.get("/settings")
print("/settings ->", s.status_code)

# Language route with Kannada support
l_kn = client.post("/settings/language", data=json.dumps({"language": "kn"}), content_type="application/json")
print("/settings/language ('kn') ->", l_kn.status_code, l_kn.get_json())
s_kn = client.get("/settings").get_json()
print("Language now set to:", s_kn.get("language"))
assert s_kn.get("language") == "kn", "Kannada language setting failed!"

# Voice authentication with Kannada secret color
auth_kn = client.post("/verify_secret_color", data=json.dumps({"color": "ನೇರಳೆ"}), content_type="application/json")
print("/verify_secret_color (Kannada 'ನೇರಳೆ') ->", auth_kn.status_code, auth_kn.get_json())
assert auth_kn.get_json().get("success") is True, "Kannada color authentication failed!"

# Reset language back to English default
client.post("/settings/language", data=json.dumps({"language": "en"}), content_type="application/json")

print("\n=== 4. Testing Fake Currency Detector ===")
from fake_detector import analyze_note, model_status as fake_model_status
print("Fake detection model status:", fake_model_status())

# Test real genuine note
note_img_path = str(ROOT / "static" / "images" / "note.png")
res_fake_real = analyze_note(note_img_path)
print("Real note fake check result:", res_fake_real["final_result"], f"(Score: {res_fake_real['authenticity_score']}%, Features: {len(res_fake_real['features'])})")
assert res_fake_real["final_result"] == "Genuine Currency Detected", f"Expected 'Genuine Currency Detected', got {res_fake_real['final_result']}"

# Test plain synthetic/fake paper
fake_paper = np.ones((400, 800, 3), dtype=np.uint8) * 230
cv2.putText(fake_paper, "RESERVE BANK OF INDIA 500", (50, 200), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (20, 20, 20), 2)
fake_paper_path = str(ROOT / "uploads" / "synth_fake.jpg")
cv2.imwrite(fake_paper_path, fake_paper)
try:
    res_fake_synth = analyze_note(fake_paper_path)
    print("Synthetic fake note check result:", res_fake_synth["final_result"], f"(Score: {res_fake_synth['authenticity_score']}%)")
    assert res_fake_synth["final_result"] == "Potential Fake Currency Detected", f"Expected 'Potential Fake Currency Detected', got {res_fake_synth['final_result']}"
finally:
    if os.path.exists(fake_paper_path):
        os.remove(fake_paper_path)

# Test blurry image quality rejection
blurry_img = cv2.GaussianBlur(cv2.imread(note_img_path), (61, 61), 0)
blurry_path = str(ROOT / "uploads" / "synth_blurry.jpg")
cv2.imwrite(blurry_path, blurry_img)
try:
    res_blurry = analyze_note(blurry_path)
    print("Blurry image check result:", res_blurry["final_result"])
    assert res_blurry["final_result"] == "Unable to Verify", f"Expected 'Unable to Verify', got {res_blurry['final_result']}"
finally:
    if os.path.exists(blurry_path):
        os.remove(blurry_path)

# Test /detect_fake route with real note
with open(note_img_path, "rb") as f:
    r_fake_route = client.post("/detect_fake", data={"image": (io.BytesIO(f.read()), "note.png")}, content_type="multipart/form-data")
fake_route_data = r_fake_route.get_json()
print("/detect_fake (Real Note) ->", r_fake_route.status_code, fake_route_data.get("currency"), "=>", fake_route_data.get("final_result"))
assert fake_route_data.get("final_result") == "Genuine Currency Detected", f"Expected Genuine Currency Detected from /detect_fake, got {fake_route_data.get('final_result')}"

# History
hist = client.get("/history")
hist_data = hist.get_json().get("history", [])
print(f"/history -> {hist.status_code}, total entries: {len(hist_data)}")
print("Latest history entry:", hist_data[0] if hist_data else "None")

print("\n=== 5. Testing PWA & Mobile Assets ===")
r_manifest = client.get("/manifest.json")
print("/manifest.json ->", r_manifest.status_code, r_manifest.get_json().get("short_name"))
assert r_manifest.status_code == 200, "Manifest route failed!"
assert r_manifest.get_json().get("short_name") == "CurrencyAI", "Invalid manifest content!"

r_sw = client.get("/sw.js")
print("/sw.js ->", r_sw.status_code, r_sw.content_type)
assert r_sw.status_code == 200, "Service worker route failed!"
assert "javascript" in r_sw.content_type, "Invalid Service Worker MIME type!"

r_icon192 = client.get("/static/icons/icon-192.png")
r_icon512 = client.get("/static/icons/icon-512.png")
print(f"PWA Icons -> 192px: {r_icon192.status_code}, 512px: {r_icon512.status_code}")
assert r_icon192.status_code == 200 and r_icon512.status_code == 200, "PWA icons missing!"

print("\nALL BACKEND, MODEL, FAKE DETECTION, AND PWA TESTS COMPLETED SUCCESSFULLY!")
