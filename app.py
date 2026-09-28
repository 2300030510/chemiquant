"""
ChemiQuant backend — Flask API for the Chemiluminometer Concentration Analyzer.
No authentication/login by design (per project requirement).

Run:
    pip install -r requirements.txt
    python app.py
Then open http://localhost:5000
"""
import os
import io
import json
import uuid
import base64
import smtplib
from email.mime.text import MIMEText
from datetime import datetime

import numpy as np
import cv2
from PIL import Image
from flask import Flask, request, jsonify, render_template, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from sklearn.linear_model import LinearRegression
from sklearn.metrics import r2_score
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))  # reads .env next to this file, if present
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{os.path.join(BASE_DIR, 'chemiquant.db')}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100 MB uploads
db = SQLAlchemy(app)

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class CalibrationCurve(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    analyte = db.Column(db.String(64), unique=True, nullable=False)
    units = db.Column(db.String(32), nullable=False, default="µM")
    points_json = db.Column(db.Text, nullable=False)  # [[conc, intensity], ...]
    slope = db.Column(db.Float)
    intercept = db.Column(db.Float)
    r2 = db.Column(db.Float)
    is_preset = db.Column(db.Boolean, default=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "analyte": self.analyte, "units": self.units,
            "points": json.loads(self.points_json),
            "slope": self.slope, "intercept": self.intercept, "r2": self.r2,
            "is_preset": self.is_preset,
        }


class Result(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    analyte = db.Column(db.String(64))
    units = db.Column(db.String(32))
    intensity = db.Column(db.Float)
    concentration = db.Column(db.Float)
    slope = db.Column(db.Float)
    intercept = db.Column(db.Float)
    r2 = db.Column(db.Float)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "analyte": self.analyte, "units": self.units,
            "intensity": self.intensity, "concentration": self.concentration,
            "slope": self.slope, "intercept": self.intercept, "r2": self.r2,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
        }


# ---------------------------------------------------------------------------
# Seed calibration library (from the project's cited literature / typical
# luminol-type response curves). Re-fit here rather than hard-coded so the
# regression path is exercised the same way custom curves are.
# ---------------------------------------------------------------------------
LIBRARY_SEED = {
    "luminol_h2o2":  {"units": "µM",     "points": [[0, 4], [5, 22], [10, 41], [20, 78], [40, 151], [80, 244]]},
    "pesticide_cl":  {"units": "ppb",    "points": [[0, 6], [2, 19], [5, 38], [10, 66], [20, 120]]},
    "oil_rancidity": {"units": "meq/kg", "points": [[0, 10], [1, 28], [2, 49], [4, 88], [8, 160]]},
    "alp_upad":      {"units": "U/L",    "points": [[0, 3], [10, 30], [25, 65], [50, 118], [100, 220]]},
}


def fit_linear(points):
    """points: list of [concentration, intensity]. Fit concentration = m*I + b."""
    pts = np.array(points, dtype=float)
    X = pts[:, 1].reshape(-1, 1)   # intensity -> feature
    y = pts[:, 0]                  # concentration -> target
    model = LinearRegression().fit(X, y)
    pred = model.predict(X)
    r2 = r2_score(y, pred) if len(y) > 1 else 1.0
    return float(model.coef_[0]), float(model.intercept_), float(r2)


def seed_library():
    for key, cfg in LIBRARY_SEED.items():
        existing = CalibrationCurve.query.filter_by(analyte=key).first()
        if existing:
            continue
        m, b, r2 = fit_linear(cfg["points"])
        db.session.add(CalibrationCurve(
            analyte=key, units=cfg["units"], points_json=json.dumps(cfg["points"]),
            slope=m, intercept=b, r2=r2, is_preset=True,
        ))
    db.session.commit()


# ---------------------------------------------------------------------------
# Image / frame analysis
# ---------------------------------------------------------------------------
def analyze_rgb_array(arr):
    """arr: HxWx3 uint8 RGB. Returns (mean_intensity, peak, hotspot_pct)."""
    gray = np.dot(arr[..., :3].astype(float), [0.299, 0.587, 0.114])
    mean_intensity = float(gray.mean())
    peak = float(gray.max())
    hotspot_pct = float((gray > 180).sum()) / gray.size * 100
    return mean_intensity, peak, hotspot_pct


def array_to_data_url(arr, max_w=520):
    img = Image.fromarray(arr.astype("uint8"), "RGB")
    if img.width > max_w:
        h = int(img.height * (max_w / img.width))
        img = img.resize((max_w, h))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=82)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/jpeg;base64,{b64}"


def upload_path(upload_id):
    for fn in os.listdir(UPLOAD_DIR):
        if fn.startswith(upload_id):
            return os.path.join(UPLOAD_DIR, fn)
    return None


# ---------------------------------------------------------------------------
# Routes — pages
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# Routes — API: upload + frame extraction (image / video / gif)
# ---------------------------------------------------------------------------
@app.route("/api/upload", methods=["POST"])
def api_upload():
    f = request.files.get("file")
    if not f:
        return jsonify(error="no file provided"), 400

    ext = os.path.splitext(f.filename)[1].lower() or ".bin"
    upload_id = uuid.uuid4().hex
    saved_path = os.path.join(UPLOAD_DIR, upload_id + ext)
    f.save(saved_path)

    kind = "video"
    meta = {}
    if ext in (".png", ".jpg", ".jpeg", ".bmp", ".webp"):
        kind = "image"
    elif ext == ".gif":
        kind = "gif"
        img = Image.open(saved_path)
        meta["frame_count"] = getattr(img, "n_frames", 1)
    else:
        cap = cv2.VideoCapture(saved_path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        cap.release()
        meta["fps"] = fps
        meta["frame_count"] = frame_count
        meta["duration"] = (frame_count / fps) if fps else 0

    return jsonify(upload_id=upload_id, kind=kind, **meta)


@app.route("/api/frame/<upload_id>", methods=["GET"])
def api_frame(upload_id):
    """Extract + analyze one frame. Query params: t=<seconds> (video) or
    frame=<index> (gif). No params -> whole still image."""
    path = upload_path(upload_id)
    if not path:
        return jsonify(error="upload not found"), 404
    ext = os.path.splitext(path)[1].lower()

    if ext in (".png", ".jpg", ".jpeg", ".bmp", ".webp"):
        arr = np.array(Image.open(path).convert("RGB"))
    elif ext == ".gif":
        idx = int(request.args.get("frame", 0))
        img = Image.open(path)
        img.seek(min(idx, getattr(img, "n_frames", 1) - 1))
        arr = np.array(img.convert("RGB"))
    else:
        t = float(request.args.get("t", 0))
        cap = cv2.VideoCapture(path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
        ok, frame = cap.read()
        cap.release()
        if not ok:
            return jsonify(error="could not read frame at that time"), 400
        arr = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    mean_i, peak, hotspot = analyze_rgb_array(arr)
    return jsonify(
        preview=array_to_data_url(arr),
        mean_intensity=mean_i, peak=peak, hotspot_pct=hotspot,
    )


# ---------------------------------------------------------------------------
# Routes — API: calibration library + fitting
# ---------------------------------------------------------------------------
@app.route("/api/library", methods=["GET"])
def api_library():
    curves = CalibrationCurve.query.all()
    return jsonify([c.to_dict() for c in curves])


@app.route("/api/calibration", methods=["POST"])
def api_save_calibration():
    body = request.get_json(force=True)
    analyte = (body.get("analyte") or "custom").strip() or "custom"
    units = body.get("units") or "µM"
    points = body.get("points") or []
    points = [[float(c), float(i)] for c, i in points if c is not None and i is not None]
    if len(points) < 2:
        return jsonify(error="need at least 2 calibration standards"), 400

    m, b, r2 = fit_linear(points)
    curve = CalibrationCurve.query.filter_by(analyte=analyte).first()
    if curve is None:
        curve = CalibrationCurve(analyte=analyte)
        db.session.add(curve)
    curve.units = units
    curve.points_json = json.dumps(points)
    curve.slope, curve.intercept, curve.r2 = m, b, r2
    db.session.commit()
    return jsonify(curve.to_dict())


# ---------------------------------------------------------------------------
# Routes — API: predict + history
# ---------------------------------------------------------------------------
@app.route("/api/predict", methods=["POST"])
def api_predict():
    body = request.get_json(force=True)
    analyte = body.get("analyte")
    intensity = body.get("intensity")
    if analyte is None or intensity is None:
        return jsonify(error="analyte and intensity are required"), 400

    curve = CalibrationCurve.query.filter_by(analyte=analyte).first()
    if not curve or curve.slope is None:
        return jsonify(error="no calibration curve found for this analyte"), 404

    concentration = curve.slope * float(intensity) + curve.intercept
    result = Result(
        analyte=analyte, units=curve.units, intensity=intensity,
        concentration=concentration, slope=curve.slope,
        intercept=curve.intercept, r2=curve.r2,
    )
    db.session.add(result)
    db.session.commit()
    return jsonify(result.to_dict())


@app.route("/api/results", methods=["GET"])
def api_results():
    rows = Result.query.order_by(Result.created_at.desc()).limit(20).all()
    return jsonify([r.to_dict() for r in rows])


# ---------------------------------------------------------------------------
# Routes — API: email (real SMTP send)
# ---------------------------------------------------------------------------
@app.route("/api/email/status", methods=["GET"])
def api_email_status():
    """Diagnostic — check this in the browser before trying to send.
    Never returns the actual password, just whether each var is present."""
    keys = ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "FROM_EMAIL"]
    present = {k: bool(os.environ.get(k)) for k in keys}
    return jsonify(configured=all(present.values()), present=present)


@app.route("/api/email", methods=["POST"])
def api_email():
    body = request.get_json(force=True)
    to_addr = body.get("to")
    result_id = body.get("result_id")
    if not to_addr or not result_id:
        return jsonify(error="'to' and 'result_id' are required"), 400

    result = Result.query.get(result_id)
    if not result:
        return jsonify(error="result not found"), 404

    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", 587))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASS")
    from_addr = os.environ.get("FROM_EMAIL", user)

    if not all([host, user, password]):
        return jsonify(
            error="Email is not configured on the server. Set SMTP_HOST, "
                  "SMTP_PORT, SMTP_USER, SMTP_PASS and FROM_EMAIL as "
                  "environment variables before sending."
        ), 503

    body_text = (
        f"ChemiQuant result — {result.created_at.strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"
        f"Analyte: {result.analyte}\n"
        f"Measured intensity: {result.intensity:.2f}\n"
        f"Calibration: c = {result.slope:.4f} * I + {result.intercept:.3f}  "
        f"(R\u00b2 = {result.r2:.4f})\n"
        f"Estimated concentration: {result.concentration:.3f} {result.units}\n"
    )
    msg = MIMEText(body_text)
    msg["Subject"] = f"ChemiQuant result — {result.analyte} ({result.created_at:%Y-%m-%d %H:%M})"
    msg["From"] = from_addr
    msg["To"] = to_addr

    try:
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.starttls()
            server.login(user, password)
            server.sendmail(from_addr, [to_addr], msg.as_string())
    except Exception as exc:  # surfaced to the UI, not swallowed
        return jsonify(error=f"send failed: {exc}"), 502

    return jsonify(sent=True, to=to_addr)


with app.app_context():
    db.create_all()
    seed_library()

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5001)),
        debug=False
    )