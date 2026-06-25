import os
from flask import Blueprint, render_template, session, redirect, url_for, flash, current_app
from functools import wraps
from flask import send_file
from app.database import query
from app.services.plan_service import get_current_plan, get_dashboard_data, get_tracking_data
from app.services.customer_service import get_customer_by_id
from app.services.plan_service import get_customer_json_filepath
from app.services.tracking_service import (
    calculate_investment_deviation,
    calculate_allocation_deviation,
    calculate_portfolio_deviation,
    TrackingError
)


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
    
    data = get_dashboard_data(customer_id) or {}
    
    # ── TRANSACTIONS ENGINE ──
    try:
        # Fetch all transactions for this user, newest first
        transactions = query(
            "SELECT transaction_date, total_amount FROM customer_transactions WHERE customer_id = %s ORDER BY transaction_date DESC",
            params=(customer_id,)
        ) or []
        
        # Calculate investments (positive) and redemptions (negative)
        total_inv = sum(float(t.get("total_amount", 0)) for t in transactions if float(t.get("total_amount", 0)) > 0)
        total_red = sum(float(t.get("total_amount", 0)) for t in transactions if float(t.get("total_amount", 0)) < 0)
        
        # Match your exact HTML variables!
        data["vasupradah_summary"] = {
            "net_invested": total_inv + total_red,
            "total_invested": total_inv,
            "total_redeemed": abs(total_red),
            "transaction_count": len(transactions)  # Feeds the counter
        }
        data["vasupradah_txns"] = transactions      # Feeds the recent list loop
        
    except Exception as e:
        print(f"❌ Failed to load transactions for dashboard: {e}")
        data["vasupradah_summary"] = {"net_invested": 0, "total_invested": 0, "total_redeemed": 0, "transaction_count": 0}
        data["vasupradah_txns"] = []
    # ─────────────────────────
    
    data.setdefault("goals", [])
    data.setdefault("current_year", 2026) 

    # Add extra context the service doesn't know about
    data.update({
        "is_admin_view": is_admin_view,
        "customer_id": customer_id
    })

    # THE CRUCIAL LINE THAT WAS MISSING:
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
    # 1. Get the current customer ID (from your session logic)
    customer_id = session.get("impersonated_customer_id") or session.get("user_id")
    
    # 2. Fetch the customer object
    customer = get_customer_by_id(customer_id)
    
    # ... your existing plan query ...
    plan = query("SELECT file_path, html_file_path FROM financial_plans WHERE customer_id = %s AND is_current = 1", params=(customer_id,), fetchone=True)
    
    # 3. Pass BOTH variables to the template!
    return render_template("customer/my_plan.html", plan=plan, customer=customer)

@customer_bp.route("/tracking")
def tracking():
    """
    Render the Goal Tracking and Asset Allocation dashboard.
    """
    customer_id = session.get("user_id")
    if not customer_id:
        return redirect(url_for("auth.login"))

    # 1. Fetch the raw PAN to locate the file
    customer = query("SELECT pan FROM customers WHERE id = %s", params=(customer_id,), fetchone=True)
    raw_pan = customer.get("pan") if customer else None

    # 2. Get the secure file path
    filepath = get_customer_json_filepath(raw_pan)

    # 3. Graceful Fallback: No file exists yet
    if not filepath or not os.path.exists(filepath):
        return render_template(
            "customer/tracking.html", 
            has_plan=False
        )

    # 4. Execute the Tracking Math
    try:
        investment_data = calculate_investment_deviation(customer_id, filepath)
        allocation_data = calculate_allocation_deviation(customer_id, filepath)
        portfolio_data = calculate_portfolio_deviation(customer_id, filepath)
        
        return render_template(
            "customer/tracking.html",
            has_plan=True,
            investment=investment_data,
            allocations=allocation_data,
            portfolio=portfolio_data
        )
        
    except TrackingError as e:
        # Catch data formatting issues smoothly without a 500 server crash
        flash(f"Data issue detected: {str(e)}", "warning")
        return render_template("customer/tracking.html", has_plan=False)
        
    except Exception as e:
        # Generic safety net
        flash("An unexpected error occurred while calculating your tracking data.", "danger")
        return render_template("customer/tracking.html", has_plan=False)
    
@customer_bp.route("/serve-report")
@customer_required # (Use whatever decorator you normally use here)
def serve_html_report():
    """Securely serve the HTML report for the logged-in customer."""
    # Get the ID of the customer we are currently viewing
    customer_id = session.get("impersonated_customer_id") or session.get("user_id")
    
    plan = query(
        "SELECT html_file_path FROM financial_plans WHERE customer_id = %s AND is_current = 1", 
        params=(customer_id,), 
        fetchone=True
    )
    
    if not plan or not plan.get("html_file_path"):
        return "No HTML report attached to this plan.", 404
        
    filepath = plan["html_file_path"]
    
    if os.path.exists(filepath):
        return send_file(filepath, mimetype='text/html')
    else:
        return "File path found in database, but physical file is missing.", 404
    
    