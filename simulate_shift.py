import requests
import time

# -----------------------------
# CONFIG
# -----------------------------
BASE_URL = "http://127.0.0.1:5000"
STORE_TOKEN = "reasors_s_ba"
EMPLOYEE_PIN = "1234"   # <-- use a real employee PIN

# 🏢 YOUR OFFICE LOCATION (TESTING)
LAT = 36.0539507832785
LNG = -95.81225207639432

# -----------------------------
# CLOCK IN
# -----------------------------
print("Clocking in...")

resp = requests.post(
    f"{BASE_URL}/api/clock-in",
    json={
        "store_token": STORE_TOKEN,
        "pin": EMPLOYEE_PIN,
        "lat": LAT,
        "lng": LNG
    }
)

if resp.status_code != 200:
    print("❌ Clock-in failed:", resp.json())
    exit()

data = resp.json()
shift_id = data["shift_id"]

print("✅ Clocked in")
print("Shift ID:", shift_id)
print("Employee:", data["employee_name"])
print("Store:", data["store_name"])

# -----------------------------
# GPS PINGS (simulate staying on site)
# -----------------------------
print("\nSending GPS pings...")

for i in range(5):
    requests.post(
        f"{BASE_URL}/api/location-ping",
        json={
            "shift_id": shift_id,
            "lat": LAT,
            "lng": LNG
        }
    )
    time.sleep(1)

print("✅ GPS pings sent")

# -----------------------------
# CLOCK OUT
# -----------------------------
print("\nClocking out...")

resp = requests.post(
    f"{BASE_URL}/api/clock-out",
    json={
        "shift_id": shift_id,
        "lat": LAT,
        "lng": LNG
    }
)

if resp.status_code == 200:
    print("✅ Clock-out successful:", resp.json())
else:
    print("❌ Clock-out failed:", resp.json())
