from flask import Blueprint

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.route("/dashboard")
def dashboard():
    return "<h2>Admin Blueprint ✅ — Admin panel coming soon</h2>"