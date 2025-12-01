import os, psycopg2
d = os.getenv('DATABASE_URL','postgresql://postgres:postgres@localhost:5432/ski')
conn = psycopg2.connect(d)
cur = conn.cursor()
cur.execute("select table_name from information_schema.tables where table_schema='public'")
print([r[0] for r in cur.fetchall()])
cur.close(); conn.close()
