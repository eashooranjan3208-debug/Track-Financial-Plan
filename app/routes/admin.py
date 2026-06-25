import os
import sys
from werkzeug.utils import secure_filename
from flask import current_app # Needed to check our environment config
from datetime import datetime
from functools import wraps
from flask import (Blueprint, render_template, request,
                   redirect, url_for, flash, session)
import tempfile
from app.services.dashboard_service import get_networth_statement
from flask import send_file 

# Utils & Services
from app.utils import (
    make_upload_path, extract_date_from_filename,
    extract_zip, cleanup_temp_dir
)
from app.database import query
from app.services.customer_service import get_all_customers
from app.services.plan_service import get_dashboard_data, get_tracking_data
from app.services.customer_service import get_customer_by_id
from app.services.admin_service import (
    get_dashboard_stats,
    archive_existing_plans,
    create_plan_record,
    insert_family_members,
    insert_goals,
    insert_cashflow,
    insert_current_assets,
    insert_retirement_expenses,
    insert_other_assets,
    insert_transactions,
    insert_portfolio_snapshots,
    insert_asset_allocation_snapshots,
    build_customer_lookup,
    process_bulk_upload,
    add_current_asset, 
    update_current_asset,
    delete_current_asset, 
    get_current_asset_row,
    add_other_asset,
    update_other_asset,
    delete_other_asset,
    get_other_asset_row,
    get_or_create_plan_id
)

# Parsers
from app.parsers.parse_plan_json import parse_plan_json
from app.parsers.parse_transactions import parse_transactions
from app.parsers.parse_portfolio import parse_portfolio
from app.parsers.parse_asset_allocation import parse_asset_allocation

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

ALLOWED_EXTENSIONS = {
    "json": {"json"},
    "html": {"html", "htm"},
    "xlsx": {"xlsx", "xls"},
}

# ── Auth guard ─────────────────────────────────────────────────
def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("role") != "admin":
            flash("Admin access required.", "warning")
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return decorated

def _allowed(filename, types):
    """Checks extension and returns a sanitized, safe filename."""
    safe_name = secure_filename(filename) 
    ext = safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else ""
    return ext in types, safe_name

# ── Admin dashboard ────────────────────────────────────────────
@admin_bp.route("/dashboard")
@admin_required
def dashboard():
    stats     = get_dashboard_stats()
    customers = get_all_customers()
    return render_template(
        "admin/dashboard.html",
        stats=stats,
        customers=customers,
    )

# ── Customer list ──────────────────────────────────────────────
@admin_bp.route("/customers")
@admin_required
def customers():
    all_customers = get_all_customers()
    return render_template("admin/customers.html", customers=all_customers)

# ── MILESTONE 1: Magic Mirror (Impersonation) ──────────────────
@admin_bp.route("/impersonate/<int:customer_id>")
@admin_required
def impersonate(customer_id):
    session["impersonated_customer_id"] = customer_id
    flash(f"You are now viewing the dashboard as Customer #{customer_id}.", "info")
    return redirect(url_for("customer.dashboard"))

@admin_bp.route("/stop-impersonating")
@admin_required
def stop_impersonating():
    session.pop("impersonated_customer_id", None)
    flash("Exited Admin View.", "success")
    return redirect(url_for("admin.dashboard"))

# ── MILESTONE 2: Manual Asset Overrides ────────────────────────
@admin_bp.route("/asset/edit/current", methods=["POST"])
@admin_required
def edit_current_asset():
    asset_id = request.form.get("asset_id")
    new_value = request.form.get("new_value")
    if asset_id and new_value is not None:
        query(
            "UPDATE plan_current_assets SET current_value = %s, is_manual_override = 1 WHERE id = %s",
            params=(new_value, asset_id), 
            commit=True
        )
        flash("Current Asset updated successfully. It is now locked from ZIP overwrites.", "success")
    return redirect(url_for("customer.dashboard"))

@admin_bp.route("/asset/edit/other", methods=["POST"])
@admin_required
def edit_other_asset():
    asset_id = request.form.get("asset_id")
    new_value = request.form.get("new_value")
    if asset_id and new_value is not None:
        query(
            "UPDATE other_assets SET maturity_value = %s, is_manual_override = 1 WHERE id = %s",
            params=(new_value, asset_id), 
            commit=True
        )
        flash("Held Away Asset updated successfully. It is now locked from ZIP overwrites.", "success")
    return redirect(url_for("customer.dashboard"))

# ── UNIFIED FOLDER UPLOAD ──────────────────────────────────────
@admin_bp.route("/upload", methods=["GET", "POST"])
@admin_required
def upload():
    if request.method == "GET":
        # We no longer need to pass the 'customers' list to the template!
        return render_template("admin/upload.html")

    # Accept multiple files from a folder selection
    uploaded_files = request.files.getlist("folder_files")
    
    if not uploaded_files or not uploaded_files[0].filename:
        flash("Please select a folder containing the data files.", "warning")
        return render_template("admin/upload.html")

    temp_dir = tempfile.mkdtemp()
    
    try:
        for file in uploaded_files:
            if file and file.filename:
                base_name = os.path.basename(file.filename)
                safe_filename = secure_filename(base_name)
                
                # Ignore empty names or hidden OS files
                if safe_filename and safe_filename.lower() != 'ds_store':
                    file_path = os.path.join(temp_dir, safe_filename)
                    file.save(file_path)

        # Pass the folder to the smart processor
        results = process_bulk_upload(temp_dir)
        
    finally:
        cleanup_temp_dir(temp_dir)

    # We can reuse the bulk_upload_result.html template since it displays the dictionary perfectly
    return render_template("admin/bulk_upload_result.html", results=results)

# ── Temporary Developer Backdoor ───────────────────────────────
@admin_bp.route("/dev-login")
def dev_login():
    """
    Bypasses the login screen so you can test the admin portal MVP.
    Strictly locked to development environments.
    """
    # Safeguard: Never allow this in production!
    if current_app.config.get('ENV') == 'production':
        flash("This route is disabled in production.", "danger")
        return redirect(url_for('auth.login'))

    session.clear()
    session["user_id"] = 1
    session["role"] = "admin"
    flash("Logged in as Admin (Developer Mode) 🛠️", "success")
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/customer/<int:customer_id>/current-assets/add", methods=["POST"])
@admin_required
def add_current_asset_route(customer_id):
    asset_name  = request.form.get("asset_name", "").strip()
    asset_class = request.form.get("asset_class", "").strip()
    current_value = request.form.get("current_value", "0").strip()

    if not asset_name:
        flash("Asset name is required.", "warning")
        return redirect(url_for("admin.customer_dashboard", customer_id=customer_id))

    try:
        value = float(current_value or 0)
    except ValueError:
        value = 0

    add_current_asset(customer_id, asset_name, asset_class, value)
    flash(f"Added '{asset_name}' to Current Financial Assets.", "success")
    return redirect(url_for("admin.customer_dashboard", customer_id=customer_id) + "#current-assets")


@admin_bp.route("/current-assets/<int:row_id>/edit", methods=["POST"])
@admin_required
def edit_current_asset_route(row_id):
    row = get_current_asset_row(row_id)
    if not row:
        flash("Asset record not found.", "danger")
        return redirect(url_for("admin.customers"))

    customer_id = request.form.get("customer_id")

    asset_name    = request.form.get("asset_name", "").strip()
    asset_class   = request.form.get("asset_class", "").strip()
    current_value = request.form.get("current_value", "0").strip()

    try:
        value = float(current_value or 0)
    except ValueError:
        value = 0

    update_current_asset(row_id, asset_name, asset_class, value)
    flash(f"Updated '{asset_name}'.", "success")
    return redirect(url_for("admin.customer_dashboard", customer_id=customer_id) + "#current-assets")


@admin_bp.route("/current-assets/<int:row_id>/delete", methods=["POST"])
@admin_required
def delete_current_asset_route(row_id):
    customer_id = request.form.get("customer_id")
    delete_current_asset(row_id)
    flash("Asset removed.", "info")
    return redirect(url_for("admin.customer_dashboard", customer_id=customer_id) + "#current-assets")



#### Added ### 

def _parse_maturity_date(raw):
    """Parse a date input (YYYY-MM-DD) or empty string → date/None."""
    from datetime import datetime
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


@admin_bp.route("/customer/<int:customer_id>/other-assets/add", methods=["POST"])
@admin_required
def add_other_asset_route(customer_id):
    asset_name = request.form.get("asset_name", "").strip()
    if not asset_name:
        flash("Asset name is required.", "warning")
        return redirect(url_for("admin.customer_dashboard", customer_id=customer_id) + "#other-assets")

    asset_type = request.form.get("asset_type", "").strip()
    notes      = request.form.get("notes", "").strip() or None

    def _num(field):
        try:
            return float(request.form.get(field, "0") or 0)
        except ValueError:
            return 0

    add_other_asset(
        customer_id, asset_name, asset_type,
        _num("current_value"), _num("maturity_value"), _num("annual_contribution"),
        _parse_maturity_date(request.form.get("maturity_date")),
        notes,
    )
    flash(f"Added '{asset_name}' to Held Away Assets.", "success")
    return redirect(url_for("admin.customer_dashboard", customer_id=customer_id) + "#other-assets")


@admin_bp.route("/other-assets/<int:row_id>/edit", methods=["POST"])
@admin_required
def edit_other_asset_route(row_id):
    row = get_other_asset_row(row_id)
    if not row:
        flash("Asset record not found.", "danger")
        return redirect(url_for("admin.customers"))

    customer_id = request.form.get("customer_id")
    asset_name  = request.form.get("asset_name", "").strip()
    asset_type  = request.form.get("asset_type", "").strip()
    notes       = request.form.get("notes", "").strip() or None

    def _num(field):
        try:
            return float(request.form.get(field, "0") or 0)
        except ValueError:
            return 0

    update_other_asset(
        row_id, asset_name, asset_type,
        _num("current_value"), _num("maturity_value"), _num("annual_contribution"),
        _parse_maturity_date(request.form.get("maturity_date")),
        notes,
    )
    flash(f"Updated '{asset_name}'.", "success")
    return redirect(url_for("admin.customer_dashboard", customer_id=customer_id) + "#other-assets")


@admin_bp.route("/other-assets/<int:row_id>/delete", methods=["POST"])
@admin_required
def delete_other_asset_route(row_id):
    customer_id = request.form.get("customer_id")
    delete_other_asset(row_id)
    flash("Asset removed.", "info")
    return redirect(url_for("admin.customer_dashboard", customer_id=customer_id) + "#other-assets")

@admin_bp.route("/customer/<int:customer_id>/dashboard")
@admin_required
def customer_dashboard(customer_id):
    customer = get_customer_by_id(customer_id)
    if not customer:
        flash("Customer not found.", "danger")
        return redirect(url_for("admin.customers"))

    # Ensure a minimal plan exists so admin can manage assets
    # even for customers with no uploaded JSON plan
    get_or_create_plan_id(customer_id)

    data = get_dashboard_data(customer_id)

    # data should now never be None for admin view, but guard anyway
    if data is None:
        return render_template(
            "customer/no_plan.html",
            is_admin_view=True,
            customer=customer,
            customer_id=customer_id,
        )

    # --- THE FIX STARTS HERE ---
    # 1. Fetch the data using our new service
    networth_data = get_networth_statement(customer_id)
    
    # 2. Structure the specific dictionary the dashboard.html template expects
    vasupradah_summary = {
        "net_invested": networth_data.get("vasupradah_managed_assets", 0)
    }
    # --- THE FIX ENDS HERE ---

    return render_template(
        "customer/dashboard.html",
        is_admin_view=True,
        customer=customer,
        customer_id=customer_id,
        vasupradah_summary=vasupradah_summary,  # <-- Pass it to Jinja here
        **data
    )

@admin_bp.route("/customer/<int:customer_id>/tracking")
@admin_required
def customer_tracking(customer_id):
    customer = get_customer_by_id(customer_id)
    
    # 1. Ask the DB exactly where the JSON file is stored
    plan_record = query(
        "SELECT file_path FROM financial_plans WHERE customer_id = %s AND is_current = 1", 
        params=(customer_id,), 
        fetchone=True
    )
    
    filepath = plan_record.get("file_path") if plan_record else None

    # 2. Safety check: Does the file exist physically?
    import os
    if not filepath or not os.path.exists(filepath):
        # Graceful fallback to the empty state UI
        return render_template("customer/tracking.html", has_plan=False, is_admin_view=True, customer=customer)

    # 3. If the file exists, run the math!
    from app.services.tracking_service import calculate_investment_deviation, calculate_allocation_deviation, calculate_portfolio_deviation
    
    investment_data = calculate_investment_deviation(customer_id, filepath)
    allocation_data = calculate_allocation_deviation(customer_id, filepath)
    portfolio_data = calculate_portfolio_deviation(customer_id, filepath)

    return render_template(
        "customer/tracking.html", 
        customer=customer, 
        is_admin_view=True,
        has_plan=True,
        investment=investment_data,
        allocations=allocation_data,
        portfolio=portfolio_data
    )

@admin_bp.route("/customer/<int:customer_id>/my-plan")
@admin_required
def customer_my_plan(customer_id):
    customer = get_customer_by_id(customer_id)
    plan = query("SELECT file_path, html_file_path FROM financial_plans WHERE customer_id = %s AND is_current = 1", params=(customer_id,), fetchone=True)
    
    return render_template("customer/my_plan.html", customer=customer, plan=plan, is_admin_view=True)

@admin_bp.route("/customer/<int:customer_id>/serve-report")
@admin_required
def serve_html_report(customer_id):
    """Securely serve the uploaded HTML report so the browser doesn't block it."""
    plan = query(
        "SELECT html_file_path FROM financial_plans WHERE customer_id = %s AND is_current = 1", 
        params=(customer_id,), 
        fetchone=True
    )
    
    if not plan or not plan.get("html_file_path"):
        return "No HTML report attached to this plan.", 404
        
    filepath = plan["html_file_path"]
    
    import os
    if os.path.exists(filepath):
        # send_file safely transmits the local file to the web browser
        return send_file(filepath, mimetype='text/html')
    else:
        return "File path found in database, but physical file is missing.", 404