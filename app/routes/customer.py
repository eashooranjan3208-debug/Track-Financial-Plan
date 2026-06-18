from flask import Blueprint, render_template, session, redirect, url_for, flash
from functools import wraps
import os 
from app.services.plan_service import (
    get_current_plan, get_current_cycle,
    get_goals, get_goal_summary,
    get_cashflow_for_year, get_cashflow,
    get_current_assets, get_current_assets_total,
    get_family_members, get_retirement_expenses,
    get_archived_plans,get_current_assets_with_id,
    get_vasupradha_investments, get_vasupradha_investments_summary,
    get_latest_portfolio_value, get_portfolio_history,
    get_portfolio_by_account,
    get_other_assets_with_id,
    get_latest_asset_allocation, get_asset_allocation_total,
    get_other_assets,get_dashboard_data
)
from app.services.customer_service import get_customer_by_id

customer_bp = Blueprint("customer", __name__, url_prefix="/customer")


def customer_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("role") != "customer":
            flash("Please log in to access your portal.", "warning")
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return decorated


@customer_bp.route("/dashboard")
@customer_required
def dashboard():
    # ── MAGIC MIRROR LOGIC ──
    # If Admin is impersonating, use that ID. Otherwise, use normal logged-in user.
    if session.get("role") == "admin" and session.get("impersonated_customer_id"):
        customer_id = session.get("impersonated_customer_id")
    else:
        customer_id = session["user_id"]
    # ────────────────────────
    is_admin_view = session.get("role") == "admin" and session.get("impersonated_customer_id")
    plan = get_current_plan(customer_id)
    if not plan:
        return render_template("customer/no_plan.html")

    plan_id = plan["id"]
    cycle   = get_current_cycle(plan)

    # After defining customer_id and is_admin_view...
    plan = get_current_plan(customer_id)
    
    # ── UPDATED GUARD CONDITION (Step 16e logic) ──
    is_admin = session.get("role") == "admin"
    if not plan or (not is_admin and plan.get("ingestion_source") == "manual"):
        return render_template("customer/no_plan.html")

    # Fetch all data in one go from your centralized service
    data = get_dashboard_data(customer_id)
    if not data:
        return render_template("customer/no_plan.html")

    # Add extra context the service doesn't know about
    data.update({
        "is_admin_view": is_admin_view,
        "customer_id": customer_id
    })

    return render_template("customer/dashboard.html", **data)
    

@customer_bp.route("/plan/<int:plan_id>")
@customer_required
def view_plan(plan_id):
    """Serve the HTML plan report for the customer to view."""
    from app.services.plan_service import get_plan_by_id
    plan = get_plan_by_id(plan_id)
    # Admins can view any plan; customers can only view their own
    if not plan or (session.get("role") != "admin" and plan["customer_id"] != session["user_id"]):
        flash("Plan not found.", "danger")
        return redirect(url_for("customer.dashboard"))

    html_path = plan.get("html_file_path") or plan.get("file_path")
    if not html_path or not os.path.exists(html_path):
        flash("Plan report file not available.", "warning")
        return redirect(url_for("customer.dashboard"))

    with open(html_path, "r", encoding="utf-8") as f:
        plan_html = f.read()

    return render_template("customer/view_plan.html",
                           plan=plan, plan_html=plan_html)





@customer_bp.route('/dev-login/<int:customer_id>')
def dev_login(customer_id):
    # Clear any existing admin session
    session.clear()
    
    # Set the session to act as this specific customer
    session['role'] = 'customer'
    session['user_id'] = customer_id
    
    return redirect(url_for('customer.dashboard'))