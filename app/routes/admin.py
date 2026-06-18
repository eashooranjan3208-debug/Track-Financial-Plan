import os
import sys
from datetime import datetime
from functools import wraps
from flask import (Blueprint, render_template, request,
                   redirect, url_for, flash, session)

# Utils & Services
from app.utils import (
    make_upload_path, extract_date_from_filename,
    extract_zip, cleanup_temp_dir
)
from app.database import query
from app.services.customer_service import get_all_customers
from app.services.plan_service import get_dashboard_data
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
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in types

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

# ── SINGLE FILE UPLOAD ─────────────────────────────────────────
@admin_bp.route("/upload", methods=["GET", "POST"])
@admin_required
def upload():
    customers = get_all_customers()

    if request.method == "GET":
        return render_template("admin/upload.html", customers=customers)

    customer_id = request.form.get("customer_id")
    if not customer_id:
        flash("Please select a customer.", "warning")
        return render_template("admin/upload.html", customers=customers)

    customer_id = int(customer_id)
    customer = query("SELECT id, pan, name FROM customers WHERE id = %s", params=(customer_id,), fetchone=True)
    
    if not customer:
        flash("Customer not found.", "danger")
        return render_template("admin/upload.html", customers=customers)

    pan = customer["pan"]
    results = {}
    plan_id = None

    # 1. HTML file
    html_file = request.files.get("html_file")
    html_file_path = None
    if html_file and html_file.filename:
        if not _allowed(html_file.filename, ALLOWED_EXTENSIONS["html"]):
            flash("HTML file must be .html or .htm", "warning")
        else:
            _, path = make_upload_path(pan, "report", "html")
            html_file.save(path)
            html_file_path = path
            results["html"] = "✅ Saved"

    # 2. JSON file
    json_file = request.files.get("json_file")
    if json_file and json_file.filename:
        if not _allowed(json_file.filename, ALLOWED_EXTENSIONS["json"]):
            flash("Plan data file must be .json", "warning")
        else:
            try:
                _, path = make_upload_path(pan, "plan", "json")
                json_file.save(path)
                json_file_path = path
                parsed = parse_plan_json(path)

                archive_existing_plans(customer_id)
                plan_id = create_plan_record(
                    customer_id=customer_id, plan_data=parsed["plan"],
                    html_file_path=html_file_path, json_file_path=json_file_path,
                    ingestion_source="upload"
                )

                insert_family_members(plan_id, parsed["family"])
                insert_goals(plan_id, parsed["goals"])
                insert_cashflow(plan_id, parsed["cashflow"])
                insert_current_assets(plan_id, parsed["current_assets"])
                insert_retirement_expenses(plan_id, parsed["retirement_expenses"])
                insert_other_assets(customer_id, plan_id, parsed["other_assets"])

                results["json"] = "✅ Plan imported successfully!"
            except Exception as e:
                results["json"] = f"❌ Error: {str(e)}"
                flash(f"JSON parse error: {str(e)}", "danger")

    elif html_file_path:
        existing = query("SELECT id FROM financial_plans WHERE customer_id=%s AND is_current=1", params=(customer_id,), fetchone=True)
        if existing:
            query("UPDATE financial_plans SET file_path=%s WHERE id=%s", params=(html_file_path, existing["id"]), commit=True)
            results["html"] = "✅ HTML report linked to existing plan"

    if not results:
        flash("No files were uploaded.", "warning")
        return render_template("admin/upload.html", customers=customers)

    return render_template("admin/upload_result.html", customer=customer, results=results, plan_id=plan_id)


# ── MILESTONE 3: BULK ZIP UPLOAD ───────────────────────────────
@admin_bp.route("/bulk-upload", methods=["GET", "POST"])
@admin_required
def bulk_upload():
    if request.method == "GET":
        return render_template("admin/bulk_upload.html")

    zip_file = request.files.get("bulk_zip")
    if not zip_file or not zip_file.filename:
        flash("Please select a ZIP file to upload.", "warning")
        return render_template("admin/bulk_upload.html")

    if not zip_file.filename.lower().endswith(".zip"):
        flash("File must be a .zip archive.", "warning")
        return render_template("admin/bulk_upload.html")

    extract_dir, temp_dir = extract_zip(zip_file)
    try:
        results = process_bulk_upload(extract_dir)
    finally:
        cleanup_temp_dir(temp_dir)

    return render_template("admin/bulk_upload_result.html", results=results)


# ── Temporary Developer Backdoor ───────────────────────────────
@admin_bp.route("/dev-login")
def dev_login():
    """Bypasses the login screen so you can test the admin portal MVP."""
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

    return render_template(
        "customer/dashboard.html",
        is_admin_view=True,
        customer=customer,
        customer_id=customer_id,
        **data
    )