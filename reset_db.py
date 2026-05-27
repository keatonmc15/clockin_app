import os
import psycopg2

DATABASE_URL = (os.environ.get("DATABASE_URL") or "").strip()
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is required")

print("Connecting to database...")
conn = psycopg2.connect(DATABASE_URL)
conn.autocommit = True
cur = conn.cursor()

sql = """
DROP TABLE IF EXISTS shifts CASCADE;
DROP TABLE IF EXISTS stores CASCADE;
DROP TABLE IF EXISTS employees CASCADE;

DROP TABLE IF EXISTS shift CASCADE;
DROP TABLE IF EXISTS store CASCADE;
DROP TABLE IF EXISTS employee CASCADE;
"""

print("Dropping tables...")
cur.execute(sql)

cur.close()
conn.close()

print("✅ Database reset complete.")
