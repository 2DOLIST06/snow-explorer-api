from app.models.base import db
from app.models.region import Region

REGIONS_FR = [
  {'id':'auvergne-rhone-alpes','name':'Auvergne-Rhône-Alpes','country_code':'FR'},
  {'id':'bourgogne-franche-comte','name':'Bourgogne-Franche-Comté','country_code':'FR'},
  {'id':'bretagne','name':'Bretagne','country_code':'FR'},
  {'id':'centre-val-de-loire','name':'Centre-Val de Loire','country_code':'FR'},
  {'id':'corse','name':'Corse','country_code':'FR'},
  {'id':'grand-est','name':'Grand Est','country_code':'FR'},
  {'id':'hauts-de-france','name':'Hauts-de-France','country_code':'FR'},
  {'id':'ile-de-france','name':'Île-de-France','country_code':'FR'},
  {'id':'normandie','name':'Normandie','country_code':'FR'},
  {'id':'nouvelle-aquitaine','name':'Nouvelle-Aquitaine','country_code':'FR'},
  {'id':'occitanie','name':'Occitanie','country_code':'FR'},
  {'id':'pays-de-la-loire','name':'Pays de la Loire','country_code':'FR'},
  {'id':'provence-alpes-cote-dazur','name':"Provence-Alpes-Côte d'Azur",'country_code':'FR'},
]

with db.connection_context():
    db.create_tables([Region])
    # upsert par clé primaire (id)
    for row in REGIONS_FR:
        Region.insert(**row).on_conflict(conflict_target=[Region.id], preserve=[Region.name, Region.country_code]).execute()
    print('OK regions seeded')
