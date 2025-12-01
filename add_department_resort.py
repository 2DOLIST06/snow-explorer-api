import os, psycopg2
d = os.getenv('DATABASE_URL','postgresql://postgres:postgres@localhost:5432/ski')
conn = psycopg2.connect(d)
cur = conn.cursor()
cur.execute("ALTER TABLE public.resort ADD COLUMN IF NOT EXISTS department TEXT")
conn.commit()
cur.close(); conn.close()
print("colonne 'department' ajoutee ou deja existante dans 'resort'")
