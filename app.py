import os
import math
import logging
import csv
import json
from io import TextIOWrapper
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timedelta, time as dtime

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify, Response
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import func, text, select

# ✅ XLSX export support
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment
from openpyxl.utils import get_column_letter

# -----------------------------
# Timezone (Windows-safe)
# -----------------------------
APP_TZ = None
UTC_TZ = None
try:
    from zoneinfo import ZoneInfo
    try:
        APP_TZ = ZoneInfo("America/Chicago")
    except Exception:
        APP_TZ = None
    try:
        UTC_TZ = ZoneInfo("UTC")
    except Exception:
        UTC_TZ = None
except Exception:
    APP_TZ = None
    UTC_TZ = None

# -----------------------------
# App + Config
# -----------------------------
app = Flask(__name__)

# Basic INFO logging (Render captures these)
logging.basicConfig(level=logging.INFO)

app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev_secret_change_me")

# ✅ Mobile ingest auth token (set on Render)
app.config["MOBILE_DEVICE_TOKEN"] = (os.environ.get("MOBILE_DEVICE_TOKEN") or "").strip()

# ✅ Dev endpoint gate
ENABLE_DEV_EXPORTS = (os.environ.get("ENABLE_DEV_EXPORTS") or "").strip() == "1"

# ------------------------------------------------------------
# Database config
#   - Prefer DATABASE_URL if present (Render)
#   - Still supports USE_RENDER_DB=1 if you like
#   - Local fallback: sqlite relative path
# ------------------------------------------------------------
def _normalize_db_url(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return raw

    # Normalize Render postgres URL
    if raw.startswith("postgres://"):
        raw = raw.replace("postgres://", "postgresql://", 1)

    # FORCE psycopg v3 (required for Python 3.13)
    if raw.startswith("postgresql://") and not raw.startswith("postgresql+psycopg://"):
        raw = raw.replace("postgresql://", "postgresql+psycopg://", 1)

    return raw


use_render_db = (os.environ.get("USE_RENDER_DB") or "").strip() == "1"
env_db_url = _normalize_db_url(os.environ.get("DATABASE_URL"))

if use_render_db:
    if not env_db_url:
        raise RuntimeError("USE_RENDER_DB=1 but DATABASE_URL is not set")
    db_url = env_db_url
else:
    # Render-safe: if DATABASE_URL exists anyway, use it
    if env_db_url:
        db_url = env_db_url
    else:
        # Local fallback (relative path; works on Windows + Render)
        db_url = "sqlite:///instance/clockin.db"

app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# -----------------------------
# Flask-Migrate (optional)
# -----------------------------
try:
    from flask_migrate import Migrate
    migrate = Migrate(app, db)
except Exception:
    migrate = None

# -----------------------------
# Admin credentials
# -----------------------------
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "dan")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Ccss1234")
ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH") or generate_password_hash(ADMIN_PASSWORD)

# -----------------------------
# Helpers (time)
# -----------------------------
def now_utc() -> datetime:
    # store naive UTC (works reliably with db.DateTime columns)
    return datetime.utcnow()

def now_local() -> datetime:
    if APP_TZ:
        return datetime.now(APP_TZ)
    return datetime.now()

def utc_naive_to_local(dt: datetime) -> datetime:
    """
    Treat incoming dt as UTC if naive; convert to APP_TZ for display.
    """
    if not dt:
        return dt
    try:
        if getattr(dt, "tzinfo", None) is None:
            if UTC_TZ:
                dt = dt.replace(tzinfo=UTC_TZ)
        if APP_TZ and getattr(dt, "tzinfo", None):
            dt = dt.astimezone(APP_TZ)
    except Exception:
        pass
    return dt

# -----------------------------
# Models
# -----------------------------
class Store(db.Model):
    __tablename__ = "stores"
    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(db.String(120), nullable=False)
    qr_token = db.Column(db.String(120), unique=True, nullable=False)

    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    geofence_radius_m = db.Column(db.Integer, nullable=False, default=150)

    created_at = db.Column(db.DateTime, default=lambda: now_utc())

class Employee(db.Model):
    __tablename__ = "employees"
    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(db.String(120), nullable=False)
    pin = db.Column(db.String(20), nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)

    # ✅ Option 2: device binding (Option C = overwrite allowed)
    device_uuid = db.Column(db.String(120), nullable=True)        # last seen/bound device
    device_label = db.Column(db.String(120), nullable=True)       # optional "Pixel 7", etc
    device_last_seen_at = db.Column(db.DateTime, nullable=True)   # UTC naive

    created_at = db.Column(db.DateTime, default=lambda: now_utc())

class Shift(db.Model):
    __tablename__ = "shifts"
    id = db.Column(db.Integer, primary_key=True)

    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    store_id = db.Column(db.Integer, db.ForeignKey("stores.id"), nullable=False)

    clock_in = db.Column(db.DateTime, nullable=False)
    clock_out = db.Column(db.DateTime, nullable=True)

    clock_in_lat = db.Column(db.Float, nullable=True)
    clock_in_lng = db.Column(db.Float, nullable=True)
    clock_out_lat = db.Column(db.Float, nullable=True)
    clock_out_lng = db.Column(db.Float, nullable=True)

    # ✅ Option 2: capture device uuid on punches
    clock_in_device_uuid = db.Column(db.String(120), nullable=True)
    clock_out_device_uuid = db.Column(db.String(120), nullable=True)

    # --- Admin override audit fields (B) ---
    closed_by_admin = db.Column(db.Boolean, nullable=False, default=False)
    admin_closed_by = db.Column(db.String(120), nullable=True)   # username
    admin_closed_at = db.Column(db.DateTime, nullable=True)
    admin_close_reason = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=lambda: now_utc())

    employee = db.relationship("Employee", backref="shifts")
    store = db.relationship("Store", backref="shifts")

# ✅ Location pings (15-min tracking)
class LocationPing(db.Model):
    __tablename__ = "location_pings"
    id = db.Column(db.Integer, primary_key=True)

    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    shift_id = db.Column(db.Integer, db.ForeignKey("shifts.id"), nullable=False)
    store_id = db.Column(db.Integer, db.ForeignKey("stores.id"), nullable=False)

    lat = db.Column(db.Float, nullable=False)
    lng = db.Column(db.Float, nullable=False)

    dist_m = db.Column(db.Float, nullable=False)
    inside_radius = db.Column(db.Boolean, nullable=False, default=True)

    created_at = db.Column(db.DateTime, default=lambda: now_utc(), nullable=False)

    employee = db.relationship("Employee")
    shift = db.relationship("Shift")
    store = db.relationship("Store")

# ✅ NEW: Shift edit audit trail (Option B-safe: new table)
class ShiftEditAudit(db.Model):
    __tablename__ = "shift_edit_audit"
    id = db.Column(db.Integer, primary_key=True)

    shift_id = db.Column(db.Integer, db.ForeignKey("shifts.id"), nullable=True)
    action = db.Column(db.String(40), nullable=False)  # create/edit/force_close
    editor = db.Column(db.String(120), nullable=False)  # admin username
    reason = db.Column(db.Text, nullable=False)

    old_clock_in = db.Column(db.DateTime, nullable=True)
    old_clock_out = db.Column(db.DateTime, nullable=True)
    new_clock_in = db.Column(db.DateTime, nullable=True)
    new_clock_out = db.Column(db.DateTime, nullable=True)

    created_at = db.Column(db.DateTime, default=lambda: now_utc(), nullable=False)

    shift = db.relationship("Shift")

# ✅ Mobile ingest raw event store (Option B-safe: new table)
class MobileEvent(db.Model):
    __tablename__ = "mobile_events"
    id = db.Column(db.Integer, primary_key=True)

    event_type = db.Column(db.String(50), nullable=False, default="unknown")
    device_uuid = db.Column(db.String(120), nullable=True)
    is_moving = db.Column(db.Boolean, nullable=True)

    lat = db.Column(db.Float, nullable=True)
    lng = db.Column(db.Float, nullable=True)
    accuracy = db.Column(db.Float, nullable=True)

    event_at = db.Column(db.DateTime, nullable=True)
    received_at = db.Column(db.DateTime, default=lambda: now_utc(), nullable=False)

    raw_json = db.Column(db.Text, nullable=False)

class MobileIssueReport(db.Model):
    __tablename__ = "mobile_issue_reports"
    id = db.Column(db.Integer, primary_key=True)

    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=True)
    store_id = db.Column(db.Integer, db.ForeignKey("stores.id"), nullable=True)
    shift_id = db.Column(db.Integer, db.ForeignKey("shifts.id"), nullable=True)

    message = db.Column(db.Text, nullable=True)
    payload_json = db.Column(db.Text, nullable=False, default="{}")

    status = db.Column(db.String(30), nullable=False, default="open")  # open / resolved / ignored
    resolved_by = db.Column(db.String(120), nullable=True)
    resolved_at = db.Column(db.DateTime, nullable=True)
    resolve_note = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=lambda: now_utc(), nullable=False)

    employee = db.relationship("Employee")
    store = db.relationship("Store")
    shift = db.relationship("Shift")

# -----------------------------
# Geo Helpers
# -----------------------------
def haversine_m(lat1, lon1, lat2, lon2) -> float:
    """
    Returns distance in meters between two WGS84 lat/lon points.
    """
    R = 6371000.0  # meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)

    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c

def find_store_for_location(
    lat: float,
    lon: float,
    accuracy_m: float | None = None,
    *,
    max_accuracy_m: float = 120.0,
    sanity_gap_m: float = 800.0,
):
    """
    Returns a dict with the best-matching store and distance, or None if not inside any store geofence.
    """
    if accuracy_m is not None and accuracy_m > max_accuracy_m:
        return {
            "ok": False,
            "reason": "accuracy_too_low",
            "message": "GPS accuracy is too low. Step outside and try again.",
            "accuracy_m": float(accuracy_m),
            "max_accuracy_m": float(max_accuracy_m),
        }

    stores = db.session.execute(select(Store)).scalars().all()
    if not stores:
        return {"ok": False, "reason": "no_stores", "message": "No stores are configured."}

    distances = []
    for s in stores:
        d = haversine_m(lat, lon, s.latitude, s.longitude)
        distances.append((d, s))

    distances.sort(key=lambda x: x[0])
    best_d, best_store = distances[0]

    if len(distances) > 1:
        second_d, _ = distances[1]
        if (second_d - best_d) < sanity_gap_m:
            return {
                "ok": False,
                "reason": "ambiguous_nearest",
                "message": "Location is ambiguous between two stores. Move closer to the building and try again.",
                "best_distance_m": float(best_d),
                "second_distance_m": float(second_d),
                "sanity_gap_m": float(sanity_gap_m),
            }

    if best_d <= best_store.geofence_radius_m:
        return {
            "ok": True,
            "store": best_store,
            "distance_m": float(best_d),
        }

    return {
        "ok": False,
        "reason": "outside_geofence",
        "message": "You are not within a valid store location.",
        "nearest_store_id": int(best_store.id),
        "nearest_store_name": best_store.name,
        "nearest_distance_m": float(best_d),
        "required_radius_m": float(best_store.geofence_radius_m),
    }

# -----------------------------
# Helpers (general)
# -----------------------------
def fmt_dt(dt: datetime | None) -> str:
    if not dt:
        return ""
    dt_local = utc_naive_to_local(dt)
    return dt_local.strftime("%Y-%m-%d %I:%M %p")

def parse_local_datetime(val: str) -> datetime | None:
    """
    Accepts 'YYYY-MM-DDTHH:MM' (HTML datetime-local) OR 'YYYY-MM-DD HH:MM'
    Input is interpreted as America/Chicago (APP_TZ) and converted to UTC-naive for storage.
    """
    if not val:
        return None
    s = val.strip()
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M"):
        try:
            naive = datetime.strptime(s, fmt)  # naive local wall time
            if APP_TZ and UTC_TZ:
                local_dt = naive.replace(tzinfo=APP_TZ)
                utc_dt = local_dt.astimezone(UTC_TZ)
                return utc_dt.replace(tzinfo=None)  # store naive UTC
            return naive
        except ValueError:
            continue
    return None

def local_range_to_utc_naive(start_local: datetime, end_local: datetime) -> tuple[datetime, datetime]:
    """
    Converts tz-aware local bounds (America/Chicago) to UTC-naive for DB filtering.
    """
    if APP_TZ and UTC_TZ and getattr(start_local, "tzinfo", None) and getattr(end_local, "tzinfo", None):
        s_utc = start_local.astimezone(UTC_TZ).replace(tzinfo=None)
        e_utc = end_local.astimezone(UTC_TZ).replace(tzinfo=None)
        return s_utc, e_utc
    return start_local.replace(tzinfo=None), end_local.replace(tzinfo=None)

# ✅ Step 1: minute-accurate shift minutes (NO quarter-hour rounding)
def shift_minutes(shift: "Shift") -> int:
    if not shift.clock_in or not shift.clock_out:
        return 0
    seconds = (shift.clock_out - shift.clock_in).total_seconds()
    if seconds <= 0:
        return 0
    return int(seconds // 60)  # whole minutes

def minutes_to_human(minutes: int) -> str:
    if minutes <= 0:
        return "0 min"
    hours = minutes // 60
    mins = minutes % 60
    if hours > 0 and mins > 0:
        return f"{hours} hr {mins} min"
    elif hours > 0:
        return f"{hours} hr"
    else:
        return f"{mins} min"

def minutes_to_short(minutes: int) -> str:
    if minutes <= 0:
        return "0h 00m"
    h = minutes // 60
    m = minutes % 60
    return f"{h}h {m:02d}m"

def minutes_to_decimal_hours(minutes: int, places: int = 4) -> str:
    if minutes <= 0:
        return "0"
    val = (Decimal(minutes) / Decimal(60)).quantize(
        Decimal("1." + "0" * places),
        rounding=ROUND_HALF_UP
    )
    return format(val, "f")

def shift_hours(shift: "Shift") -> float:
    mins = shift_minutes(shift)
    return float(Decimal(mins) / Decimal(60)) if mins else 0.0

def last_completed_payroll_week(reference: datetime | None = None):
    ref_local = reference or now_local()
    weekday = ref_local.weekday()  # Monday=0
    this_monday = ref_local.date() - timedelta(days=weekday)
    last_monday = this_monday - timedelta(days=7)
    last_sunday = last_monday + timedelta(days=6)

    if APP_TZ:
        start_dt = datetime.combine(last_monday, dtime.min, tzinfo=APP_TZ)
        end_dt = datetime.combine(last_sunday, dtime.max, tzinfo=APP_TZ)
    else:
        start_dt = datetime.combine(last_monday, dtime.min)
        end_dt = datetime.combine(last_sunday, dtime.max)

    return start_dt, end_dt

def require_admin():
    return session.get("admin_logged_in") is True

def admin_guard():
    if not require_admin():
        return redirect(url_for("admin_login"))
    return None

def admin_username() -> str:
    return (session.get("admin_username") or ADMIN_USERNAME or "admin")

# ✅ Canonical store codes = lowercase
def normalize_store_code(val: str) -> str:
    return (val or "").strip().lower()

def log_event(event: str, **fields):
    parts = [f"{k}={fields[k]}" for k in sorted(fields.keys())]
    app.logger.info("%s %s", event, " ".join(parts))

# -----------------------------
# Mobile ingest helpers
# -----------------------------
def _get_device_token() -> str:
    token = (request.headers.get("X-Device-Token") or "").strip()
    if token:
        return token

    auth = (request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()

    return ""

def _require_mobile_auth():
    expected = (app.config.get("MOBILE_DEVICE_TOKEN") or "").strip()
    provided = _get_device_token()

    if not expected:
        # Fail closed: don't allow anonymous ingest if you forgot to set env var on Render
        app.logger.error("MOBILE_DEVICE_TOKEN is not set on the server.")
        return False, ("server_not_configured", 500)

    if not provided or provided != expected:
        return False, ("unauthorized", 401)

    return True, None

def _dev_guard():
    if not ENABLE_DEV_EXPORTS:
        return False, ("not_found", 404)
    ok, err = _require_mobile_auth()
    if not ok:
        return False, err
    return True, None

def _safe_json_dumps(obj) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return json.dumps({"_error": "json_dumps_failed"}, separators=(",", ":"))

def _extract_location_coords(payload: dict) -> tuple[dict, dict]:
    loc = {}
    if isinstance(payload.get("location"), dict):
        loc = payload.get("location") or {}
    elif isinstance(payload.get("params"), dict) and isinstance((payload.get("params") or {}).get("location"), dict):
        loc = (payload.get("params") or {}).get("location") or {}
    elif isinstance(payload.get("data"), dict) and isinstance((payload.get("data") or {}).get("location"), dict):
        loc = (payload.get("data") or {}).get("location") or {}

    coords = (loc.get("coords") or {}) if isinstance(loc, dict) else {}
    if not isinstance(coords, dict):
        coords = {}
    return loc, coords

def _extract_event_at(payload: dict, loc: dict | None) -> datetime | None:
    ts_ms = payload.get("timestamp")
    if ts_ms is None and isinstance(loc, dict):
        ts_ms = loc.get("timestamp")

    if isinstance(ts_ms, (int, float)) and ts_ms > 0:
        try:
            return datetime.utcfromtimestamp(ts_ms / 1000.0)
        except Exception:
            return None
    return None

def _coerce_str(val, max_len: int = 120) -> str | None:
    if val is None:
        return None
    try:
        s = str(val).strip()
    except Exception:
        return None
    if not s:
        return None
    return s[:max_len]

def _touch_employee_device(emp: "Employee", device_uuid: str | None, device_label: str | None):
    """
    Option C behavior: if device_uuid provided, overwrite employee.device_uuid.
    Never blocks clock-ins.
    """
    if not device_uuid:
        return
    try:
        emp.device_uuid = device_uuid
        if device_label:
            emp.device_label = device_label
        emp.device_last_seen_at = now_utc()
    except Exception:
        pass

def _device_has_other_open_shift(device_uuid: str, employee_id: int) -> "Shift | None":
    """
    Prevent the obvious abuse: one phone can't have an open shift for Employee A
    while Employee B tries to clock in on same device.
    """
    if not device_uuid:
        return None
    return (
        Shift.query
        .filter(
            Shift.clock_out.is_(None),
            Shift.clock_in_device_uuid == device_uuid,
            Shift.employee_id != employee_id
        )
        .order_by(Shift.clock_in.desc())
        .first()
    )

# Make helpers available in templates
@app.context_processor
def inject_helpers():
    return dict(
        fmt_dt=fmt_dt,
        shift_minutes=shift_minutes,
        minutes_to_human=minutes_to_human,
        minutes_to_short=minutes_to_short,
        minutes_to_decimal_hours=minutes_to_decimal_hours
    )

# -----------------------------
# ✅ Option B-safe: add missing columns without migrations
# -----------------------------
def _ensure_column(table_name: str, column_name: str, sql_type: str):
    """
    Best-effort: add a column if it doesn't exist.
    Works for SQLite and Postgres for simple ADD COLUMN cases.
    Race-safe on Render: ignores "already exists" errors.
    """
    try:
        bind = db.engine
        dialect = bind.dialect.name

        exists = False

        if dialect == "postgresql":
            q = text("""
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = :t AND column_name = :c
                LIMIT 1
            """)
            row = db.session.execute(q, {"t": table_name, "c": column_name}).first()
            exists = bool(row)

        elif dialect == "sqlite":
            q = text(f"PRAGMA table_info({table_name})")
            rows = db.session.execute(q).fetchall()
            exists = any((r[1] == column_name) for r in rows)  # r[1] = name

        if exists:
            return

        db.session.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {sql_type}"))
        db.session.commit()
        app.logger.info("Added missing column %s.%s", table_name, column_name)

    except Exception as e:
        db.session.rollback()
        msg = str(e).lower()
        if "already exists" in msg or "duplicate" in msg:
            app.logger.info("Column already exists (race): %s.%s", table_name, column_name)
            return
        app.logger.exception("Could not ensure column %s.%s", table_name, column_name)

# -----------------------------
# Create tables on startup (Option B)
# -----------------------------
with app.app_context():
    try:
        db.create_all()
        app.logger.info("DB create_all OK")
    except Exception as e:
        app.logger.exception("DB create_all failed: %s", e)

    # Ensure new Option 2 columns exist (no migrations needed)
    _ensure_column("employees", "device_uuid", "VARCHAR(120)")
    _ensure_column("employees", "device_label", "VARCHAR(120)")
    _ensure_column("employees", "device_last_seen_at", "TIMESTAMP")

    _ensure_column("shifts", "clock_in_device_uuid", "VARCHAR(120)")
    _ensure_column("shifts", "clock_out_device_uuid", "VARCHAR(120)")

# -----------------------------
# Fingerprint (DEBUG)
# -----------------------------
@app.get("/__fingerprint__")
def fingerprint():
    return "clockin_app LIVE fingerprint 2026-02-16"

# -----------------------------
# Optional: favicon
# -----------------------------
@app.get("/favicon.ico")
def favicon():
    return ("", 204)

# -----------------------------
# DEV endpoints (locked down)
# -----------------------------
@app.get("/dev/db-info")
def dev_db_info():
    ok, err = _dev_guard()
    if not ok:
        msg, code = err
        return jsonify({"ok": False, "error": msg}), code

    uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    return jsonify({
        "ok": True,
        "db_uri": uri,
        "store_count": Store.query.count(),
    })

@app.get("/dev/routes")
def dev_routes():
    ok, err = _dev_guard()
    if not ok:
        msg, code = err
        return jsonify({"ok": False, "error": msg}), code
    return jsonify(sorted([str(r) for r in app.url_map.iter_rules()]))

@app.get("/dev/export-stores", endpoint="dev_export_stores_v2")
def dev_export_stores():
    ok, err = _dev_guard()
    if not ok:
        msg, code = err
        return jsonify({"ok": False, "error": msg}), code

    stores = Store.query.order_by(Store.id.asc()).all()
    return jsonify({
        "ok": True,
        "stores": [
            {
                "name": s.name,
                "qr_token": s.qr_token,
                "latitude": s.latitude,
                "longitude": s.longitude,
                "geofence_radius_m": s.geofence_radius_m,
            }
            for s in stores
        ]
    })

@app.get("/dev/export-employees", endpoint="dev_export_employees_v2")
def dev_export_employees():
    ok, err = _dev_guard()
    if not ok:
        msg, code = err
        return jsonify({"ok": False, "error": msg}), code

    emps = Employee.query.order_by(Employee.id.asc()).all()
    return jsonify({
        "ok": True,
        "employees": [
            {"name": e.name, "pin": e.pin, "active": bool(e.active)}
            for e in emps
        ]
    })

@app.post("/dev/import-stores")
def dev_import_stores():
    ok, err = _dev_guard()
    if not ok:
        msg, code = err
        return jsonify({"ok": False, "error": msg}), code

    data = request.get_json(silent=True) or {}
    stores = data.get("stores") or []
    if not isinstance(stores, list):
        return jsonify({"ok": False, "error": "stores_must_be_list"}), 400

    upserted = 0
    for s in stores:
        name = (s.get("name") or "").strip()
        qr_token = normalize_store_code(s.get("qr_token") or "")
        lat = s.get("latitude")
        lon = s.get("longitude")
        radius = s.get("geofence_radius_m", 150)

        if not name or not qr_token or lat is None or lon is None:
            continue

        try:
            lat = float(lat)
            lon = float(lon)
            radius = int(radius)
        except (TypeError, ValueError):
            continue

        existing = Store.query.filter(func.lower(Store.qr_token) == qr_token).first()
        if existing:
            existing.name = name
            existing.latitude = lat
            existing.longitude = lon
            existing.geofence_radius_m = radius
        else:
            db.session.add(Store(
                name=name,
                qr_token=qr_token,
                latitude=lat,
                longitude=lon,
                geofence_radius_m=radius,
                created_at=now_utc()
            ))
        upserted += 1

    db.session.commit()
    return jsonify({"ok": True, "imported_or_updated": upserted})

@app.post("/dev/import-employees")
def dev_import_employees():
    ok, err = _dev_guard()
    if not ok:
        msg, code = err
        return jsonify({"ok": False, "error": msg}), code

    data = request.get_json(silent=True) or {}
    employees = data.get("employees") or []
    if not isinstance(employees, list):
        return jsonify({"ok": False, "error": "employees_must_be_list"}), 400

    upserted = 0
    for e in employees:
        name = (e.get("name") or "").strip()
        pin = (e.get("pin") or "").strip()
        active = bool(e.get("active", True))

        if not name or not pin:
            continue

        existing = Employee.query.filter_by(pin=pin).first()
        if existing:
            existing.name = name
            existing.active = active
        else:
            db.session.add(Employee(
                name=name,
                pin=pin,
                active=active,
                created_at=now_utc()
            ))
        upserted += 1

    db.session.commit()
    return jsonify({"ok": True, "imported_or_updated": upserted})

@app.post("/dev/add-store")
def dev_add_store():
    ok, err = _dev_guard()
    if not ok:
        msg, code = err
        return jsonify({"ok": False, "error": msg}), code

    data = request.get_json(silent=True) or {}

    name = (data.get("name") or "").strip()
    qr_token = normalize_store_code(data.get("qr_token") or "")
    lat = data.get("lat")
    lon = data.get("lon")
    radius = data.get("geofence_radius_m", 200)

    if not name or not qr_token or lat is None or lon is None:
        return jsonify({"ok": False, "error": "missing_fields", "required": ["name", "qr_token", "lat", "lon"]}), 400

    try:
        lat = float(lat)
        lon = float(lon)
        radius = int(radius)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "invalid_values"}), 400

    store = Store.query.filter(func.lower(Store.qr_token) == qr_token).first()
    if store:
        store.name = name
        store.latitude = lat
        store.longitude = lon
        store.geofence_radius_m = radius
    else:
        store = Store(
            name=name,
            qr_token=qr_token,
            latitude=lat,
            longitude=lon,
            geofence_radius_m=radius
        )
        db.session.add(store)

    db.session.commit()
    return jsonify({"ok": True, "store_id": store.id, "name": store.name})

# -----------------------------
# Store Suggest API (Autocomplete)
# -----------------------------
@app.get("/api/stores/suggest")
def api_stores_suggest():
    """
    Autocomplete support for employee store-code entry.
    Query: /api/stores/suggest?q=rea
    Returns: [{code, name}] (code is qr_token)
    """
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify([])

    ql = q.lower()
    matches = (
        Store.query
        .filter(
            (func.lower(Store.qr_token).like(f"%{ql}%")) |
            (func.lower(Store.name).like(f"%{ql}%"))
        )
        .order_by(Store.name.asc())
        .limit(10)
        .all()
    )

    return jsonify([{"code": s.qr_token, "name": s.name} for s in matches])

@app.get("/api/stores/all")
def api_stores_all():
    """
    Returns all stores for the mobile store picker.
    Public (no auth). Only exposes store name + code.
    """
    stores = Store.query.order_by(Store.name.asc()).all()
    return jsonify([{"code": s.qr_token, "name": s.name} for s in stores])

# -----------------------------
# ✅ Mobile identity + geofence endpoints (Option 2)
# -----------------------------
@app.post("/api/mobile/me")
def api_mobile_me():
    """
    Option C: PIN always works. Device UUID is accepted and stored as "last seen".
    Body: { pin, device_uuid?, device_label? }
    """
    ok, err = _require_mobile_auth()
    if not ok:
        msg, code = err
        return jsonify({"ok": False, "error": msg}), code

    data = request.get_json(silent=True) or {}
    pin = (data.get("pin") or "").strip()
    device_uuid = _coerce_str(data.get("device_uuid") or data.get("uuid"))
    device_label = _coerce_str(data.get("device_label"))

    if not pin:
        return jsonify({"ok": False, "error": "missing_pin"}), 400

    emp = Employee.query.filter_by(pin=pin).first()
    if not emp or not emp.active:
        return jsonify({"ok": False, "error": "invalid_or_inactive_employee"}), 403

    _touch_employee_device(emp, device_uuid, device_label)
    db.session.commit()

    return jsonify({
        "ok": True,
        "employee": {
            "id": emp.id,
            "name": emp.name,
            "active": bool(emp.active),
            "device_uuid": emp.device_uuid,
            "device_label": emp.device_label,
            "device_last_seen_at": fmt_dt(emp.device_last_seen_at) if emp.device_last_seen_at else ""
        },
        "server_time_utc": now_utc().isoformat() + "Z"
    })

@app.post("/api/mobile/status")
def api_mobile_status():
    """
    Returns employee identity + current open shift (if any).
    Body: { pin, device_uuid?, device_label? }
    """
    ok, err = _require_mobile_auth()
    if not ok:
        msg, code = err
        return jsonify({"ok": False, "error": msg}), code

    data = request.get_json(silent=True) or {}
    pin = (data.get("pin") or "").strip()
    device_uuid = _coerce_str(data.get("device_uuid") or data.get("uuid"))
    device_label = _coerce_str(data.get("device_label"))

    if not pin:
        return jsonify({"ok": False, "error": "missing_pin"}), 400

    emp = Employee.query.filter_by(pin=pin).first()
    if not emp or not emp.active:
        return jsonify({"ok": False, "error": "invalid_or_inactive_employee"}), 403

    _touch_employee_device(emp, device_uuid, device_label)

    open_shift = (
        Shift.query
        .filter(Shift.employee_id == emp.id, Shift.clock_out.is_(None))
        .order_by(Shift.clock_in.desc())
        .first()
    )

    payload = {
        "ok": True,
        "employee": {
            "id": emp.id,
            "name": emp.name,
            "active": bool(emp.active),
            "device_uuid": emp.device_uuid,
            "device_label": emp.device_label,
            "device_last_seen_at": fmt_dt(emp.device_last_seen_at) if emp.device_last_seen_at else "",
        },
        "server_time_utc": now_utc().isoformat() + "Z",
        "open_shift": None,
    }

    if open_shift:
        store = Store.query.get(open_shift.store_id)
        payload["open_shift"] = {
            "shift_id": open_shift.id,
            "store_id": open_shift.store_id,
            "store_name": store.name if store else "",
            "clock_in_utc": open_shift.clock_in.isoformat() + "Z",
            "clock_in_local": fmt_dt(open_shift.clock_in),
            "closed_by_admin": bool(open_shift.closed_by_admin),
        }

    db.session.commit()
    return jsonify(payload), 200

@app.post("/api/mobile/clock-in")
def api_mobile_clock_in():
    ok, err = _require_mobile_auth()
    if not ok:
        msg, code = err
        return jsonify({"ok": False, "error": msg}), code

    data = request.get_json(silent=True) or {}

    pin = (data.get("pin") or "").strip()
    lat = data.get("lat")
    lon = data.get("lon")
    accuracy_m = data.get("accuracy_m")
    device_uuid = _coerce_str(data.get("device_uuid") or data.get("uuid"))
    device_label = _coerce_str(data.get("device_label"))

    if not pin or lat is None or lon is None:
        return jsonify({"ok": False, "error": "missing_required_fields"}), 400

    try:
        lat = float(lat)
        lon = float(lon)
        if accuracy_m is not None:
            accuracy_m = float(accuracy_m)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "invalid_location"}), 400

    emp = Employee.query.filter_by(pin=pin).first()
    if not emp or not emp.active:
        return jsonify({"ok": False, "error": "invalid_or_inactive_employee"}), 403

    # Prevent double clock-in
    existing = Shift.query.filter(
        Shift.employee_id == emp.id,
        Shift.clock_out.is_(None)
    ).first()

    if existing:
        return jsonify({"ok": False, "error": "already_clocked_in"}), 409

    # Auto-detect store from location
    result = find_store_for_location(lat, lon, accuracy_m)
    if not result.get("ok"):
        return jsonify({"ok": False, "error": "location_invalid", **result}), 403

    store = result["store"]

    # Device guardrail
    if device_uuid:
        other = _device_has_other_open_shift(device_uuid, emp.id)
        if other:
            return jsonify({"ok": False, "error": "device_in_use"}), 409

    _touch_employee_device(emp, device_uuid, device_label)

    shift = Shift(
        employee_id=emp.id,
        store_id=store.id,
        clock_in=now_utc(),
        clock_in_lat=lat,
        clock_in_lng=lon,
        clock_in_device_uuid=device_uuid
    )

    db.session.add(shift)
    db.session.commit()

    return jsonify({
        "ok": True,
        "shift_id": shift.id,
        "store_name": store.name,
        "clock_in_utc": shift.clock_in.isoformat() + "Z"
    })

@app.post("/api/mobile/clock-out")
def api_mobile_clock_out():
    ok, err = _require_mobile_auth()
    if not ok:
        msg, code = err
        return jsonify({"ok": False, "error": msg}), code

    data = request.get_json(silent=True) or {}

    pin = (data.get("pin") or "").strip()
    lat = data.get("lat")
    lon = data.get("lon")
    accuracy_m = data.get("accuracy_m")
    device_uuid = _coerce_str(data.get("device_uuid") or data.get("uuid"))
    device_label = _coerce_str(data.get("device_label"))

    if not pin or lat is None or lon is None:
        return jsonify({"ok": False, "error": "missing_required_fields"}), 400

    try:
        lat = float(lat)
        lon = float(lon)
        if accuracy_m is not None:
            accuracy_m = float(accuracy_m)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "invalid_location"}), 400

    emp = Employee.query.filter_by(pin=pin).first()
    if not emp or not emp.active:
        return jsonify({"ok": False, "error": "invalid_or_inactive_employee"}), 403

    open_shift = (
        Shift.query
        .filter(Shift.employee_id == emp.id, Shift.clock_out.is_(None))
        .order_by(Shift.clock_in.desc())
        .first()
    )

    if not open_shift:
        return jsonify({"ok": False, "error": "no_open_shift"}), 409

    store = Store.query.get(open_shift.store_id)

    result = find_store_for_location(lat, lon, accuracy_m)
    if not result.get("ok"):
        return jsonify({"ok": False, "error": "location_invalid", **result}), 403

    if store and result.get("store").id != store.id:
        return jsonify({"ok": False, "error": "wrong_store_location"}), 403

    _touch_employee_device(emp, device_uuid, device_label)

    open_shift.clock_out = now_utc()
    open_shift.clock_out_lat = lat
    open_shift.clock_out_lng = lon
    open_shift.clock_out_device_uuid = device_uuid

    db.session.commit()

    minutes = shift_minutes(open_shift)

    return jsonify({
        "ok": True,
        "shift_id": open_shift.id,
        "clock_out_utc": open_shift.clock_out.isoformat() + "Z",
        "minutes": minutes,
        "human": minutes_to_human(minutes)
    })

@app.post("/api/mobile/auto-exit-close")
def api_mobile_auto_exit_close():
    ok, err = _require_mobile_auth()
    if not ok:
        msg, code = err
        return jsonify({"ok": False, "error": msg}), code

    data = request.get_json(silent=True) or {}

    pin = (data.get("pin") or "").strip()
    lat = data.get("lat")
    lon = data.get("lon")
    accuracy_m = data.get("accuracy_m")
    device_uuid = _coerce_str(data.get("device_uuid") or data.get("uuid"))
    device_label = _coerce_str(data.get("device_label"))

    # optional: reason from app
    reason = (data.get("reason") or "Auto-close after EXIT").strip()

    if not pin or lat is None or lon is None:
        return jsonify({"ok": False, "error": "missing_required_fields"}), 400

    try:
        lat = float(lat)
        lon = float(lon)
        if accuracy_m is not None:
            accuracy_m = float(accuracy_m)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "invalid_location"}), 400

    emp = Employee.query.filter_by(pin=pin).first()
    if not emp or not emp.active:
        return jsonify({"ok": False, "error": "invalid_or_inactive_employee"}), 403

    # Open shift required
    open_shift = (
        Shift.query
        .filter(Shift.employee_id == emp.id, Shift.clock_out.is_(None))
        .order_by(Shift.clock_in.desc())
        .first()
    )
    if not open_shift:
        return jsonify({"ok": True, "already_closed": True, "message": "No open shift."}), 200

    store = Store.query.get(open_shift.store_id)
    if not store:
        return jsonify({"ok": False, "error": "store_not_found"}), 500

    # Distance check
    dist_m = haversine_m(lat, lon, store.latitude, store.longitude)

    # Accuracy gate (prevent bad GPS closing someone incorrectly)
    # Match your validate-location gate style
    if accuracy_m is not None and accuracy_m > 120:
        return jsonify({
            "ok": False,
            "error": "accuracy_too_low",
            "message": "GPS accuracy too low to auto-close. Try again.",
            "accuracy_m": accuracy_m
        }), 409

    # Only allow auto-close if OUTSIDE radius (with a little buffer)
    buffer_m = 15.0
    if dist_m <= (store.geofence_radius_m + buffer_m):
        return jsonify({
            "ok": False,
            "error": "still_inside_or_near_store",
            "dist_m": float(dist_m),
            "radius_m": float(store.geofence_radius_m),
            "buffer_m": buffer_m
        }), 409

    # Touch employee device last-seen
    _touch_employee_device(emp, device_uuid, device_label)

    # Close shift as admin override
    old_in = open_shift.clock_in
    old_out = open_shift.clock_out

    open_shift.clock_out = now_utc()
    open_shift.clock_out_lat = lat
    open_shift.clock_out_lng = lon
    open_shift.clock_out_device_uuid = device_uuid

    open_shift.closed_by_admin = True
    open_shift.admin_closed_by = "AUTO_EXIT"
    open_shift.admin_closed_at = now_utc()
    open_shift.admin_close_reason = reason

    audit = ShiftEditAudit(
        shift_id=open_shift.id,
        action="auto_exit_close",
        editor="AUTO_EXIT",
        reason=reason,
        old_clock_in=old_in,
        old_clock_out=old_out,
        new_clock_in=open_shift.clock_in,
        new_clock_out=open_shift.clock_out
    )
    db.session.add(audit)
    db.session.commit()

    log_event(
        "AUTO_EXIT_CLOSE_OK",
        employee_id=emp.id,
        shift_id=open_shift.id,
        store_id=store.id,
        dist_m=round(dist_m, 1),
        radius_m=store.geofence_radius_m,
        accuracy_m=accuracy_m if accuracy_m is not None else "",
        device_uuid=device_uuid or "",
    )

    mins = shift_minutes(open_shift)

    return jsonify({
        "ok": True,
        "shift_id": open_shift.id,
        "store_name": store.name,
        "dist_m": round(dist_m, 1),
        "minutes": mins,
        "human": minutes_to_human(mins),
        "clock_out_utc": open_shift.clock_out.isoformat() + "Z",
        "message": "Shift auto-closed after EXIT."
    }), 200

@app.post("/api/mobile/geofences")
def api_mobile_geofences():
    """
    Returns the *real* store geofence based on store_code.
    Body: { pin, qr_token, device_uuid? }  (pin is required; device_uuid stored for last-seen)
    """
    ok, err = _require_mobile_auth()
    if not ok:
        msg, code = err
        return jsonify({"ok": False, "error": msg}), code

    data = request.get_json(silent=True) or {}

    pin = (data.get("pin") or "").strip()
    qr_token = normalize_store_code((data.get("qr_token") or "").strip())

    device_uuid = _coerce_str(data.get("device_uuid") or data.get("uuid"))
    device_label = _coerce_str(data.get("device_label"))

    if not pin or not qr_token:
        return jsonify({"ok": False, "error": "missing_pin_or_store_code"}), 400

    emp = Employee.query.filter_by(pin=pin).first()
    if not emp or not emp.active:
        return jsonify({"ok": False, "error": "invalid_or_inactive_employee"}), 403

    store = Store.query.filter(func.lower(Store.qr_token) == qr_token).first()
    if not store:
        return jsonify({"ok": False, "error": "invalid_store_code"}), 404

    _touch_employee_device(emp, device_uuid, device_label)
    db.session.commit()

    geofences = [{
        "identifier": f"store_{store.id}",
        "latitude": float(store.latitude),
        "longitude": float(store.longitude),
        "radius": int(store.geofence_radius_m),
        "notifyOnEntry": True,
        "notifyOnExit": True
    }]

    return jsonify({
        "ok": True,
        "store": {"id": store.id, "name": store.name, "code": store.qr_token, "radius_m": store.geofence_radius_m},
        "geofences": geofences
    })

# -----------------------------
# ✅ Mobile event ingest (Transistorsoft BG Geolocation)
# -----------------------------
@app.post("/api/mobile/bg/event")
def api_mobile_bg_event():
    ok, err = _require_mobile_auth()
    if not ok:
        msg, code = err
        return jsonify({"ok": False, "error": msg}), code

    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "invalid_json"}), 400

    event_type = (payload.get("event") or payload.get("name") or payload.get("type") or "unknown")
    event_type = str(event_type).strip().lower() or "unknown"

    loc, coords = _extract_location_coords(payload)

    device_uuid = payload.get("uuid")
    if not device_uuid and isinstance(payload.get("device"), dict):
        device_uuid = (payload.get("device") or {}).get("uuid")
    if device_uuid is not None:
        device_uuid = str(device_uuid)

    is_moving = payload.get("is_moving")
    if is_moving is None and isinstance(loc, dict):
        is_moving = loc.get("is_moving")

    lat = coords.get("latitude")
    lng = coords.get("longitude")
    accuracy = coords.get("accuracy")

    event_at = _extract_event_at(payload, loc)

    try:
        evt = MobileEvent(
            event_type=event_type,
            device_uuid=device_uuid,
            is_moving=bool(is_moving) if isinstance(is_moving, bool) else None,
            lat=float(lat) if isinstance(lat, (int, float)) else None,
            lng=float(lng) if isinstance(lng, (int, float)) else None,
            accuracy=float(accuracy) if isinstance(accuracy, (int, float)) else None,
            event_at=event_at,
            received_at=now_utc(),
            raw_json=_safe_json_dumps(payload),
        )
        db.session.add(evt)
        db.session.commit()
    except Exception:
        app.logger.exception("MOBILE_BG_EVENT_SAVE_FAILED")
        return jsonify({"ok": False, "error": "db_error"}), 500

    return jsonify({"ok": True, "id": evt.id})

@app.post("/api/mobile/report-issue")
def api_mobile_report_issue():
    ok, err = _require_mobile_auth()
    if not ok:
        msg, code = err
        return jsonify({"ok": False, "error": msg}), code

    data = request.get_json(silent=True) or {}

    pin = (data.get("pin") or "").strip()
    if not pin:
        return jsonify({"ok": False, "error": "missing_pin"}), 400

    emp = Employee.query.filter_by(pin=pin).first()
    if not emp or not emp.active:
        return jsonify({"ok": False, "error": "invalid_or_inactive_employee"}), 403

    msg = (data.get("message") or "").strip() or None
    payload = data.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {"_raw": payload}

    # Try to attach store_id / shift_id if possible
    store_id = None
    shift_id = None

    try:
        store_obj = payload.get("store") if isinstance(payload.get("store"), dict) else {}
        store_code = normalize_store_code(store_obj.get("code") or "")
        if store_code:
            s = Store.query.filter(func.lower(Store.qr_token) == store_code).first()
            if s:
                store_id = s.id
    except Exception:
        pass

    try:
        open_shift = (
            Shift.query
            .filter(Shift.employee_id == emp.id, Shift.clock_out.is_(None))
            .order_by(Shift.clock_in.desc())
            .first()
        )
        if open_shift:
            shift_id = open_shift.id
            if not store_id:
                store_id = open_shift.store_id
    except Exception:
        pass

    try:
        report = MobileIssueReport(
            employee_id=emp.id,
            store_id=store_id,
            shift_id=shift_id,
            message=msg,
            payload_json=_safe_json_dumps(payload),
            status="open",
            created_at=now_utc(),
        )
        db.session.add(report)
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception("MOBILE_ISSUE_SAVE_FAILED")
        return jsonify({"ok": False, "error": "db_error"}), 500

    # Optional log line
    app.logger.warning(f"[MOBILE ISSUE] id={report.id} emp={emp.id} {emp.name} store_id={store_id} shift_id={shift_id}")

    return jsonify({"ok": True, "id": report.id})

@app.post("/api/mobile/bg/locations")
def api_mobile_bg_locations_bulk():
    """
    Optional bulk endpoint if you configure BG to POST arrays of locations.
    """
    ok, err = _require_mobile_auth()
    if not ok:
        msg, code = err
        return jsonify({"ok": False, "error": msg}), code

    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "invalid_json"}), 400

    locations = payload.get("locations")
    if not isinstance(locations, list):
        return jsonify({"ok": False, "error": "expected_locations_array"}), 400

    device_uuid = payload.get("uuid")
    if device_uuid is not None:
        device_uuid = str(device_uuid)

    saved = 0
    try:
        for item in locations:
            if not isinstance(item, dict):
                continue

            coords = item.get("coords") if isinstance(item.get("coords"), dict) else {}
            ts_ms = item.get("timestamp")
            event_at = None
            if isinstance(ts_ms, (int, float)) and ts_ms > 0:
                try:
                    event_at = datetime.utcfromtimestamp(ts_ms / 1000.0)
                except Exception:
                    event_at = None

            evt = MobileEvent(
                event_type="location",
                device_uuid=str(item.get("uuid") or device_uuid) if (item.get("uuid") or device_uuid) else None,
                is_moving=bool(item.get("is_moving")) if isinstance(item.get("is_moving"), bool) else None,
                lat=float(coords.get("latitude")) if isinstance(coords.get("latitude"), (int, float)) else None,
                lng=float(coords.get("longitude")) if isinstance(coords.get("longitude"), (int, float)) else None,
                accuracy=float(coords.get("accuracy")) if isinstance(coords.get("accuracy"), (int, float)) else None,
                event_at=event_at,
                received_at=now_utc(),
                raw_json=_safe_json_dumps(item),
            )
            db.session.add(evt)
            saved += 1

        db.session.commit()
    except Exception:
        app.logger.exception("MOBILE_BG_LOCATIONS_SAVE_FAILED")
        return jsonify({"ok": False, "error": "db_error"}), 500

    return jsonify({"ok": True, "saved": saved})

# -----------------------------
# Legacy-ish mobile endpoints (now token protected)
# -----------------------------
@app.post("/mobile/validate-location")
def mobile_validate_location():
    ok, err = _require_mobile_auth()
    if not ok:
        msg, code = err
        return jsonify({"ok": False, "error": msg}), code

    data = request.get_json(silent=True) or {}

    lat = data.get("lat")
    lon = data.get("lon")
    accuracy_m = data.get("accuracy_m")

    if lat is None or lon is None:
        return jsonify({"ok": False, "error": "missing_lat_lon"}), 400

    try:
        lat = float(lat)
        lon = float(lon)
        if accuracy_m is not None:
            accuracy_m = float(accuracy_m)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "invalid_lat_lon"}), 400

    result = find_store_for_location(lat, lon, accuracy_m)

    if not result.get("ok"):
        return jsonify(result), 200

    store = result["store"]
    return jsonify({
        "ok": True,
        "store_id": store.id,
        "store_name": store.name,
        "distance_m": result["distance_m"],
        "geofence_radius_m": store.geofence_radius_m,
    }), 200

@app.post("/mobile/clock-in")
def mobile_clock_in():
    ok, err = _require_mobile_auth()
    if not ok:
        msg, code = err
        return jsonify({"ok": False, "error": msg}), code

    data = request.get_json(silent=True) or {}

    pin = (data.get("pin") or "").strip()
    device_uuid = (data.get("device_uuid") or "").strip()

    lat = data.get("lat")
    lon = data.get("lon")
    accuracy_m = data.get("accuracy_m")

    if not pin or not device_uuid:
        return jsonify({"ok": False, "error": "missing_pin_or_device_uuid"}), 400

    if lat is None or lon is None:
        return jsonify({"ok": False, "error": "missing_lat_lon"}), 400

    try:
        lat = float(lat)
        lon = float(lon)
        if accuracy_m is not None:
            accuracy_m = float(accuracy_m)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "invalid_lat_lon"}), 400

    employee = Employee.query.filter_by(pin=pin, active=True).first()
    if not employee:
        return jsonify({"ok": False, "error": "invalid_pin"}), 401

    store_result = find_store_for_location(lat, lon, accuracy_m)
    if not store_result.get("ok"):
        return jsonify({"ok": False, "error": "location_invalid", **store_result}), 200

    store = store_result["store"]
    dist_m = store_result.get("distance_m")

    open_shift = Shift.query.filter_by(employee_id=employee.id, clock_out=None).order_by(Shift.clock_in.desc()).first()
    if open_shift:
        return jsonify({
            "ok": True,
            "already_clocked_in": True,
            "shift_id": open_shift.id,
            "employee_id": employee.id,
            "employee_name": employee.name,
            "store_id": open_shift.store_id,
            "store_name": Store.query.get(open_shift.store_id).name if open_shift.store_id else None,
            "clock_in": open_shift.clock_in.isoformat(),
        }), 200

    shift = Shift(
        employee_id=employee.id,
        store_id=store.id,
        clock_in=now_utc(),
        clock_in_lat=lat,
        clock_in_lng=lon,
        clock_in_device_uuid=device_uuid,
        closed_by_admin=False,
        created_at=now_utc(),
    )
    db.session.add(shift)
    db.session.commit()

    return jsonify({
        "ok": True,
        "already_clocked_in": False,
        "shift_id": shift.id,
        "employee_id": employee.id,
        "employee_name": employee.name,
        "store_id": store.id,
        "store_name": store.name,
        "distance_m": dist_m,
        "geofence_radius_m": store.geofence_radius_m,
        "clock_in": shift.clock_in.isoformat(),
    }), 200

# -----------------------------
# Employee Clock Page
# -----------------------------
@app.get("/employee")
def employee_page():
    stores = Store.query.order_by(Store.name.asc()).all()
    stores_min = [{"name": s.name, "code": s.qr_token} for s in stores]
    return render_template("employee_clock.html", stores=stores_min)

# -----------------------------
# Employee API (Clock In/Out)
# -----------------------------
@app.post("/api/clockin")
def api_clockin():
    data = request.get_json(force=True, silent=True) or {}

    pin = (data.get("pin") or "").strip()
    qr_token = normalize_store_code((data.get("qr_token") or "").strip())
    lat = data.get("lat")
    lng = data.get("lng")

    device_uuid = _coerce_str(data.get("device_uuid") or data.get("uuid"))
    device_label = _coerce_str(data.get("device_label"))

    if not pin or not qr_token:
        return jsonify({"error": "Missing PIN or store code."}), 400

    emp = Employee.query.filter_by(pin=pin).first()
    if not emp or not emp.active:
        return jsonify({"error": "Invalid or inactive employee."}), 403

    store = Store.query.filter(func.lower(Store.qr_token) == qr_token).first()
    if not store:
        log_event("CLOCKIN_DENY_INVALID_STORE", employee_pin=pin, store_code=qr_token)
        return jsonify({"error": "Invalid store code."}), 404

    open_shift = Shift.query.filter_by(employee_id=emp.id, clock_out=None).order_by(Shift.clock_in.desc()).first()
    if open_shift:
        log_event("CLOCKIN_DENY_ALREADY_CLOCKED_IN", employee_id=emp.id, open_shift_id=open_shift.id)
        return jsonify({"error": "You are already clocked in. Please clock out first."}), 409

    if device_uuid:
        other = _device_has_other_open_shift(device_uuid, emp.id)
        if other:
            log_event(
                "CLOCKIN_DENY_DEVICE_IN_USE",
                device_uuid=device_uuid,
                employee_id=emp.id,
                other_employee_id=other.employee_id,
                other_shift_id=other.id
            )
            return jsonify({"error": "This phone is currently being used for another active shift. Use your own phone or have a manager help."}), 409

    if lat is None or lng is None:
        log_event("CLOCKIN_DENY_LOCATION_REQUIRED", employee_id=emp.id, store_id=store.id)
        return jsonify({"error": "Location required."}), 400

    try:
        lat = float(lat)
        lng = float(lng)
    except ValueError:
        log_event("CLOCKIN_DENY_BAD_LATLNG", employee_id=emp.id, store_id=store.id)
        return jsonify({"error": "Invalid lat/lng."}), 400

    dist_m = haversine_m(lat, lng, store.latitude, store.longitude)

    log_event(
        "CLOCKIN_ATTEMPT",
        employee_id=emp.id,
        employee=emp.name,
        store_id=store.id,
        store=store.name,
        store_code=store.qr_token,
        dist_m=round(dist_m, 1),
        radius_m=store.geofence_radius_m,
        device_uuid=device_uuid or ""
    )

    if dist_m > store.geofence_radius_m:
        log_event(
            "CLOCKIN_DENY_OUTSIDE_RADIUS",
            employee_id=emp.id,
            store_id=store.id,
            dist_m=round(dist_m, 1),
            radius_m=store.geofence_radius_m,
            device_uuid=device_uuid or ""
        )
        return jsonify({"error": "You are not at the store location."}), 403

    _touch_employee_device(emp, device_uuid, device_label)

    s = Shift(
        employee_id=emp.id,
        store_id=store.id,
        clock_in=now_utc(),
        clock_in_lat=lat,
        clock_in_lng=lng,
        clock_in_device_uuid=device_uuid,
        closed_by_admin=False,
        admin_closed_by=None,
        admin_closed_at=None,
        admin_close_reason=None,
    )
    db.session.add(s)
    db.session.commit()

    log_event("CLOCKIN_OK", employee_id=emp.id, shift_id=s.id, store_id=store.id, device_uuid=device_uuid or "")

    return jsonify({
        "ok": True,
        "employee": emp.name,
        "message": f"Clock-in successful for {emp.name} at {store.name}.",
        "shift_id": s.id,
        "clock_in": fmt_dt(s.clock_in),
    })

@app.post("/api/clockout")
def api_clockout():
    data = request.get_json(force=True, silent=True) or {}
    pin = (data.get("pin") or "").strip()
    lat = data.get("lat")
    lng = data.get("lng")

    device_uuid = _coerce_str(data.get("device_uuid") or data.get("uuid"))
    device_label = _coerce_str(data.get("device_label"))

    if not pin:
        return jsonify({"error": "Missing PIN."}), 400

    emp = Employee.query.filter_by(pin=pin).first()
    if not emp or not emp.active:
        return jsonify({"error": "Invalid or inactive employee."}), 403

    open_shift = Shift.query.filter_by(employee_id=emp.id, clock_out=None).order_by(Shift.clock_in.desc()).first()
    if not open_shift:
        log_event("CLOCKOUT_DENY_NO_OPEN_SHIFT", employee_id=emp.id)
        return jsonify({"error": "No open shift found. You must clock in first."}), 409

    if lat is None or lng is None:
        log_event("CLOCKOUT_DENY_LOCATION_REQUIRED", employee_id=emp.id, shift_id=open_shift.id)
        return jsonify({"error": "Location required."}), 400

    try:
        lat = float(lat)
        lng = float(lng)
    except ValueError:
        log_event("CLOCKOUT_DENY_BAD_LATLNG", employee_id=emp.id, shift_id=open_shift.id)
        return jsonify({"error": "Invalid lat/lng."}), 400

    store = Store.query.get(open_shift.store_id)
    dist_m = haversine_m(lat, lng, store.latitude, store.longitude)

    log_event(
        "CLOCKOUT_ATTEMPT",
        employee_id=emp.id,
        employee=emp.name,
        shift_id=open_shift.id,
        store_id=store.id,
        store=store.name,
        store_code=store.qr_token,
        dist_m=round(dist_m, 1),
        radius_m=store.geofence_radius_m,
        device_uuid=device_uuid or ""
    )

    if dist_m > store.geofence_radius_m:
        log_event(
            "CLOCKOUT_DENY_OUTSIDE_RADIUS",
            employee_id=emp.id,
            shift_id=open_shift.id,
            store_id=store.id,
            dist_m=round(dist_m, 1),
            radius_m=store.geofence_radius_m,
            device_uuid=device_uuid or ""
        )
        return jsonify({"error": "You are not at the store location."}), 403

    _touch_employee_device(emp, device_uuid, device_label)

    open_shift.clock_out = now_utc()
    open_shift.clock_out_lat = lat
    open_shift.clock_out_lng = lng
    open_shift.clock_out_device_uuid = device_uuid
    db.session.commit()

    mins = shift_minutes(open_shift)
    log_event("CLOCKOUT_OK", employee_id=emp.id, shift_id=open_shift.id, minutes=mins, device_uuid=device_uuid or "")

    return jsonify({
        "ok": True,
        "employee": emp.name,
        "message": f"Clock-out successful for {emp.name}.",
        "shift_id": open_shift.id,
        "clock_out": fmt_dt(open_shift.clock_out),
        "minutes": mins,
        "human": minutes_to_human(mins),
    })

# 15-minute location ping endpoint
@app.post("/api/ping")
def api_ping():
    data = request.get_json(force=True, silent=True) or {}

    pin = (data.get("pin") or "").strip()
    lat = (data.get("lat"))
    lng = (data.get("lng"))

    device_uuid = _coerce_str(data.get("device_uuid") or data.get("uuid"))
    device_label = _coerce_str(data.get("device_label"))

    if not pin:
        return jsonify({"error": "Missing PIN."}), 400

    emp = Employee.query.filter_by(pin=pin).first()
    if not emp or not emp.active:
        return jsonify({"error": "Invalid or inactive employee."}), 403

    open_shift = Shift.query.filter_by(employee_id=emp.id, clock_out=None).order_by(Shift.clock_in.desc()).first()
    if not open_shift:
        return jsonify({"error": "No open shift."}), 409

    if lat is None or lng is None:
        return jsonify({"error": "Location required."}), 400

    try:
        lat = float(lat)
        lng = float(lng)
    except ValueError:
        return jsonify({"error": "Invalid lat/lng."}), 400

    store = Store.query.get(open_shift.store_id)
    dist_m = haversine_m(lat, lng, store.latitude, store.longitude)
    inside = dist_m <= store.geofence_radius_m

    _touch_employee_device(emp, device_uuid, device_label)

    ping = LocationPing(
        employee_id=emp.id,
        shift_id=open_shift.id,
        store_id=store.id,
        lat=lat,
        lng=lng,
        dist_m=float(dist_m),
        inside_radius=bool(inside),
        created_at=now_utc()
    )
    db.session.add(ping)
    db.session.commit()

    log_event(
        "PING_OK",
        employee_id=emp.id,
        shift_id=open_shift.id,
        store_id=store.id,
        dist_m=round(dist_m, 1),
        inside=inside,
        device_uuid=device_uuid or ""
    )

    return jsonify({
        "ok": True,
        "shift_id": open_shift.id,
        "dist_m": round(dist_m, 1),
        "inside_radius": inside,
        "ping_at": fmt_dt(ping.created_at),
    })

# -----------------------------
# Admin Auth
# -----------------------------
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "")

        if username == ADMIN_USERNAME and check_password_hash(ADMIN_PASSWORD_HASH, password):
            session["admin_logged_in"] = True
            session["admin_username"] = username  # ✅ store for audit trail
            return redirect(url_for("admin_dashboard"))

        flash("Invalid username or password.", "danger")

    return render_template("login.html")

@app.get("/admin/logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    session.pop("admin_username", None)
    flash("Logged out.", "info")
    return redirect(url_for("admin_login"))

# -----------------------------
# Admin Pages
# -----------------------------
@app.get("/admin")
def admin_dashboard():
    guard = admin_guard()
    if guard: return guard

    total_employees = Employee.query.count()
    active_employees = Employee.query.filter_by(active=True).count()
    inactive_employees = Employee.query.filter_by(active=False).count()

    open_shifts = Shift.query.filter_by(clock_out=None).count()
    stores = Store.query.count()
    last7 = now_utc() - timedelta(days=7)
    shifts_7d = Shift.query.filter(Shift.clock_in >= last7).count()

    return render_template(
        "admin.html",
        total_employees=total_employees,
        active_employees=active_employees,
        inactive_employees=inactive_employees,
        open_shifts=open_shifts,
        stores=stores,
        shifts_7d=shifts_7d,
    )

# ✅ Admin Issues List
@app.get("/admin/issues")
def admin_issues():
    guard = admin_guard()
    if guard:
        return guard

    status = (request.args.get("status") or "open").strip().lower()
    limit_raw = (request.args.get("limit") or "").strip()

    # limit: 25..500 (default 200)
    try:
        limit = int(limit_raw) if limit_raw else 200
    except ValueError:
        limit = 200
    limit = max(25, min(limit, 500))

    q = MobileIssueReport.query

    if status in ("open", "resolved", "ignored"):
        q = q.filter(MobileIssueReport.status == status)
    else:
        status = "open"
        q = q.filter(MobileIssueReport.status == status)

    issues = q.order_by(MobileIssueReport.created_at.desc()).limit(limit).all()

    return render_template(
        "admin_issues.html",
        issues=issues,
        status=status,
        limit=limit,
    )

# ✅ Admin GPS Ping Viewer
@app.get("/admin/pings")
def admin_pings():
    guard = admin_guard()
    if guard: return guard

    start_str = (request.args.get("start") or "").strip()
    end_str = (request.args.get("end") or "").strip()
    employee_id = (request.args.get("employee_id") or "").strip()
    store_id = (request.args.get("store_id") or "").strip()
    shift_id = (request.args.get("shift_id") or "").strip()
    inside_raw = (request.args.get("inside") or "all").strip().lower()

    try:
        page = int(request.args.get("page", "1"))
    except ValueError:
        page = 1
    page = max(page, 1)

    try:
        per_page = int(request.args.get("per_page", "200"))
    except ValueError:
        per_page = 200
    per_page = max(25, min(per_page, 500))

    if not start_str or not end_str:
        today_local = now_local().date()
        default_start = today_local - timedelta(days=7)
        start_str = start_str or default_start.isoformat()
        end_str = end_str or today_local.isoformat()

    try:
        start_date = datetime.strptime(start_str, "%Y-%m-%d").date()
        end_date = datetime.strptime(end_str, "%Y-%m-%d").date()
        if APP_TZ:
            start_local = datetime.combine(start_date, dtime.min, tzinfo=APP_TZ)
            end_local = datetime.combine(end_date, dtime.max, tzinfo=APP_TZ)
        else:
            start_local = datetime.combine(start_date, dtime.min)
            end_local = datetime.combine(end_date, dtime.max)
        q_start, q_end = local_range_to_utc_naive(start_local, end_local)
    except ValueError:
        flash("Invalid start/end date format. Use YYYY-MM-DD.", "error")
        today_local = now_local().date()
        start_local = datetime.combine(today_local - timedelta(days=7), dtime.min, tzinfo=APP_TZ) if APP_TZ else datetime.combine(today_local - timedelta(days=7), dtime.min)
        end_local = datetime.combine(today_local, dtime.max, tzinfo=APP_TZ) if APP_TZ else datetime.combine(today_local, dtime.max)
        q_start, q_end = local_range_to_utc_naive(start_local, end_local)
        start_str = (today_local - timedelta(days=7)).isoformat()
        end_str = today_local.isoformat()

    q = (
        LocationPing.query
        .filter(LocationPing.created_at >= q_start, LocationPing.created_at <= q_end)
        .order_by(LocationPing.created_at.desc())
    )

    if employee_id:
        try:
            q = q.filter(LocationPing.employee_id == int(employee_id))
        except ValueError:
            flash("employee_id must be a number.", "error")

    if store_id:
        try:
            q = q.filter(LocationPing.store_id == int(store_id))
        except ValueError:
            flash("store_id must be a number.", "error")

    if shift_id:
        try:
            q = q.filter(LocationPing.shift_id == int(shift_id))
        except ValueError:
            flash("shift_id must be a number.", "error")

    inside = "all"
    if inside_raw in ("1", "true", "yes", "y", "inside"):
        q = q.filter(LocationPing.inside_radius.is_(True))
        inside = "1"
    elif inside_raw in ("0", "false", "no", "n", "outside"):
        q = q.filter(LocationPing.inside_radius.is_(False))
        inside = "0"

    offset = (page - 1) * per_page
    items = q.offset(offset).limit(per_page + 1).all()
    has_next = len(items) > per_page
    pings = items[:per_page]
    has_prev = page > 1

    employees = Employee.query.order_by(Employee.active.desc(), Employee.name.asc()).all()
    stores = Store.query.order_by(Store.name.asc()).all()

    try:
        total_in_view = q.count()
    except Exception:
        total_in_view = None

    return render_template(
        "admin_pings.html",
        pings=pings,
        employees=employees,
        stores=stores,
        start=start_str,
        end=end_str,
        employee_id=employee_id,
        store_id=store_id,
        shift_id=shift_id,
        inside=inside,
        page=page,
        per_page=per_page,
        has_prev=has_prev,
        has_next=has_next,
        total_in_view=total_in_view,
    )

# ✅ Admin Mobile Event Viewer
@app.get("/admin/mobile-events")
def admin_mobile_events():
    guard = admin_guard()
    if guard:
        return guard

    event_type = (request.args.get("event") or "").strip().lower()
    device_uuid = (request.args.get("device") or "").strip()
    try:
        limit = int(request.args.get("limit", "200"))
    except ValueError:
        limit = 200
    limit = max(25, min(limit, 500))

    q = MobileEvent.query

    if event_type:
        q = q.filter(func.lower(MobileEvent.event_type) == event_type)

    if device_uuid:
        q = q.filter(MobileEvent.device_uuid == device_uuid)

    events = q.order_by(MobileEvent.received_at.desc()).limit(limit).all()

    return render_template(
        "admin_mobile_events.html",
        events=events,
        limit=limit,
        event=event_type,
        device=device_uuid,
    )

@app.get("/admin/issues/<int:issue_id>")
def admin_issue_detail(issue_id: int):
    guard = admin_guard()
    if guard:
        return guard

    issue = MobileIssueReport.query.get(issue_id)
    if not issue:
        flash("Issue not found.", "error")
        return redirect(url_for("admin_issues"))

    # Pretty payload for template
    payload_pretty = issue.payload_json
    try:
        payload_pretty = json.dumps(json.loads(issue.payload_json), indent=2, ensure_ascii=False)
    except Exception:
        pass

    return render_template(
        "admin_issue_detail.html",
        issue=issue,
        payload_pretty=payload_pretty,
    )

@app.post("/admin/issues/<int:issue_id>/resolve")
def admin_issue_resolve(issue_id: int):
    guard = admin_guard()
    if guard:
        return guard

    issue = MobileIssueReport.query.get(issue_id)
    if not issue:
        flash("Issue not found.", "error")
        return redirect(url_for("admin_issues"))

    new_status = (request.form.get("status") or "resolved").strip().lower()
    note = (request.form.get("note") or "").strip()

    if new_status not in ("open", "resolved", "ignored"):
        new_status = "resolved"

    issue.status = new_status
    issue.resolve_note = note or None
    issue.resolved_by = admin_username()
    issue.resolved_at = now_utc() if new_status in ("resolved", "ignored") else None

    db.session.commit()
    flash("Issue updated.", "success")
    return redirect(url_for("admin_issue_detail", issue_id=issue.id))

# -------- Bulk Import (stores + employees) --------
@app.route("/admin/import", methods=["GET", "POST"])
def admin_import():
    guard = admin_guard()
    if guard: return guard

    results = None

    if request.method == "POST":
        stores_file = request.files.get("stores_file")
        employees_file = request.files.get("employees_file")

        created_stores = 0
        skipped_stores = 0
        store_errors = []

        created_emps = 0
        skipped_emps = 0
        emp_errors = []

        # ---------- Import STORES ----------
        if stores_file and stores_file.filename:
            try:
                reader = csv.DictReader(TextIOWrapper(stores_file.stream, encoding="utf-8"))
                required = {"name", "qr_token", "latitude", "longitude", "geofence_radius_m"}
                missing_cols = required - set((reader.fieldnames or []))

                if missing_cols:
                    store_errors.append(f"Stores CSV missing columns: {', '.join(sorted(missing_cols))}")
                else:
                    for i, row in enumerate(reader, start=2):
                        try:
                            name = (row.get("name") or "").strip()
                            qr_token = normalize_store_code(row.get("qr_token") or "")
                            lat = row.get("latitude")
                            lng = row.get("longitude")
                            radius = row.get("geofence_radius_m") or "150"

                            if not name or not qr_token or lat is None or lng is None:
                                skipped_stores += 1
                                store_errors.append(f"Stores row {i}: missing name/code/lat/lng")
                                continue

                            lat = float(lat)
                            lng = float(lng)
                            radius = int(float(radius))

                            existing = Store.query.filter(func.lower(Store.qr_token) == qr_token).first()
                            if existing:
                                skipped_stores += 1
                                continue

                            s = Store(
                                name=name,
                                qr_token=qr_token,
                                latitude=lat,
                                longitude=lng,
                                geofence_radius_m=radius
                            )
                            db.session.add(s)
                            created_stores += 1

                        except Exception as e:
                            skipped_stores += 1
                            store_errors.append(f"Stores row {i}: {e}")

                    db.session.commit()

            except Exception as e:
                store_errors.append(str(e))

        # ---------- Import EMPLOYEES ----------
        if employees_file and employees_file.filename:
            try:
                reader = csv.DictReader(TextIOWrapper(employees_file.stream, encoding="utf-8"))
                required = {"name", "pin"}
                missing_cols = required - set((reader.fieldnames or []))

                if missing_cols:
                    emp_errors.append(f"Employees CSV missing columns: {', '.join(sorted(missing_cols))}")
                else:
                    for i, row in enumerate(reader, start=2):
                        try:
                            name = (row.get("name") or "").strip()
                            pin = (row.get("pin") or "").strip()
                            active_raw = (row.get("active") or "1").strip().lower()

                            if not name or not pin:
                                skipped_emps += 1
                                emp_errors.append(f"Employees row {i}: missing name or pin")
                                continue

                            active = active_raw not in ("0", "false", "no", "n")

                            if Employee.query.filter_by(pin=pin).first():
                                skipped_emps += 1
                                continue

                            e = Employee(name=name, pin=pin, active=active)
                            db.session.add(e)
                            created_emps += 1

                        except Exception as e:
                            skipped_emps += 1
                            emp_errors.append(f"Employees row {i}: {e}")

                    db.session.commit()

            except Exception as e:
                emp_errors.append(str(e))

        results = {
            "created_stores": created_stores,
            "skipped_stores": skipped_stores,
            "store_errors": store_errors[:50],
            "created_emps": created_emps,
            "skipped_emps": skipped_emps,
            "emp_errors": emp_errors[:50],
        }

        flash(
            f"Import done. Stores: +{created_stores} (skipped {skipped_stores}). "
            f"Employees: +{created_emps} (skipped {skipped_emps}).",
            "success"
        )

        log_event(
            "ADMIN_IMPORT",
            created_stores=created_stores,
            skipped_stores=skipped_stores,
            created_employees=created_emps,
            skipped_employees=skipped_emps
        )

    return render_template("import.html", results=results)

@app.route("/admin/employees", methods=["GET", "POST"])
def admin_employees():
    guard = admin_guard()
    if guard: return guard

    if request.method == "POST":
        action = request.form.get("action")

        if action == "create":
            name = (request.form.get("name") or "").strip()
            pin = (request.form.get("pin") or "").strip()

            if not name or not pin:
                flash("Name and PIN required.", "error")
            else:
                if Employee.query.filter_by(pin=pin).first():
                    flash("PIN already in use.", "error")
                else:
                    e = Employee(name=name, pin=pin, active=True)
                    db.session.add(e)
                    db.session.commit()
                    flash("Employee created.", "success")

        elif action == "toggle_active":
            emp_id = request.form.get("employee_id")
            emp = Employee.query.get(emp_id)
            if emp:
                emp.active = not emp.active
                db.session.commit()
                flash(f"Employee {'activated' if emp.active else 'deactivated'}.", "success")

    view = (request.args.get("view") or "active").strip().lower()

    q = Employee.query
    if view == "inactive":
        q = q.filter(Employee.active.is_(False))
    elif view == "all":
        pass
    else:
        q = q.filter(Employee.active.is_(True))
        view = "active"

    employees = q.order_by(Employee.name.asc()).all()
    inactive_count = Employee.query.filter(Employee.active.is_(False)).count()

    return render_template(
        "employees.html",
        employees=employees,
        view=view,
        inactive_count=inactive_count
    )

@app.post("/admin/employees/update")
def admin_employees_update():
    guard = admin_guard()
    if guard: return guard

    emp_id = request.form.get("employee_id")
    name = (request.form.get("name") or "").strip()
    pin = (request.form.get("pin") or "").strip()
    active = (request.form.get("active") or "0") == "1"

    emp = Employee.query.get(emp_id)
    if not emp:
        flash("Employee not found.", "error")
        return redirect(url_for("admin_employees"))

    if not name or not pin:
        flash("Name and PIN required.", "error")
        return redirect(url_for("admin_employees"))

    other = Employee.query.filter(Employee.pin == pin, Employee.id != emp.id).first()
    if other:
        flash("That PIN is already in use.", "error")
        return redirect(url_for("admin_employees"))

    emp.name = name
    emp.pin = pin
    emp.active = active
    db.session.commit()

    flash("Employee updated.", "success")
    return redirect(url_for("admin_employees"))

@app.post("/admin/employees/delete")
def admin_employees_delete():
    guard = admin_guard()
    if guard: return guard

    emp_id = request.form.get("employee_id")
    emp = Employee.query.get(emp_id)
    if not emp:
        flash("Employee not found.", "error")
        return redirect(url_for("admin_employees"))

    shift_count = Shift.query.filter_by(employee_id=emp.id).count()
    if shift_count > 0:
        flash("Cannot delete employee with shift history. Deactivate instead.", "error")
        return redirect(url_for("admin_employees"))

    db.session.delete(emp)
    db.session.commit()
    flash("Employee deleted.", "success")
    return redirect(url_for("admin_employees"))

@app.route("/admin/stores", methods=["GET", "POST"])
def admin_stores():
    guard = admin_guard()
    if guard: return guard

    if request.method == "POST":
        action = request.form.get("action")

        if action == "create":
            name = (request.form.get("name") or "").strip()
            qr_token = normalize_store_code(request.form.get("qr_token") or "")
            lat = request.form.get("latitude")
            lng = request.form.get("longitude")
            radius = request.form.get("geofence_radius_m") or "150"

            if not name or not qr_token or not lat or not lng:
                flash("Name, store code, latitude, and longitude required.", "error")
            else:
                try:
                    lat = float(lat)
                    lng = float(lng)
                    radius = int(float(radius))
                except ValueError:
                    flash("Invalid lat/lng/radius.", "error")
                else:
                    existing = Store.query.filter(func.lower(Store.qr_token) == qr_token).first()
                    if existing:
                        flash("Store code already in use.", "error")
                    else:
                        s = Store(
                            name=name,
                            qr_token=qr_token,
                            latitude=lat,
                            longitude=lng,
                            geofence_radius_m=radius
                        )
                        db.session.add(s)
                        db.session.commit()
                        flash("Store created.", "success")

    stores = Store.query.order_by(Store.name.asc()).all()
    return render_template("stores.html", stores=stores)

@app.post("/admin/stores/update")
def admin_stores_update():
    guard = admin_guard()
    if guard: return guard

    store_id = request.form.get("store_id")
    name = (request.form.get("name") or "").strip()
    qr_token = normalize_store_code(request.form.get("qr_token") or "")
    lat = request.form.get("latitude")
    lng = request.form.get("longitude")
    radius = request.form.get("geofence_radius_m") or "150"

    store = Store.query.get(store_id)
    if not store:
        flash("Store not found.", "error")
        return redirect(url_for("admin_stores"))

    if not name or not qr_token or not lat or not lng:
        flash("Name, store code, latitude, and longitude required.", "error")
        return redirect(url_for("admin_stores"))

    try:
        lat = float(lat)
        lng = float(lng)
        radius = int(float(radius))
    except ValueError:
        flash("Invalid lat/lng/radius.", "error")
        return redirect(url_for("admin_stores"))

    existing = Store.query.filter(func.lower(Store.qr_token) == qr_token, Store.id != store.id).first()
    if existing:
        flash("Store code already in use.", "error")
        return redirect(url_for("admin_stores"))

    store.name = name
    store.qr_token = qr_token
    store.latitude = lat
    store.longitude = lng
    store.geofence_radius_m = radius
    db.session.commit()

    flash("Store updated.", "success")
    return redirect(url_for("admin_stores"))

@app.post("/admin/stores/delete")
def admin_stores_delete():
    guard = admin_guard()
    if guard: return guard

    store_id = request.form.get("store_id")
    store = Store.query.get(store_id)
    if not store:
        flash("Store not found.", "error")
        return redirect(url_for("admin_stores"))

    shift_count = Shift.query.filter_by(store_id=store.id).count()
    if shift_count > 0:
        flash("Cannot delete store with shift history.", "error")
        return redirect(url_for("admin_stores"))

    db.session.delete(store)
    db.session.commit()
    flash("Store deleted.", "success")
    return redirect(url_for("admin_stores"))

@app.get("/admin/shifts")
def admin_shifts():
    guard = admin_guard()
    if guard: return guard

    shifts = Shift.query.order_by(
        Shift.clock_out.is_(None).desc(),
        Shift.clock_in.desc()
    ).limit(300).all()

    return render_template("shifts.html", shifts=shifts)

@app.post("/admin/shifts/close")
def admin_close_shift():
    guard = admin_guard()
    if guard: return guard

    shift_id = request.form.get("shift_id")
    s = Shift.query.get(shift_id)
    if not s:
        flash("Shift not found.", "error")
        return redirect(url_for("admin_shifts"))

    if s.clock_out:
        flash("Shift already closed.", "success")
        return redirect(url_for("admin_shifts"))

    s.clock_out = now_utc()
    db.session.commit()
    flash("Shift closed.", "success")
    return redirect(url_for("admin_shifts"))

@app.post("/admin/shifts/force_close")
def admin_force_close_shift():
    guard = admin_guard()
    if guard: return guard

    shift_id = request.form.get("shift_id")
    reason = (request.form.get("reason") or "").strip()

    s = Shift.query.get(shift_id)
    if not s:
        flash("Shift not found.", "error")
        return redirect(url_for("admin_shifts"))

    if s.clock_out:
        flash("Shift already closed.", "success")
        return redirect(url_for("admin_shifts"))

    old_in = s.clock_in
    old_out = s.clock_out

    s.clock_out = now_utc()
    s.closed_by_admin = True
    s.admin_closed_by = admin_username()
    s.admin_closed_at = now_utc()
    s.admin_close_reason = reason or None

    audit = ShiftEditAudit(
        shift_id=s.id,
        action="force_close",
        editor=admin_username(),
        reason=reason or "Force close (no reason provided)",
        old_clock_in=old_in,
        old_clock_out=old_out,
        new_clock_in=s.clock_in,
        new_clock_out=s.clock_out
    )
    db.session.add(audit)
    db.session.commit()

    flash("Shift force-closed (admin override).", "success")
    return redirect(url_for("admin_shifts"))

@app.route("/admin/shifts/new", methods=["GET", "POST"])
def admin_shift_new():
    guard = admin_guard()
    if guard: return guard

    employees = Employee.query.order_by(Employee.active.desc(), Employee.name.asc()).all()
    stores = Store.query.order_by(Store.name.asc()).all()

    if request.method == "POST":
        employee_id = request.form.get("employee_id")
        store_id = request.form.get("store_id")
        clock_in_raw = request.form.get("clock_in")
        clock_out_raw = request.form.get("clock_out")
        reason = (request.form.get("reason") or "").strip()

        if not employee_id or not store_id:
            flash("Employee and store are required.", "error")
            return render_template("admin_shift_new.html", employees=employees, stores=stores)

        if not reason:
            flash("Reason is required for manual shift creation.", "error")
            return render_template("admin_shift_new.html", employees=employees, stores=stores)

        cin = parse_local_datetime(clock_in_raw)
        cout = parse_local_datetime(clock_out_raw) if clock_out_raw else None

        if not cin:
            flash("Clock-in is required and must be valid.", "error")
            return render_template("admin_shift_new.html", employees=employees, stores=stores)

        if cout and cout <= cin:
            flash("Clock-out must be after clock-in.", "error")
            return render_template("admin_shift_new.html", employees=employees, stores=stores)

        s = Shift(
            employee_id=int(employee_id),
            store_id=int(store_id),
            clock_in=cin,
            clock_out=cout,
            closed_by_admin=True,
            admin_closed_by=admin_username(),
            admin_closed_at=now_utc(),
            admin_close_reason=reason
        )
        db.session.add(s)
        db.session.commit()

        audit = ShiftEditAudit(
            shift_id=s.id,
            action="create",
            editor=admin_username(),
            reason=reason,
            old_clock_in=None,
            old_clock_out=None,
            new_clock_in=s.clock_in,
            new_clock_out=s.clock_out
        )
        db.session.add(audit)
        db.session.commit()

        flash("Manual shift created.", "success")
        return redirect(url_for("admin_shifts"))

    return render_template("admin_shift_new.html", employees=employees, stores=stores)

@app.route("/admin/shifts/<int:shift_id>/edit", methods=["GET", "POST"])
def admin_shift_edit(shift_id: int):
    guard = admin_guard()
    if guard: return guard

    s = Shift.query.get(shift_id)
    if not s:
        flash("Shift not found.", "error")
        return redirect(url_for("admin_shifts"))

    employees = Employee.query.order_by(Employee.active.desc(), Employee.name.asc()).all()
    stores = Store.query.order_by(Store.name.asc()).all()

    if request.method == "POST":
        employee_id = request.form.get("employee_id")
        store_id = request.form.get("store_id")
        clock_in_raw = request.form.get("clock_in")
        clock_out_raw = request.form.get("clock_out")
        reason = (request.form.get("reason") or "").strip()

        if not reason:
            flash("Reason is required for shift edits.", "error")
            return render_template("admin_shift_edit.html", s=s, employees=employees, stores=stores)

        cin = parse_local_datetime(clock_in_raw)
        cout = parse_local_datetime(clock_out_raw) if clock_out_raw else None

        if not cin:
            flash("Clock-in must be valid.", "error")
            return render_template("admin_shift_edit.html", s=s, employees=employees, stores=stores)

        if cout and cout <= cin:
            flash("Clock-out must be after clock-in.", "error")
            return render_template("admin_shift_edit.html", s=s, employees=employees, stores=stores)

        old_in = s.clock_in
        old_out = s.clock_out

        s.employee_id = int(employee_id) if employee_id else s.employee_id
        s.store_id = int(store_id) if store_id else s.store_id
        s.clock_in = cin
        s.clock_out = cout

        s.closed_by_admin = True
        s.admin_closed_by = admin_username()
        s.admin_closed_at = now_utc()
        s.admin_close_reason = reason

        audit = ShiftEditAudit(
            shift_id=s.id,
            action="edit",
            editor=admin_username(),
            reason=reason,
            old_clock_in=old_in,
            old_clock_out=old_out,
            new_clock_in=s.clock_in,
            new_clock_out=s.clock_out
        )
        db.session.add(audit)
        db.session.commit()

        flash("Shift updated (audit logged).", "success")
        return redirect(url_for("admin_shifts"))

    return render_template("admin_shift_edit.html", s=s, employees=employees, stores=stores)

@app.get("/admin/audit")
def admin_audit():
    guard = admin_guard()
    if guard: return guard

    audits = ShiftEditAudit.query.order_by(ShiftEditAudit.created_at.desc()).limit(500).all()
    return render_template("admin_audit.html", audits=audits)

# -----------------------------
# ✅ Payroll (unchanged)
# -----------------------------
@app.get("/admin/payroll")
def admin_payroll():
    guard = admin_guard()
    if guard: return guard

    start_str = request.args.get("start")
    end_str = request.args.get("end")
    out_format = (request.args.get("format") or "").lower()

    if start_str and end_str:
        try:
            start_date = datetime.strptime(start_str, "%Y-%m-%d").date()
            end_date = datetime.strptime(end_str, "%Y-%m-%d").date()
            if APP_TZ:
                start_dt = datetime.combine(start_date, dtime.min, tzinfo=APP_TZ)
                end_dt = datetime.combine(end_date, dtime.max, tzinfo=APP_TZ)
            else:
                start_dt = datetime.combine(start_date, dtime.min)
                end_dt = datetime.combine(end_date, dtime.max)
        except ValueError:
            flash("Invalid start/end date format. Use YYYY-MM-DD.", "error")
            start_dt, end_dt = last_completed_payroll_week()
    else:
        start_dt, end_dt = last_completed_payroll_week()

    q_start, q_end = local_range_to_utc_naive(start_dt, end_dt)

    shifts = Shift.query.filter(
        Shift.clock_out.isnot(None),
        Shift.clock_out >= q_start,
        Shift.clock_out <= q_end
    ).order_by(Shift.clock_out.asc()).all()

    rows = []
    totals_by_emp_min = {}
    weekly_map: dict[str, dict[int, dict[str, int]]] = {}

    for s in shifts:
        mins = shift_minutes(s)
        emp_name = s.employee.name
        store_name = s.store.name

        rows.append({
            "employee": emp_name,
            "store": store_name,
            "clock_in": fmt_dt(s.clock_in),
            "clock_out": fmt_dt(s.clock_out),
            "minutes": mins,
            "human_short": minutes_to_short(mins),
        })
        totals_by_emp_min[emp_name] = totals_by_emp_min.get(emp_name, 0) + mins

        cin_local = utc_naive_to_local(s.clock_in)
        wd = cin_local.weekday()  # Mon=0 ... Sun=6

        if emp_name not in weekly_map:
            weekly_map[emp_name] = {}
        if wd not in weekly_map[emp_name]:
            weekly_map[emp_name][wd] = {}
        weekly_map[emp_name][wd][store_name] = weekly_map[emp_name][wd].get(store_name, 0) + mins

    summary = []
    for emp_name in sorted(totals_by_emp_min.keys(), key=lambda x: x.lower()):
        m = totals_by_emp_min[emp_name]
        summary.append({
            "employee": emp_name,
            "minutes": m,
            "human": minutes_to_human(m),
            "human_short": minutes_to_short(m),
            "hours_decimal": minutes_to_decimal_hours(m, places=4),
        })

    grand_minutes = sum(totals_by_emp_min.values())
    grand_human = minutes_to_human(grand_minutes)
    grand_human_short = minutes_to_short(grand_minutes)
    grand_hours_decimal = minutes_to_decimal_hours(grand_minutes, places=4)

    if out_format == "csv":
        from io import StringIO

        si = StringIO()
        w = csv.writer(si)

        w.writerow(["Payroll Week Start (local)", start_dt.date().isoformat()])
        w.writerow(["Payroll Week End (local)", end_dt.date().isoformat()])
        w.writerow(["Note", "Weekly filter uses CLOCK-OUT date; day columns assign time to CLOCK-IN day (local)."])
        w.writerow([])

        day_headers = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        w.writerow(["Employee"] + day_headers + ["Total"])

        for emp_name in sorted(weekly_map.keys(), key=lambda x: x.lower()):
            day_cells = []
            total_emp = 0

            for wd in range(7):
                stores_for_day = weekly_map.get(emp_name, {}).get(wd, {})
                if not stores_for_day:
                    day_cells.append("0h 00m")
                    continue

                parts = []
                for store_name in sorted(stores_for_day.keys(), key=lambda x: x.lower()):
                    m = stores_for_day[store_name]
                    total_emp += m
                    parts.append(f"{store_name} {minutes_to_short(m)}")

                day_cells.append("; ".join(parts))

            w.writerow([emp_name] + day_cells + [minutes_to_short(total_emp)])

        w.writerow(["GRAND TOTAL"] + [""] * 7 + [grand_human_short])
        w.writerow([])

        w.writerow(["Shift Detail"])
        w.writerow(["Employee", "Store", "Clock In", "Clock Out", "Minutes", "Time (Short)"])
        for r in rows:
            w.writerow([r["employee"], r["store"], r["clock_in"], r["clock_out"], r["minutes"], r["human_short"]])

        output = si.getvalue()
        filename = f"payroll_{start_dt.date().isoformat()}_to_{end_dt.date().isoformat()}.csv"
        return Response(
            output,
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    if out_format == "xlsx":
        from io import BytesIO

        wb = Workbook()
        header_font = Font(bold=True)
        wrap = Alignment(wrap_text=True, vertical="top")

        ws = wb.active
        ws.title = "Weekly"

        ws.append(["Payroll Week Start (local)", start_dt.date().isoformat()])
        ws.append(["Payroll Week End (local)", end_dt.date().isoformat()])
        ws.append(["Note", "Weekly filter uses CLOCK-OUT date; day columns assign time to CLOCK-IN day (local)."])
        ws.append([])

        headers = ["Employee", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun", "Total"]
        ws.append(headers)

        for col_idx in range(1, len(headers) + 1):
            c = ws.cell(row=ws.max_row, column=col_idx)
            c.font = header_font
            c.alignment = wrap

        for emp_name in sorted(weekly_map.keys(), key=lambda x: x.lower()):
            day_cells = []
            total_emp = 0

            for wd in range(7):
                stores_for_day = weekly_map.get(emp_name, {}).get(wd, {})
                if not stores_for_day:
                    day_cells.append("0h 00m")
                    continue

                parts = []
                for store_name in sorted(stores_for_day.keys(), key=lambda x: x.lower()):
                    m = stores_for_day[store_name]
                    total_emp += m
                    parts.append(f"{store_name} {minutes_to_short(m)}")

                day_cells.append("; ".join(parts))

            ws.append([emp_name] + day_cells + [minutes_to_short(total_emp)])

        ws.append(["GRAND TOTAL", "", "", "", "", "", "", "", grand_human_short])

        max_col = len(headers)
        for row in ws.iter_rows(min_row=1, max_row=ws.max_row, min_col=1, max_col=max_col):
            for cell in row:
                cell.alignment = wrap

        for col_idx in range(1, max_col + 1):
            ws.column_dimensions[get_column_letter(col_idx)].width = 25

        ws.freeze_panes = "A6"

        ws2 = wb.create_sheet("Shift Detail")
        detail_headers = ["Employee", "Store", "Clock In", "Clock Out", "Minutes", "Time (Short)"]
        ws2.append(detail_headers)

        for col_idx in range(1, len(detail_headers) + 1):
            c = ws2.cell(row=1, column=col_idx)
            c.font = header_font
            c.alignment = wrap

        for r in rows:
            ws2.append([r["employee"], r["store"], r["clock_in"], r["clock_out"], r["minutes"], r["human_short"]])

        max_col2 = len(detail_headers)
        for row in ws2.iter_rows(min_row=1, max_row=ws2.max_row, min_col=1, max_col=max_col2):
            for cell in row:
                cell.alignment = wrap

        for col_idx in range(1, max_col2 + 1):
            ws2.column_dimensions[get_column_letter(col_idx)].width = 25

        ws2.freeze_panes = "A2"

        bio = BytesIO()
        wb.save(bio)
        bio.seek(0)

        filename = f"payroll_{start_dt.date().isoformat()}_to_{end_dt.date().isoformat()}.xlsx"
        return Response(
            bio.getvalue(),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    day_headers = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    grid_rows = []

    for emp_name in sorted(weekly_map.keys(), key=lambda x: x.lower()):
        day_cells = []
        total_emp = 0

        for wd in range(7):
            stores_for_day = weekly_map.get(emp_name, {}).get(wd, {})
            if not stores_for_day:
                day_cells.append("0h 00m")
                continue

            parts = []
            for store_name in sorted(stores_for_day.keys(), key=lambda x: x.lower()):
                m = stores_for_day[store_name]
                total_emp += m
                parts.append(f"{store_name} {minutes_to_short(m)}")

            day_cells.append("; ".join(parts))

        grid_rows.append({
            "employee": emp_name,
            "days": day_cells,
            "total": minutes_to_short(total_emp),
        })

    return render_template(
        "payroll.html",
        start=start_dt.date().isoformat(),
        end=end_dt.date().isoformat(),
        summary=summary,
        rows=rows,
        day_headers=day_headers,
        grid_rows=grid_rows,
        grand_minutes=grand_minutes,
        grand_human=grand_human,
        grand_human_short=grand_human_short,
        grand_hours_decimal=grand_hours_decimal
    )

# ✅ Backwards-compatible alias for old Reports link
@app.get("/admin/reports/hours")
def admin_reports_hours_redirect():
    guard = admin_guard()
    if guard:
        return guard

    args = request.args.to_dict(flat=True)
    return redirect(url_for("admin_payroll", **args))

# -----------------------------
# Index
# -----------------------------
@app.get("/")
def index():
    return redirect(url_for("employee_page"))

# -----------------------------
# Run (local only)
# -----------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)