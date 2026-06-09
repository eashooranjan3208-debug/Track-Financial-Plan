from flask import Blueprint

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@auth_bp.route("/login")
def login():
    return "<h2>Auth Blueprint ✅ — Login page coming soon</h2>"


@auth_bp.route("/logout")
def logout():
    return "<h2>Logged out</h2>"