# scripts/add_department.py
from app import create_app
from app.models.base import db

app = create_app()

with app.app_context():
    db.connect(reuse_if_open=True)
    try:
        db.execute_sql('ALTER TABLE resort ADD COLUMN department VARCHAR(255);')
        print("OK: colonne 'department' ajoutée")
    except Exception as e:
        print("Note:", e)  # ex: "column already exists"
    finally:
        db.close()
