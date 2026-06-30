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
from decimal import Decimal

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
        # ── NETWORTH STATEMENT ─────────────────────────
    # Displayed above Goal Tracking.
    # 1. Investments with Vasupradah -> latest asset allocation Excel
    # 2. Other Heldaway Assets -> JSON dfcurrent_assets
    # 3. Other Assets (Non-Liquid) -> JSON dfheldaway, top 3 only
    
    
        
        # ── ASSET ALLOCATION & REBALANCING ─────────────────────
    # Box 1: Vasupradah investment only, from asset allocation Excel
    # Box 2: Vasupradah investment + dfcurrent_assets, from Excel + JSON
    # Target allocation comes from current plan weights.

    def _money(value):
        if value is None:
            return Decimal("0.00")
        try:
            return Decimal(str(value)).quantize(Decimal("0.01"))
        except Exception:
            return Decimal("0.00")

    def _pct(part, total):
        part = _money(part)
        total = _money(total)
        if total == 0:
            return Decimal("0.00")
        return ((part / total) * Decimal("100")).quantize(Decimal("0.01"))

    def _normalise_asset_class(asset_class):
        text = str(asset_class or "").strip().lower()

        if "equity" in text or "stock" in text or "share" in text:
            return "Equity"

        if "debt" in text or "bond" in text or "fixed" in text:
            return "Debt"

        if "liquid" in text or "cash" in text or "money market" in text:
            return "Liquid"

        # Important: asset_class1 = Commodity should be treated as Gold
        if "gold" in text or "commodity" in text:
            return "Gold"

        return "Other"

    def _target_allocations(plan):
        return {
            "Equity": _money(plan.get("weight_equity") or plan.get("w_eq") or 0),
            "Debt": _money(plan.get("weight_debt") or plan.get("w_de") or 0),
            "Liquid": _money(plan.get("weight_liquid") or plan.get("w_li") or 0),
            "Gold": _money(plan.get("weight_gold") or plan.get("w_go") or 0),
        }

    def _build_allocation_rows(actual_values, target_values):
        total_value = sum(actual_values.values(), Decimal("0.00"))
        rows = []

        for asset_class in ["Equity", "Debt", "Liquid", "Gold"]:
            target_pct = _money(target_values.get(asset_class))
            current_value = _money(actual_values.get(asset_class))
            actual_pct = _pct(current_value, total_value)
            deviation = (actual_pct - target_pct).quantize(Decimal("0.01"))

            is_off_track = abs(deviation) > Decimal("10.00")

            rows.append({
                "asset_class": asset_class,
                "target_pct": target_pct,
                "actual_pct": actual_pct,
                "deviation": deviation,
                "status": "Off Track" if is_off_track else "On Track",
                "is_off_track": is_off_track,
                "current_value": current_value,
            })

        return rows

    try:
        target_values = _target_allocations(plan)

        # 1. Vasupradah-only actual allocation from asset allocation Excel.
        vasupradah_actual = {
            "Equity": Decimal("0.00"),
            "Debt": Decimal("0.00"),
            "Liquid": Decimal("0.00"),
            "Gold": Decimal("0.00"),
        }

        vasupradah_allocation_rows = query(
            """
            SELECT asset_name, COALESCE(SUM(current_value), 0) AS total_value
            FROM customer_asset_allocation_snapshots
            WHERE customer_id = %s
            GROUP BY asset_name
            """,
            params=(customer_id,),
        ) or []

        for row in vasupradah_allocation_rows:
            bucket = _normalise_asset_class(row.get("asset_name"))
            if bucket in vasupradah_actual:
                vasupradah_actual[bucket] += _money(row.get("total_value"))

        # 2. Combined actual allocation = Vasupradah Excel + dfcurrent_assets JSON.
        combined_actual = dict(vasupradah_actual)

        current_asset_rows = query(
            """
            SELECT asset_class, COALESCE(SUM(current_value), 0) AS total_value
            FROM plan_current_assets
            WHERE plan_id = %s
            GROUP BY asset_class
            """,
            params=(plan["id"],),
        ) or []

        for row in current_asset_rows:
            bucket = _normalise_asset_class(row.get("asset_class"))
            if bucket in combined_actual:
                combined_actual[bucket] += _money(row.get("total_value"))

        data["allocation_rebalancing"] = {
            "vasupradah_only": _build_allocation_rows(vasupradah_actual, target_values),
            "combined": _build_allocation_rows(combined_actual, target_values),
            "threshold": Decimal("10.00"),
        }

    except Exception as e:
        print(f"❌ Failed to load allocation rebalancing: {e}")
        data["allocation_rebalancing"] = None

    def _share(part, total):
        part = _money(part)
        total = _money(total)

        if total == 0:
            return Decimal("0.00")

        return ((part / total) * Decimal("100")).quantize(Decimal("0.01"))

    try:
        # 1. Investments with Vasupradah
        # From latest asset allocation Excel import.
        # Show all asset classes.
        vasupradah_rows = query(
            """
            SELECT asset_name AS asset_class,
            COALESCE(SUM(current_value), 0) AS total_value
            FROM customer_asset_allocation_snapshots
            WHERE customer_id = %s
            GROUP BY asset_name
            ORDER BY total_value DESC
            """,
            params=(customer_id, ),
        ) or []

        # 2. Other Heldaway Assets
        # From JSON dfcurrent_assets imported into plan_current_assets.
        # Show all asset classes.
        heldaway_rows = query(
            """
            SELECT asset_class,
                   COALESCE(SUM(current_value), 0) AS total_value
            FROM plan_current_assets
            WHERE plan_id = %s
            GROUP BY asset_class
            ORDER BY total_value DESC
            """,
            params=(plan["id"],),
        ) or []

        # 3. Other Assets (Non-Liquid)
        # From JSON dfheldaway imported into other_assets.
        # ONLY this section shows top 3 asset classes.
        non_liquid_rows = query(
            """
            SELECT 
                   COALESCE(asset_type, asset_name, 'Other') AS asset_class,
                   MIN(maturity_date) AS start_year,
                   MAX(YEAR(maturity_date)) AS end_year,
                   COALESCE(SUM(maturity_value), 0) AS total_value
            FROM other_assets
            WHERE customer_id = %s
            GROUP BY COALESCE(asset_type, asset_name, 'Other')
            ORDER BY total_value DESC
            LIMIT 3
            """,
            params=(customer_id,),
        ) or []

        vasupradah_total = sum(
            (_money(r.get("total_value")) for r in vasupradah_rows),
            Decimal("0.00")
        )

        heldaway_total = sum(
            (_money(r.get("total_value")) for r in heldaway_rows),
            Decimal("0.00")
        )

        non_liquid_total = sum(
            (_money(r.get("total_value")) for r in non_liquid_rows),
            Decimal("0.00")
        )

        grand_total = vasupradah_total + heldaway_total + non_liquid_total

        data["networth_statement"] = {
            "vasupradah_rows": vasupradah_rows,
            "heldaway_rows": heldaway_rows,
            "non_liquid_rows": non_liquid_rows,

            "vasupradah_total": vasupradah_total,
            "heldaway_total": heldaway_total,
            "non_liquid_total": non_liquid_total,
            "grand_total": grand_total,

            "vasupradah_share": _share(vasupradah_total, grand_total),
            "heldaway_share": _share(heldaway_total, grand_total),
            "non_liquid_share": _share(non_liquid_total, grand_total),
        }

    except Exception as e:
        print(f"❌ Failed to load networth statement: {e}")
        data["networth_statement"] = None
    
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
    
    