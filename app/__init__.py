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
from app.routes.public_resorts import bp_public
from app.routes.admin_resorts import bp_admin, bp_admin_stations_compat
from app.routes.stations_widgets import bp_widgets      
from app.routes.admin_stations import bp_admin_st
from app.routes.public_regions import bp_regions
from app.routes.public_departments import bp_departments
from app.routes.uploads import bp_uploads



def create_app():
    load_dotenv()
    app = Flask(__name__)

    # CORS pour le front Next.js
    CORS(app)

    # Connexion à la base et création des tables
    db.connect(reuse_if_open=True)
    db.create_tables([Region, Resort, Piste, Lift, ResortMap, StationWidgets])  # ⬅️ StationWidgets ajoutée
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

    return app

