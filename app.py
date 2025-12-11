from flask import Flask, request, render_template, jsonify
from datetime import datetime
import math
import os
import psycopg2

app = Flask(__name__)

# --- DB CONNECTION ---
def get_db_conn():
    return psycopg2.connect(os.getenv("DATABASE_URL"))

def distance_meters(lat1, lon1, lat2, lon2):
    R = 6371000  # meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (math.sin(dphi/2)**2 +
         math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2)
    c = 2*math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R*c

@app.route("/")
def index():
    return "Clock-in system running."

@app.route("/scan")
def scan_page():
    store_token = request.args.get("store")
    if not store_token:
        return "Missing store token.", 400
    return render_template("scan.html", store_token=store_token)

@app.route("/api/clock-in", methods=["POST"])
def clock_in():
    data = request.json
    store_token = data["store_token"]
    pin = data["pin"]
    lat = float(data["lat"])
    lng = float(data["lng"])

    conn = get_db_conn()
    cur = conn.cursor()

    # Get store by token
    cur.execute("SELECT id, name, latitude, longitude, geofence_radius_meters FROM stores WHERE qr_code_token = %s", (store_token,))
    row = cur.fetchone()
    if not row:
        return jsonify({"error": "Invalid store QR"}), 400
    store_id, store_name, s_lat, s_lng, radius = row

    # Get employee by PIN
    cur.execute("SELECT id, name FROM employees WHERE pin = %s", (pin,))
    emp = cur.fetchone()
    if not emp:
        return jsonify({"error": "Invalid PIN"}), 400
    employee_id, emp_name = emp

    # Check for open shift
    cur.execute("SELECT id FROM shifts WHERE employee_id = %s AND store_id = %s AND status = 'open'",
                (employee_id, store_id))
    if cur.fetchone():
        return jsonify({"error": "You already have an open shift here."}), 400

    # Geofence check
    dist = distance_meters(lat, lng, s_lat, s_lng)
    if dist > radius:
        return jsonify({"error": "You are not at the store location."}), 400

    # Create shift
    now = datetime.utcnow()
    cur.execute("""
        INSERT INTO shifts (employee_id, store_id, clock_in_time, clock_in_lat, clock_in_lng, status)
        VALUES (%s, %s, %s, %s, %s, 'open') RETURNING id
    """, (employee_id, store_id, now, lat, lng))
    shift_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"shift_id": shift_id, "employee_name": emp_name, "store_name": store_name})

@app.route("/api/location-ping", methods=["POST"])
def location_ping():
    data = request.json
    shift_id = data["shift_id"]
    lat = float(data["lat"])
    lng = float(data["lng"])

    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO location_logs (shift_id, timestamp, lat, lng) VALUES (%s, %s, %s, %s)",
                (shift_id, datetime.utcnow(), lat, lng))
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"status": "ok"})
    
@app.route("/admin")
def admin_dashboard():
    """
    Simple dashboard to show recent shifts.
    If something goes wrong, show the error text in the browser.
    """
    try:
        conn = get_db()
        cur = conn.cursor()

        # Simple query — avoids joins until everything works
        cur.execute(
            """
            SELECT
                id,
                employee_id,
                store_id,
                clock_in_time,
                clock_out_time,
                status
            FROM shifts
            ORDER BY clock_in_time DESC NULLS LAST, id DESC
            LIMIT 100;
            """
        )

        rows = cur.fetchall()
        cur.close()
        conn.close()

        shifts = []
        for row in rows:
            shifts.append(
                {
                    "id": row[0],
                    "employee_name": f"Employee #{row[1]}",
                    "store_name": f"Store #{row[2]}",
                    "clock_in_time": row[3],
                    "clock_out_time": row[4],
                    "status": row[5],
                }
            )

        return render_template("admin.html", shifts=shifts)

    except Exception as e:
        # Show full error in the browser so we know what's wrong
        return f"Admin error: {repr(e)}", 500


if __name__ == "__main__":
    app.run(debug=True)






