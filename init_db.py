import os
import psycopg2

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise SystemExit("DATABASE_URL is not set. Run `set DATABASE_URL=...` first.")

create_tables_sql = """
CREATE TABLE IF NOT EXISTS employees (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    pin VARCHAR(10) NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS stores (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    qr_code_token VARCHAR(50) NOT NULL UNIQUE,
    latitude DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    geofence_radius_meters INT NOT NULL DEFAULT 200
);

CREATE TABLE IF NOT EXISTS shifts (
    id SERIAL PRIMARY KEY,
    employee_id INT REFERENCES employees(id),
    store_id INT REFERENCES stores(id),
    clock_in_time TIMESTAMP,
    clock_in_lat DOUBLE PRECISION,
    clock_in_lng DOUBLE PRECISION,
    clock_out_time TIMESTAMP,
    clock_out_lat DOUBLE PRECISION,
    clock_out_lng DOUBLE PRECISION,
    status VARCHAR(20) NOT NULL DEFAULT 'open'
);

CREATE TABLE IF NOT EXISTS location_logs (
    id SERIAL PRIMARY KEY,
    shift_id INT REFERENCES shifts(id),
    timestamp TIMESTAMP NOT NULL,
    lat DOUBLE PRECISION NOT NULL,
    lng DOUBLE PRECISION NOT NULL
);
"""

def main():
    print("Connecting to database...")
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    cur = conn.cursor()
    print("Creating tables (if not exist)...")
    cur.execute(create_tables_sql)
    cur.close()
    conn.close()
    print("Done! Tables created.")

if __name__ == "__main__":
    main()
