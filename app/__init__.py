from flask import Flask
from dotenv import load_dotenv
from flask_cors import CORS
from app.models.base import db
from app.models.region import Region
from app.models.resort import Resort
from app.models.piste import Piste
from app.models.lift import Lift
from app.models.resort_map import ResortMap
from app.models.station_widgets import StationWidgets   
from app.models.resort_import_history import ResortImportHistory
from app.routes.public_resorts import bp_public
from app.routes.admin_resorts import bp_admin, bp_admin_stations_compat
from app.routes.stations_widgets import bp_widgets      
from app.routes.admin_stations import bp_admin_st
from app.routes.public_regions import bp_regions
from app.routes.public_departments import bp_departments
from app.routes.uploads import bp_uploads
from app.routes.admin_resort_import import bp_resort_json
from app.services.admin_auth import protect_admin_routes



def create_app():
    load_dotenv()
    app = Flask(__name__)

    # La protection est centralisée afin qu'aucune route d'administration,
    # présente ou ajoutée plus tard, ne puisse être oubliée.
    protect_admin_routes(app)

    # CORS pour le front Next.js
    CORS(app)

    # Connexion à la base et création des tables
    db.connect(reuse_if_open=True)
    db.create_tables([Region, Resort, Piste, Lift, ResortMap, StationWidgets, ResortImportHistory])
    db.close()

    # Enregistrement des blueprints
    app.register_blueprint(bp_public)
    app.register_blueprint(bp_admin)
    app.register_blueprint(bp_admin_stations_compat)
    app.register_blueprint(bp_widgets)  # ⬅️ Enregistrement widgets
    app.register_blueprint(bp_admin_st)
    app.register_blueprint(bp_regions)
    app.register_blueprint(bp_departments)
    app.register_blueprint(bp_uploads)
    app.register_blueprint(bp_resort_json)
    # Le front historique utilise ``/api/admin/stations`` tandis que les
    # routes d'import/export ont d'abord été publiées sous ``resorts``.
    # Enregistrer le même blueprint une seconde fois garde les deux contrats
    # disponibles (Flask exige un nom distinct pour ce second montage).
    app.register_blueprint(
        bp_resort_json,
        url_prefix="/api/admin/stations",
        name="admin_station_json",
    )

    return app
