from app.models.base import db
from app.models.region import Region

with db.connection_context():
    print('DSN OK')
    n = Region.select().count()
    print('regions count =', n)
    for r in Region.select().order_by(Region.name):
        print(r.id, '—', r.name)
