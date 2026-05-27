import os
import psycopg2

print("DATABASE_URL:", os.getenv("DATABASE_URL"))
conn = psycopg2.connect(os.getenv("DATABASE_URL"))
cur = conn.cursor()
cur.execute("SELECT NOW()")
print("DB Connection OK, time:", cur.fetchone())
cur.close()
conn.close()
