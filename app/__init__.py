from flask import Flask
from dotenv import load_dotenv 
import os                      

# Load .env file into environment variables BEFORE anything else
load_dotenv()


def create_app():
    """
    Application Factory — assembles and returns the Flask app.
    Called by run.py at startup.
    """
    app = Flask(__name__)

    # ── 1. Load config from environment variables ──────────────────────────
    app.config["SECRET_KEY"]  = os.getenv("SECRET_KEY", "fallback-secret-key")
    app.config["DB_HOST"]     = os.getenv("DB_HOST", "localhost")
    app.config["DB_PORT"]     = os.getenv("DB_PORT", "3306")
    app.config["DB_NAME"]     = os.getenv("DB_NAME", "track_act")
    app.config["DB_USER"]     = os.getenv("DB_USER", "root")
    app.config["DB_PASSWORD"] = os.getenv("DB_PASSWORD", "")

    # ── 2. Initialise the database connection pool ─────────────────────────
    from app.database import init_db
    init_db(app)

    # ── 3. Register Blueprints (routes) ────────────────────────────────────
    # We'll add more blueprints here as we build them
    from app.routes.main import main_bp
    app.register_blueprint(main_bp)

    print("[App] Flask app created successfully ✅")
    return app