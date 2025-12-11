import os
import psycopg2

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise SystemExit("DATABASE_URL not set. Run `set DATABASE_URL=...` first.")

conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

cur.execute("SELECT id, name, pin FROM employees ORDER BY id;")
rows = cur.fetchall()

if not rows:
    print("No employees found.")
else:
    print("Employees:")
    for emp_id, name, pin in rows:
        print(f"- ID {emp_id}: {name}, PIN: {pin}")

cur.close()
conn.close()
