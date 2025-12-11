import os
import psycopg2

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise SystemExit("DATABASE_URL not set.")

conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

name = input("Enter employee name: ")
pin = input("Enter employee PIN (numbers only): ")

cur.execute("INSERT INTO employees (name, pin) VALUES (%s, %s) RETURNING id", (name, pin))
emp_id = cur.fetchone()[0]
conn.commit()

print(f"Employee created with ID: {emp_id}")

cur.close()
conn.close()
