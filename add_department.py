import os, psycopg2
d = os.getenv('DATABASE_URL','postgresql://postgres:postgres@localhost:5432/ski')
conn = psycopg2.connect(d)
cur = conn.cursor()
cur.execute("ALTER TABLE resorts ADD COLUMN IF NOT EXISTS department TEXT")
conn.commit()
cur.close(); conn.close()
print('column department added/kept')
