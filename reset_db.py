import os
import psycopg2

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://candcjan:WGx3HkvQQbM6gQ2sl0b8RN54oSohbcMd@dpg-d4s90akcjiac73fdc730-a.ohio-postgres.render.com/clockin_db_ht20"
)

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
