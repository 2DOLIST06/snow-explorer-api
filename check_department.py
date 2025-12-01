import os, psycopg2
d = os.getenv('DATABASE_URL','postgresql://postgres:postgres@localhost:5432/ski')
conn = psycopg2.connect(d)
cur = conn.cursor()
cur.execute("select 1 from information_schema.columns where table_name='resorts' and column_name='department'")
print('department = OK' if cur.fetchone() else 'department = MISSING')
cur.close(); conn.close()
