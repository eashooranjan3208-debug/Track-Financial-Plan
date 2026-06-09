from flask import Flask
from dotenv import load_dotenv
import os

load_dotenv()


def create_app():
    # Tell Flask exactly where templates/ and static/ live
    # since they are outside the app/ package folder
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

    app = Flask(
        __name__,
        template_folder=os.path.join(base_dir, 'templates'),
        static_folder=os.path.join(base_dir, 'static')
    )

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

    # ── 3. Register Blueprints ─────────────────────────────────────────────
    from app.routes.main import main_bp
    from app.routes.auth import auth_bp
    from app.routes.customer import customer_bp
    from app.routes.admin import admin_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(customer_bp)
    app.register_blueprint(admin_bp)

    print(f"[App] Template folder → {app.template_folder}")
    print(f"[App] Static folder   → {app.static_folder}")
    print("[App] Flask app created successfully ✅")
    return app