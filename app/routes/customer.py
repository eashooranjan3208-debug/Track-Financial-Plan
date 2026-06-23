import os
from flask import Blueprint, render_template, session, redirect, url_for, flash, current_app
from functools import wraps
from flask import send_file

from app.services.plan_service import get_current_plan, get_dashboard_data, get_tracking_data
from app.services.customer_service import get_customer_by_id

customer_bp = Blueprint("customer", __name__, url_prefix="/customer")


def customer_required(f):
    """
    Guard route for customers.
    Allows access if the user is a logged-in customer OR an Admin using the Magic Mirror.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        role = session.get("role")
        is_impersonating = session.get("impersonated_customer_id") is not None
        
        if role == "customer" or (role == "admin" and is_impersonating):
            return f(*args, **kwargs)
            
        flash("Please log in to access your portal.", "warning")
        return redirect(url_for("auth.login"))
    return decorated


@customer_bp.route("/dashboard")
@customer_required
def dashboard():
    # ── MAGIC MIRROR LOGIC ──
    is_admin = session.get("role") == "admin"
    is_admin_view = is_admin and session.get("impersonated_customer_id")
    
    # Determine which ID to query based on impersonation state
    customer_id = session.get("impersonated_customer_id") if is_admin_view else session.get("user_id")
    # ────────────────────────

    # Fetch plan exactly once
    plan = get_current_plan(customer_id)
    
    # Guard condition: No plan, or a manual plan viewed by a normal customer
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
    """
    Serves the UI wrapper (Navbar, Sidebar) with an iframe for the plan.
    """
    from app.services.plan_service import get_plan_by_id
    plan = get_plan_by_id(plan_id)
    
    current_viewer_id = session.get("impersonated_customer_id") if session.get("role") == "admin" else session.get("user_id")
    
    if not plan or (session.get("role") != "admin" and plan["customer_id"] != current_viewer_id):
        flash("Plan not found.", "danger")
        return redirect(url_for("customer.dashboard"))

    # We only verify the file exists here; we DO NOT read it into memory.
    html_path = plan.get("html_file_path") or plan.get("file_path")
    if not html_path or not os.path.exists(html_path):
        flash("Plan report file not available.", "warning")
        return redirect(url_for("customer.dashboard"))

    # Pass only the plan metadata to the template
    return render_template("customer/view_plan.html", plan=plan)

@customer_bp.route("/plan/<int:plan_id>/content")
@customer_required
def serve_plan_content(plan_id):
    """
    Streams the raw HTML file directly to the browser using OS-level file buffering.
    Uses almost zero memory.
    """
    from app.services.plan_service import get_plan_by_id
    plan = get_plan_by_id(plan_id)
    
    current_viewer_id = session.get("impersonated_customer_id") if session.get("role") == "admin" else session.get("user_id")
    
    # Re-verify auth to prevent users from guessing the /content URL of other people's plans
    if not plan or (session.get("role") != "admin" and plan["customer_id"] != current_viewer_id):
        return "Unauthorized", 403

    html_path = plan.get("html_file_path") or plan.get("file_path")
    
    # Send the file natively. 
    # mimetype='text/html' forces the browser to render it rather than downloading it.
    return send_file(html_path, mimetype='text/html')


@customer_bp.route('/dev-login/<int:customer_id>')
def dev_login(customer_id):
    """
    Bypasses the login screen so you can test the customer portal MVP.
    Strictly locked to development environments.
    """
    if current_app.config.get('ENV') == 'production':
        flash("This route is disabled in production.", "danger")
        return redirect(url_for('auth.login'))

    session.clear()
    session['role'] = 'customer'
    session['user_id'] = customer_id
    
    flash(f"Logged in as Customer #{customer_id} (Developer Mode) 🛠️", "success")
    return redirect(url_for('customer.dashboard'))

@customer_bp.route("/my-plan")
@customer_required
def my_plan():
    # If using your Impersonation feature, check that first
    customer_id = session.get("impersonated_customer_id") or session.get("user_id")
    
    plan = get_current_plan(customer_id)
    return render_template("customer/my_plan.html", plan=plan)

@customer_bp.route("/tracking")
@customer_required
def tracking():
    customer_id = session.get("impersonated_customer_id") or session.get("user_id")
    
    tracking_data = get_tracking_data(customer_id)
    if not tracking_data:
        flash("No active plan data to track.", "warning")
        return redirect(url_for("customer.dashboard"))
        
    return render_template("customer/tracking.html", **tracking_data)
