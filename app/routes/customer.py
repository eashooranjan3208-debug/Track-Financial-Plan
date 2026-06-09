from flask import Blueprint

customer_bp = Blueprint("customer", __name__, url_prefix="/customer")


@customer_bp.route("/dashboard")
def dashboard():
    return "<h2>Customer Blueprint ✅ — Dashboard coming soon</h2>"