import os
import psycopg2

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise SystemExit("DATABASE_URL is not set. Run `set DATABASE_URL=...` first in this window.")

def main():
    print("Connecting to database...")
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    cur.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
        ORDER BY table_name;
    """)

    rows = cur.fetchall()
    if not rows:
        print("No tables found in public schema.")
    else:
        print("Tables in public schema:")
        for (name,) in rows:
            print(" -", name)

    cur.close()
    conn.close()
    print("Done.")

if __name__ == "__main__":
    main()
