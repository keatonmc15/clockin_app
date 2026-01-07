import os
import math
import logging
import csv
from io import TextIOWrapper
from datetime import datetime, timedelta, time as dtime

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify, Response
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import func

# -----------------------------
# Timezone (Windows-safe)
# -----------------------------
APP_TZ = None
try:
    from zoneinfo import ZoneInfo
    try:
        APP_TZ = ZoneInfo("America/Chicago")
    except Exception:
        APP_TZ = None
except Exception:
    APP_TZ = None

# -----------------------------
# App + Config
# -----------------------------
app = Flask(__name__)

# Basic INFO logging (Render captures these)
logging.basicConfig(level=logging.INFO)

app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev_secret_change_me")

db_url = os.environ.get("DATABASE_URL", "sqlite:///clockin.db")

# Normalize Render postgres URL
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)

# FORCE psycopg v3 (required for Python 3.13)
if db_url.startswith("postgresql://"):
    db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)

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

    created_at = db.Column(db.DateTime, default=lambda: now_tz())


class Employee(db.Model):
    __tablename__ = "employees"
    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(db.String(120), nullable=False)
    pin = db.Column(db.String(20), nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)

    created_at = db.Column(db.DateTime, default=lambda: now_tz())


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

    # --- Admin override audit fields (B) ---
    closed_by_admin = db.Column(db.Boolean, nullable=False, default=False)
    admin_closed_by = db.Column(db.String(120), nullable=True)   # username
    admin_closed_at = db.Column(db.DateTime, nullable=True)
    admin_close_reason = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, default=lambda: now_tz())

    employee = db.relationship("Employee", backref="shifts")
    store = db.relationship("Store", backref="shifts")


# ✅ NEW: Location pings (15-min tracking)
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

    created_at = db.Column(db.DateTime, default=lambda: now_tz(), nullable=False)

    employee = db.relationship("Employee")
    shift = db.relationship("Shift")
    store = db.relationship("Store")


# -----------------------------
# Helpers
# -----------------------------
def now_tz() -> datetime:
    if APP_TZ:
        return datetime.now(APP_TZ)
    return datetime.now()

def fmt_dt(dt: datetime | None) -> str:
    if not dt:
        return ""
    try:
        if APP_TZ and getattr(dt, "tzinfo", None):
            dt = dt.astimezone(APP_TZ)
    except Exception:
        pass
    return dt.strftime("%Y-%m-%d %I:%M %p")

def haversine_m(lat1, lon1, lat2, lon2) -> float:
    R = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)

    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dl / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def shift_hours(shift: "Shift") -> float:
    if not shift.clock_in or not shift.clock_out:
        return 0.0
    seconds = (shift.clock_out - shift.clock_in).total_seconds()
    return max(0.0, seconds / 3600.0)

def last_completed_payroll_week(reference: datetime | None = None):
    ref = reference or now_tz()
    weekday = ref.weekday()  # Monday=0
    this_monday = ref.date() - timedelta(days=weekday)
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

# ✅ Canonical store codes = lowercase
def normalize_store_code(val: str) -> str:
    return (val or "").strip().lower()

def log_event(event: str, **fields):
    parts = [f"{k}={fields[k]}" for k in sorted(fields.keys())]
    app.logger.info("%s %s", event, " ".join(parts))


# -----------------------------
# Fingerprint (DEBUG)
# -----------------------------
@app.get("/__fingerprint__")
def fingerprint():
    return "clockin_app LIVE fingerprint 2025-12-18"


# -----------------------------
# Optional: favicon
# -----------------------------
@app.get("/favicon.ico")
def favicon():
    return ("", 204)


# -----------------------------
# Employee Clock Page
# -----------------------------
@app.get("/employee")
def employee_page():
    stores = Store.query.order_by(Store.name.asc()).all()
    # send codes as stored (now canonical lowercase)
    stores_min = [{"name": s.name, "code": s.qr_token} for s in stores]
    return render_template("employee_clock.html", stores=stores_min)


# -----------------------------
# Employee API (Clock In/Out)
# -----------------------------
@app.post("/api/clockin")
def api_clockin():
    data = request.get_json(force=True, silent=True) or {}

    pin = (data.get("pin") or "").strip()
    qr_token_raw = (data.get("qr_token") or "").strip()
    qr_token = normalize_store_code(qr_token_raw)
    lat = data.get("lat")
    lng = data.get("lng")

    if not pin or not qr_token:
        return jsonify({"error": "Missing PIN or store code."}), 400

    emp = Employee.query.filter_by(pin=pin).first()
    if not emp or not emp.active:
        return jsonify({"error": "Invalid or inactive employee."}), 403

    # ✅ case-insensitive match using lower()
    store = Store.query.filter(func.lower(Store.qr_token) == qr_token).first()
    if not store:
        log_event("CLOCKIN_DENY_INVALID_STORE", employee_pin=pin, store_code=qr_token)
        return jsonify({"error": "Invalid store code."}), 404

    open_shift = Shift.query.filter_by(employee_id=emp.id, clock_out=None).order_by(Shift.clock_in.desc()).first()
    if open_shift:
        log_event("CLOCKIN_DENY_ALREADY_CLOCKED_IN", employee_id=emp.id, open_shift_id=open_shift.id)
        return jsonify({"error": "You are already clocked in. Please clock out first."}), 409

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
    )

    if dist_m > store.geofence_radius_m:
        log_event(
        "CLOCKIN_DENY_OUTSIDE_RADIUS",
            employee_id=emp.id,
            store_id=store.id,
            dist_m=round(dist_m, 1),
            radius_m=store.geofence_radius_m,
        )
        return jsonify({"error": "You are not at the store location."}), 403

    s = Shift(
        employee_id=emp.id,
        store_id=store.id,
        clock_in=now_tz(),
        clock_in_lat=lat,
        clock_in_lng=lng,
        closed_by_admin=False,
        admin_closed_by=None,
        admin_closed_at=None,
        admin_close_reason=None,
    )
    db.session.add(s)
    db.session.commit()

    log_event("CLOCKIN_OK", employee_id=emp.id, shift_id=s.id, store_id=store.id)

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
    )

    if dist_m > store.geofence_radius_m:
        log_event(
            "CLOCKOUT_DENY_OUTSIDE_RADIUS",
            employee_id=emp.id,
            shift_id=open_shift.id,
            store_id=store.id,
            dist_m=round(dist_m, 1),
            radius_m=store.geofence_radius_m,
        )
        return jsonify({"error": "You are not at the store location."}), 403

    open_shift.clock_out = now_tz()
    open_shift.clock_out_lat = lat
    open_shift.clock_out_lng = lng
    db.session.commit()

    hours = shift_hours(open_shift)
    log_event("CLOCKOUT_OK", employee_id=emp.id, shift_id=open_shift.id, hours=round(hours, 2))

    return jsonify({
        "ok": True,
        "employee": emp.name,
        "message": f"Clock-out successful for {emp.name}.",
        "shift_id": open_shift.id,
        "clock_out": fmt_dt(open_shift.clock_out),
        "hours": round(hours, 2),
    })


# ✅ NEW: 15-minute location ping endpoint (called by employee_clock.html)
@app.post("/api/ping")
def api_ping():
    data = request.get_json(force=True, silent=True) or {}

    pin = (data.get("pin") or "").strip()
    lat = data.get("lat")
    lng = data.get("lng")

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

    ping = LocationPing(
        employee_id=emp.id,
        shift_id=open_shift.id,
        store_id=store.id,
        lat=lat,
        lng=lng,
        dist_m=float(dist_m),
        inside_radius=bool(inside),
    )
    db.session.add(ping)
    db.session.commit()

    log_event(
        "PING_OK",
        employee_id=emp.id,
        shift_id=open_shift.id,
        store_id=store.id,
        dist_m=round(dist_m, 1),
        inside=inside
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
            return redirect(url_for("admin_dashboard"))

        flash("Invalid username or password.", "danger")

    return render_template("login.html")

@app.get("/admin/logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    flash("Logged out.", "info")
    return redirect(url_for("admin_login"))


# -----------------------------
# Admin Pages
# -----------------------------
@app.get("/admin")
def admin_dashboard():
    guard = admin_guard()
    if guard: return guard

    open_shifts = Shift.query.filter_by(clock_out=None).count()
    employees = Employee.query.count()
    stores = Store.query.count()
    last7 = now_tz() - timedelta(days=7)
    shifts_7d = Shift.query.filter(Shift.clock_in >= last7).count()

    return render_template(
        "admin_dashboard.html",
        open_shifts=open_shifts,
        employees=employees,
        stores=stores,
        shifts_7d=shifts_7d,
    )

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

                            # ✅ case-insensitive duplicate check
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
                flash("Name and PIN required.", "danger")
            else:
                if Employee.query.filter_by(pin=pin).first():
                    flash("PIN already in use.", "danger")
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

    employees = Employee.query.order_by(Employee.active.desc(), Employee.name.asc()).all()
    return render_template("employees.html", employees=employees)

# ---- NEW: Employee update/delete ----
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
        flash("Employee not found.", "danger")
        return redirect(url_for("admin_employees"))

    if not name or not pin:
        flash("Name and PIN required.", "danger")
        return redirect(url_for("admin_employees"))

    other = Employee.query.filter(Employee.pin == pin, Employee.id != emp.id).first()
    if other:
        flash("That PIN is already in use.", "danger")
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
        flash("Employee not found.", "danger")
        return redirect(url_for("admin_employees"))

    shift_count = Shift.query.filter_by(employee_id=emp.id).count()
    if shift_count > 0:
        flash("Cannot delete employee with shift history. Deactivate instead.", "warning")
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
            qr_token_raw = (request.form.get("qr_token") or "")
            qr_token = normalize_store_code(qr_token_raw)
            lat = request.form.get("latitude")
            lng = request.form.get("longitude")
            radius = request.form.get("geofence_radius_m") or "150"

            if not name or not qr_token or not lat or not lng:
                flash("Name, store code, latitude, and longitude required.", "danger")
            else:
                try:
                    lat = float(lat)
                    lng = float(lng)
                    radius = int(float(radius))
                except ValueError:
                    flash("Invalid lat/lng/radius.", "danger")
                else:
                    existing = Store.query.filter(func.lower(Store.qr_token) == qr_token).first()
                    if existing:
                        flash("Store code already in use.", "danger")
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

# ---- NEW: Store update/delete ----
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
        flash("Store not found.", "danger")
        return redirect(url_for("admin_stores"))

    if not name or not qr_token or not lat or not lng:
        flash("Name, store code, latitude, and longitude required.", "danger")
        return redirect(url_for("admin_stores"))

    try:
        lat = float(lat)
        lng = float(lng)
        radius = int(float(radius))
    except ValueError:
        flash("Invalid lat/lng/radius.", "danger")
        return redirect(url_for("admin_stores"))

    existing = Store.query.filter(func.lower(Store.qr_token) == qr_token, Store.id != store.id).first()
    if existing:
        flash("Store code already in use.", "danger")
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
        flash("Store not found.", "danger")
        return redirect(url_for("admin_stores"))

    shift_count = Shift.query.filter_by(store_id=store.id).count()
    if shift_count > 0:
        flash("Cannot delete store with shift history.", "warning")
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

    return render_template("shifts.html", shifts=shifts, fmt_dt=fmt_dt, shift_hours=shift_hours)

@app.post("/admin/shifts/close")
def admin_close_shift():
    guard = admin_guard()
    if guard: return guard

    shift_id = request.form.get("shift_id")
    s = Shift.query.get(shift_id)
    if not s:
        flash("Shift not found.", "danger")
        return redirect(url_for("admin_shifts"))

    if s.clock_out:
        flash("Shift already closed.", "info")
        return redirect(url_for("admin_shifts"))

    s.clock_out = now_tz()
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
        flash("Shift not found.", "danger")
        return redirect(url_for("admin_shifts"))

    if s.clock_out:
        flash("Shift already closed.", "info")
        return redirect(url_for("admin_shifts"))

    s.clock_out = now_tz()
    s.closed_by_admin = True
    s.admin_closed_by = ADMIN_USERNAME
    s.admin_closed_at = now_tz()
    s.admin_close_reason = reason or None

    db.session.commit()

    flash("Shift force-closed (admin override).", "warning")
    return redirect(url_for("admin_shifts"))

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
            flash("Invalid start/end date format. Use YYYY-MM-DD.", "danger")
            start_dt, end_dt = last_completed_payroll_week()
    else:
        start_dt, end_dt = last_completed_payroll_week()

    shifts = Shift.query.filter(
        Shift.clock_out.isnot(None),
        Shift.clock_out >= start_dt,
        Shift.clock_out <= end_dt
    ).order_by(Shift.clock_out.asc()).all()

    rows = []
    totals_by_emp = {}

    for s in shifts:
        hrs = shift_hours(s)
        emp_name = s.employee.name
        store_name = s.store.name

        rows.append({
            "employee": emp_name,
            "store": store_name,
            "clock_in": fmt_dt(s.clock_in),
            "clock_out": fmt_dt(s.clock_out),
            "hours": round(hrs, 2),
        })
        totals_by_emp[emp_name] = totals_by_emp.get(emp_name, 0.0) + hrs

    summary = [{"employee": k, "hours": round(v, 2)} for k, v in sorted(totals_by_emp.items(), key=lambda x: x[0].lower())]
    grand_total = round(sum(totals_by_emp.values()), 2)

    if out_format == "csv":
        from io import StringIO

        si = StringIO()
        w = csv.writer(si)

        w.writerow(["Payroll Week Start", start_dt.date().isoformat()])
        w.writerow(["Payroll Week End", end_dt.date().isoformat()])
        w.writerow([])

        w.writerow(["Employee", "Total Hours"])
        for item in summary:
            w.writerow([item["employee"], item["hours"]])
        w.writerow(["GRAND TOTAL", grand_total])
        w.writerow([])

        w.writerow(["Employee", "Store", "Clock In", "Clock Out", "Hours"])
        for r in rows:
            w.writerow([r["employee"], r["store"], r["clock_in"], r["clock_out"], r["hours"]])

        output = si.getvalue()
        filename = f"payroll_{start_dt.date().isoformat()}_to_{end_dt.date().isoformat()}.csv"
        return Response(
            output,
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    return render_template(
        "payroll.html",
        start=start_dt.date().isoformat(),
        end=end_dt.date().isoformat(),
        summary=summary,
        rows=rows,
        grand_total=grand_total
    )


# -----------------------------
# Index
# -----------------------------
@app.get("/")
def index():
    return redirect(url_for("employee_page"))


# -----------------------------
# Run
# -----------------------------
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
