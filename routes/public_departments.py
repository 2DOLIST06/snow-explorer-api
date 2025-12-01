# app/routes/public_departments.py
from flask import Blueprint, jsonify, request

bp_departments = Blueprint("departments_public", __name__)

# Liste des 96 départements métropolitains (+2 corses) avec code INSEE, nom, region_id (slug FR métropole)
DEPARTMENTS_FR = [
  # Auvergne-Rhône-Alpes
  {"code":"01","name":"Ain","region_id":"auvergne-rhone-alpes"},
  {"code":"03","name":"Allier","region_id":"auvergne-rhone-alpes"},
  {"code":"07","name":"Ardèche","region_id":"auvergne-rhone-alpes"},
  {"code":"15","name":"Cantal","region_id":"auvergne-rhone-alpes"},
  {"code":"26","name":"Drôme","region_id":"auvergne-rhone-alpes"},
  {"code":"38","name":"Isère","region_id":"auvergne-rhone-alpes"},
  {"code":"42","name":"Loire","region_id":"auvergne-rhone-alpes"},
  {"code":"43","name":"Haute-Loire","region_id":"auvergne-rhone-alpes"},
  {"code":"63","name":"Puy-de-Dôme","region_id":"auvergne-rhone-alpes"},
  {"code":"69","name":"Rhône","region_id":"auvergne-rhone-alpes"},
  {"code":"73","name":"Savoie","region_id":"auvergne-rhone-alpes"},
  {"code":"74","name":"Haute-Savoie","region_id":"auvergne-rhone-alpes"},

  # Bourgogne-Franche-Comté
  {"code":"21","name":"Côte-d'Or","region_id":"bourgogne-franche-comte"},
  {"code":"25","name":"Doubs","region_id":"bourgogne-franche-comte"},
  {"code":"39","name":"Jura","region_id":"bourgogne-franche-comte"},
  {"code":"58","name":"Nièvre","region_id":"bourgogne-franche-comte"},
  {"code":"70","name":"Haute-Saône","region_id":"bourgogne-franche-comte"},
  {"code":"71","name":"Saône-et-Loire","region_id":"bourgogne-franche-comte"},
  {"code":"89","name":"Yonne","region_id":"bourgogne-franche-comte"},
  {"code":"90","name":"Territoire de Belfort","region_id":"bourgogne-franche-comte"},

  # Bretagne
  {"code":"22","name":"Côtes-d'Armor","region_id":"bretagne"},
  {"code":"29","name":"Finistère","region_id":"bretagne"},
  {"code":"35","name":"Ille-et-Vilaine","region_id":"bretagne"},
  {"code":"56","name":"Morbihan","region_id":"bretagne"},

  # Centre-Val de Loire
  {"code":"18","name":"Cher","region_id":"centre-val-de-loire"},
  {"code":"28","name":"Eure-et-Loir","region_id":"centre-val-de-loire"},
  {"code":"36","name":"Indre","region_id":"centre-val-de-loire"},
  {"code":"37","name":"Indre-et-Loire","region_id":"centre-val-de-loire"},
  {"code":"41","name":"Loir-et-Cher","region_id":"centre-val-de-loire"},
  {"code":"45","name":"Loiret","region_id":"centre-val-de-loire"},

  # Corse
  {"code":"2A","name":"Corse-du-Sud","region_id":"corse"},
  {"code":"2B","name":"Haute-Corse","region_id":"corse"},

  # Grand Est
  {"code":"08","name":"Ardennes","region_id":"grand-est"},
  {"code":"10","name":"Aube","region_id":"grand-est"},
  {"code":"51","name":"Marne","region_id":"grand-est"},
  {"code":"52","name":"Haute-Marne","region_id":"grand-est"},
  {"code":"54","name":"Meurthe-et-Moselle","region_id":"grand-est"},
  {"code":"55","name":"Meuse","region_id":"grand-est"},
  {"code":"57","name":"Moselle","region_id":"grand-est"},
  {"code":"67","name":"Bas-Rhin","region_id":"grand-est"},
  {"code":"68","name":"Haut-Rhin","region_id":"grand-est"},
  {"code":"88","name":"Vosges","region_id":"grand-est"},

  # Hauts-de-France
  {"code":"02","name":"Aisne","region_id":"hauts-de-france"},
  {"code":"59","name":"Nord","region_id":"hauts-de-france"},
  {"code":"60","name":"Oise","region_id":"hauts-de-france"},
  {"code":"62","name":"Pas-de-Calais","region_id":"hauts-de-france"},
  {"code":"80","name":"Somme","region_id":"hauts-de-france"},

  # Île-de-France
  {"code":"75","name":"Paris","region_id":"ile-de-france"},
  {"code":"77","name":"Seine-et-Marne","region_id":"ile-de-france"},
  {"code":"78","name":"Yvelines","region_id":"ile-de-france"},
  {"code":"91","name":"Essonne","region_id":"ile-de-france"},
  {"code":"92","name":"Hauts-de-Seine","region_id":"ile-de-france"},
  {"code":"93","name":"Seine-Saint-Denis","region_id":"ile-de-france"},
  {"code":"94","name":"Val-de-Marne","region_id":"ile-de-france"},
  {"code":"95","name":"Val-d'Oise","region_id":"ile-de-france"},

  # Normandie
  {"code":"14","name":"Calvados","region_id":"normandie"},
  {"code":"27","name":"Eure","region_id":"normandie"},
  {"code":"50","name":"Manche","region_id":"normandie"},
  {"code":"61","name":"Orne","region_id":"normandie"},
  {"code":"76","name":"Seine-Maritime","region_id":"normandie"},

  # Nouvelle-Aquitaine
  {"code":"16","name":"Charente","region_id":"nouvelle-aquitaine"},
  {"code":"17","name":"Charente-Maritime","region_id":"nouvelle-aquitaine"},
  {"code":"19","name":"Corrèze","region_id":"nouvelle-aquitaine"},
  {"code":"23","name":"Creuse","region_id":"nouvelle-aquitaine"},
  {"code":"24","name":"Dordogne","region_id":"nouvelle-aquitaine"},
  {"code":"33","name":"Gironde","region_id":"nouvelle-aquitaine"},
  {"code":"40","name":"Landes","region_id":"nouvelle-aquitaine"},
  {"code":"47","name":"Lot-et-Garonne","region_id":"nouvelle-aquitaine"},
  {"code":"64","name":"Pyrénées-Atlantiques","region_id":"nouvelle-aquitaine"},
  {"code":"79","name":"Deux-Sèvres","region_id":"nouvelle-aquitaine"},
  {"code":"86","name":"Vienne","region_id":"nouvelle-aquitaine"},
  {"code":"87","name":"Haute-Vienne","region_id":"nouvelle-aquitaine"},

  # Occitanie
  {"code":"09","name":"Ariège","region_id":"occitanie"},
  {"code":"11","name":"Aude","region_id":"occitanie"},
  {"code":"12","name":"Aveyron","region_id":"occitanie"},
  {"code":"30","name":"Gard","region_id":"occitanie"},
  {"code":"31","name":"Haute-Garonne","region_id":"occitanie"},
  {"code":"32","name":"Gers","region_id":"occitanie"},
  {"code":"34","name":"Hérault","region_id":"occitanie"},
  {"code":"46","name":"Lot","region_id":"occitanie"},
  {"code":"48","name":"Lozère","region_id":"occitanie"},
  {"code":"65","name":"Hautes-Pyrénées","region_id":"occitanie"},
  {"code":"66","name":"Pyrénées-Orientales","region_id":"occitanie"},
  {"code":"81","name":"Tarn","region_id":"occitanie"},
  {"code":"82","name":"Tarn-et-Garonne","region_id":"occitanie"},

  # Pays de la Loire
  {"code":"44","name":"Loire-Atlantique","region_id":"pays-de-la-loire"},
  {"code":"49","name":"Maine-et-Loire","region_id":"pays-de-la-loire"},
  {"code":"53","name":"Mayenne","region_id":"pays-de-la-loire"},
  {"code":"72","name":"Sarthe","region_id":"pays-de-la-loire"},
  {"code":"85","name":"Vendée","region_id":"pays-de-la-loire"},

  # Provence-Alpes-Côte d'Azur
  {"code":"04","name":"Alpes-de-Haute-Provence","region_id":"provence-alpes-cote-dazur"},
  {"code":"05","name":"Hautes-Alpes","region_id":"provence-alpes-cote-dazur"},
  {"code":"06","name":"Alpes-Maritimes","region_id":"provence-alpes-cote-dazur"},
  {"code":"13","name":"Bouches-du-Rhône","region_id":"provence-alpes-cote-dazur"},
  {"code":"83","name":"Var","region_id":"provence-alpes-cote-dazur"},
  {"code":"84","name":"Vaucluse","region_id":"provence-alpes-cote-dazur"},
]

@bp_departments.get("/api/departments")
def list_departments():
    region_id = (request.args.get("region_id") or "").strip().lower()
    data = DEPARTMENTS_FR
    if region_id:
        data = [d for d in DEPARTMENTS_FR if d["region_id"] == region_id]
    # format léger pour le front
    return jsonify([{"code": d["code"], "name": d["name"], "region_id": d["region_id"]} for d in data]), 200
