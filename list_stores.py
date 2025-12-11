import os
import psycopg2

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise SystemExit("DATABASE_URL not set. Run `set DATABASE_URL=...` first.")

conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

cur.execute("""
    SELECT id, name, qr_code_token, latitude, longitude, geofence_radius_meters
    FROM stores
    ORDER BY id;
""")

rows = cur.fetchall()

if not rows:
    print("No stores found.")
else:
    print("Stores:")
    for row in rows:
        store_id, name, token, lat, lng, radius = row
        print(f"- ID {store_id}: {name}")
        print(f"    token: {token}")
        print(f"    lat/lng: {lat}, {lng}")
        print(f"    radius: {radius} m")
        print()

cur.close()
conn.close()
