import os
import psycopg2

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise SystemExit("DATABASE_URL not set. Run `set DATABASE_URL=...` first.")

conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

store_id = input("Enter store ID to update (e.g. 1): ")
new_token = input("Enter NEW qr_code_token (no spaces): ")

cur.execute("UPDATE stores SET qr_code_token = %s WHERE id = %s", (new_token, store_id))
if cur.rowcount == 0:
    print("No store with that ID.")
else:
    print(f"Updated store {store_id} to token: {new_token}")

conn.commit()
cur.close()
conn.close()
