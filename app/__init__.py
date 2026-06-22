from flask import Flask, redirect, url_for
from app.config import config_by_name
from flask_wtf.csrf import CSRFProtect

# 1. Import the init_db function
from app.database import init_db

csrf = CSRFProtect()

def create_app(config_name='dev'):
    app = Flask(__name__, template_folder='../templates')
    
    # Load configuration first so the DB has access to the credentials
    app.config.from_object(config_by_name[config_name])

    # 2. Initialize the database connection pool
    init_db(app)

    # Initialize CSRF protection
    csrf.init_app(app)

    # Register Blueprints
    from app.routes.admin import admin_bp
    from app.routes.customer import customer_bp
    from app.routes.auth import auth_bp
    
    app.register_blueprint(admin_bp)
    app.register_blueprint(customer_bp)
    app.register_blueprint(auth_bp)

    # Root Route
    @app.route('/')
    def index():
        return redirect(url_for('auth.login'))

    return app
 