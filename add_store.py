import os
import psycopg2

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise SystemExit("DATABASE_URL not set.")

conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

name = input("Store name: ")
qr = input("QR code token (just make up something unique): ")
lat = float(input("Store latitude: "))
lng = float(input("Store longitude: "))
radius = int(input("Geofence radius in meters (ex: 200): "))

cur.execute("""
    INSERT INTO stores (name, qr_code_token, latitude, longitude, geofence_radius_meters)
    VALUES (%s, %s, %s, %s, %s)
    RETURNING id
""", (name, qr, lat, lng, radius))

store_id = cur.fetchone()[0]
conn.commit()

print(f"Store created with ID: {store_id}")

cur.close()
conn.close()
