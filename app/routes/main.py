from flask import Blueprint, render_template
from app.database import query
from app.services.customer_service import get_all_customers
from app.services.plan_service import get_yearly_investments

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def index():
    return "<h2>Track & Act — App is running ✅</h2>"


# ── TEMPORARY test route — we will remove this after verification ──────────
@main_bp.route("/test-db")
def test_db():
    try:
        customers = query("SELECT id, name, email, mobile FROM customers")

        if not customers:
            return "<p>✅ Connected to DB — but no customers found yet.</p>"

        # Build a simple HTML table to display results
        rows = ""
        for c in customers:
            rows += f"""
                <tr>
                    <td>{c['id']}</td>
                    <td>{c['name']}</td>
                    <td>{c['email']}</td>
                    <td>{c['mobile']}</td>
                </tr>
            """

        return f"""
            <h2>✅ MySQL Connection Successful</h2>
            <table border='1' cellpadding='8' cellspacing='0'>
                <thead>
                    <tr>
                        <th>ID</th>
                        <th>Name</th>
                        <th>Email</th>
                        <th>Mobile</th>
                    </tr>
                </thead>
                <tbody>
                    {rows}
                </tbody>
            </table>
        """

    except Exception as e:
        return f"<h2>❌ DB Error</h2><pre>{str(e)}</pre>"
@main_bp.route("/test-services")
def test_services():
    try:
        customers = get_all_customers()

        first_id = customers[0]["id"] if customers else None

        investments = (
            get_yearly_investments(first_id)
            if first_id else []
        )

        return f"""
            <h2>✅ Services Layer Working</h2>
            <p><strong>Customers found:</strong> {len(customers)}</p>
            <p><strong>Investments for customer {first_id}:</strong>
            {len(investments)} records</p>
        """

    except Exception as e:
        return f"<h2>❌ Service Error</h2><pre>{str(e)}</pre>"

@main_bp.route("/test-base")
def test_base():
    return render_template("test_base.html")