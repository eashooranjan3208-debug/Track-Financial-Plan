from app.database import query
from datetime import datetime
import os 
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "parsers"))


def get_dashboard_stats():
    customers = query(
        "SELECT COUNT(*) AS total FROM customers WHERE is_active = 1",
        fetchone=True
    )
    plans = query(
        "SELECT COUNT(*) AS total FROM financial_plans WHERE is_current = 1",
        fetchone=True
    )
    # plan_goals now exists after migration 3
    goals_at_risk = query(
        "SELECT COUNT(*) AS total FROM plan_goals WHERE probability < 95",
        fetchone=True
    )
    recent_uploads = query(
        """
        SELECT COUNT(*) AS total FROM financial_plans
        WHERE uploaded_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
        """,
        fetchone=True
    )
    return {
        "active_customers": customers["total"]    if customers    else 0,
        "active_plans":     plans["total"]        if plans        else 0,
        "goals_at_risk":    goals_at_risk["total"] if goals_at_risk else 0,
        "recent_uploads":   recent_uploads["total"] if recent_uploads else 0,
    }


def archive_existing_plans(customer_id):
    query(
        "UPDATE financial_plans SET is_current = 0 WHERE customer_id = %s AND is_current = 1",
        params=(customer_id,),
        commit=True
    )


def create_plan_record(customer_id, plan_data,
                       html_file_path=None,
                       json_file_path=None,
                       ingestion_source="upload"):
    """
    Insert into financial_plans using actual + new migrated columns.
    file_path kept for backwards compatibility (set to json_file_path).
    """
    file_path = json_file_path or html_file_path or ""
    query(
        """
        INSERT INTO financial_plans (
            customer_id, plan_year, file_path, is_current,
            plan_start_date, end_of_plan_year,
            ret_age, end_age, ret_start_year, ret_expected_expenses,
            target_surplus, growth_target_surplus,
            risk_category, risk_description,
            weight_equity, weight_debt, weight_liquid, weight_gold,
            expected_return, std_deviation,
            advisor_comment, allocation_comment,
            html_file_path, json_file_path, ingestion_source
        ) VALUES (
            %s,%s,%s,1,
            %s,%s,
            %s,%s,%s,%s,
            %s,%s,
            %s,%s,
            %s,%s,%s,%s,
            %s,%s,
            %s,%s,
            %s,%s,%s
        )
        """,
        params=(
            customer_id,
            plan_data.get("plan_year"),
            file_path,
            plan_data.get("plan_start_date"),
            plan_data.get("end_of_plan_year"),
            plan_data.get("ret_age"),
            plan_data.get("end_age"),
            plan_data.get("ret_start_year"),
            plan_data.get("ret_expected_expenses"),
            plan_data.get("target_surplus"),
            plan_data.get("growth_target_surplus"),
            plan_data.get("risk_category"),
            plan_data.get("risk_description"),
            plan_data.get("weight_equity"),
            plan_data.get("weight_debt"),
            plan_data.get("weight_liquid"),
            plan_data.get("weight_gold"),
            plan_data.get("expected_return"),
            plan_data.get("std_deviation"),
            plan_data.get("advisor_comment"),
            plan_data.get("allocation_comment"),
            html_file_path,
            json_file_path,
            ingestion_source,
        ),
        commit=True
    )
    result = query(
        """
        SELECT id FROM financial_plans
        WHERE customer_id = %s AND is_current = 1
        ORDER BY uploaded_at DESC LIMIT 1
        """,
        params=(customer_id,),
        fetchone=True
    )
    return result["id"] if result else None


def insert_family_members(plan_id, members):
    for m in members:
        query(
            """
            INSERT INTO plan_family_members
                (plan_id, member_name, age, occupation, relation, risk_profile)
            VALUES (%s,%s,%s,%s,%s,%s)
            """,
            params=(
                plan_id, m.get("member_name"), m.get("age"),
                m.get("occupation"), m.get("relation"), m.get("risk_profile"),
            ),
            commit=True
        )


def insert_goals(plan_id, goals):
    for g in goals:
        query(
            """
            INSERT INTO plan_goals (
                plan_id, goal_name, goal_type, criticality,
                current_price, inflation_rate, goal_year, time_period_yrs,
                upfront_amount, emi_amount, loan_term_yrs,
                future_price, status_text, probability
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            params=(
                plan_id,
                g.get("goal_name"), g.get("goal_type"), g.get("criticality"),
                g.get("current_price", 0), g.get("inflation_rate", 0),
                g.get("goal_year"), g.get("time_period_yrs", 0),
                g.get("upfront_amount", 0), g.get("emi_amount", 0),
                g.get("loan_term_yrs", 0), g.get("future_price", 0),
                g.get("status_text"), g.get("probability", 0),
            ),
            commit=True
        )


def insert_cashflow(plan_id, cashflow_rows):
    for row in cashflow_rows:
        query(
            """
            INSERT IGNORE INTO plan_cashflow (
                plan_id, calendar_year,
                projected_investments, projected_expenses,
                projected_portfolio_value
            ) VALUES (%s,%s,%s,%s,%s)
            """,
            params=(
                plan_id, row.get("calendar_year"),
                row.get("projected_investments", 0),
                row.get("projected_expenses", 0),
                row.get("projected_portfolio_value", 0),
            ),
            commit=True
        )


def insert_current_assets(plan_id, assets):
    for a in assets:
        query(
            """
            INSERT INTO plan_current_assets
                (plan_id, asset_name, asset_class, current_value)
            VALUES (%s,%s,%s,%s)
            """,
            params=(
                plan_id, a.get("asset_name"),
                a.get("asset_class"), a.get("current_value", 0),
            ),
            commit=True
        )


def insert_retirement_expenses(plan_id, expenses):
    for e in expenses:
        query(
            """
            INSERT INTO plan_retirement_expenses
                (plan_id, expense_name, annual_value, inflation_rate)
            VALUES (%s,%s,%s,%s)
            """,
            params=(
                plan_id, e.get("expense_name"),
                e.get("annual_value", 0), e.get("inflation_rate", 5),
            ),
            commit=True
        )


def insert_other_assets(customer_id, plan_id, assets):
    """
    maturity_date is a DATE column in actual schema.
    Convert integer year (e.g. 2038) to proper date (2038-01-01).
    """
    for a in assets:
        raw_year    = a.get("maturity_year")
        maturity_dt = None
        if raw_year:
            try:
                maturity_dt = f"{int(raw_year)}-01-01"
            except (ValueError, TypeError):
                maturity_dt = None

        query(
            """
            INSERT INTO other_assets (
                customer_id, asset_name, asset_type,
                current_value, maturity_value,
                annual_contribution, maturity_date, notes
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            params=(
                customer_id,
                a.get("asset_name"), a.get("asset_type"),
                a.get("current_value", 0), a.get("maturity_value", 0),
                a.get("annual_contribution", 0),
                maturity_dt, a.get("notes"),
            ),
            commit=True
        )


def insert_transactions(rows, customer_lookup):
    """
    Actual customer_transactions columns:
    customer_id, pan_source, transaction_date,
    total_amount, applicant_name
    No source_row_id, no pan, no account_type, no entry_type.
    """
    inserted = skipped = 0
    for row in rows:
        customer_id = customer_lookup.get(row.get("pan"))
        if not customer_id:
            skipped += 1
            continue
        try:
            query(
                """
                INSERT INTO customer_transactions (
                    customer_id, pan_source,
                    transaction_date, total_amount, applicant_name
                ) VALUES (%s,%s,%s,%s,%s)
                """,
                params=(
                    customer_id,
                    row.get("pan_source"),
                    row["transaction_date"],
                    row["total_amount"],
                    row.get("applicant_name"),
                ),
                commit=True
            )
            inserted += 1
        except Exception:
            skipped += 1
    return inserted, skipped


def insert_portfolio_snapshots(rows, customer_lookup):
    """
    Actual customer_portfolio_snapshots columns:
    customer_id, snapshot_date, pan_source, current_value
    No pan, no account_type columns.
    """
    inserted = skipped = 0
    for row in rows:
        customer_id = customer_lookup.get(row.get("pan"))
        if not customer_id:
            skipped += 1
            continue
        try:
            query(
                """
                INSERT IGNORE INTO customer_portfolio_snapshots (
                    customer_id, snapshot_date, pan_source, current_value
                ) VALUES (%s,%s,%s,%s)
                """,
                params=(
                    customer_id,
                    row["snapshot_date"],
                    row.get("pan_source"),
                    row.get("current_value", 0),
                ),
                commit=True
            )
            inserted += 1
        except Exception:
            skipped += 1
    return inserted, skipped


def insert_asset_allocation_snapshots(rows, customer_lookup):
    """
    Table is 'asset_allocation' (confirmed existing).
    Columns: customer_id, snapshot_date, asset_name,
             target_pct, current_pct, current_value,
             target_value, raw_diff, final_trade
    """
    inserted = skipped = 0
    for row in rows:
        pan         = row.get("pan")
        customer_id = customer_lookup.get(pan) if pan else None
        if not customer_id:
            skipped += 1
            continue
        try:
            query(
                """
                INSERT INTO asset_allocation (
                    customer_id, snapshot_date, asset_name,
                    target_pct, current_pct, current_value,
                    target_value, raw_diff, final_trade
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                params=(
                    customer_id,
                    row.get("snapshot_date"),
                    row.get("asset_class") or row.get("asset_name"),
                    row.get("target_pct", 0),
                    row.get("current_pct", 0),
                    row.get("current_value", 0),
                    row.get("target_value", 0),
                    row.get("raw_diff", 0),
                    row.get("final_trade", 0),
                ),
                commit=True
            )
            inserted += 1
        except Exception:
            skipped += 1
    return inserted, skipped


def build_customer_lookup():
    """Returns {pan: customer_id} for all active customers."""
    rows = query("SELECT id, pan FROM customers WHERE is_active = 1")
    return {row["pan"]: row["id"] for row in rows} if rows else {}


def process_bulk_upload(extract_dir, save_uploads_to="uploads"):
    """
    Process a directory of extracted bulk-upload files.

    For each file:
      - Classify by filename pattern
      - Per-customer files (plan_json, report_html): match PAN from filename
      - System-wide files (transactions, portfolio, asset_allocation):
        parse and match PAN per-row using customer_lookup
      - Save file to permanent storage using existing naming convention
      - Call existing parsers + insert functions (unchanged)

    Returns a results dict:
      {
        "plan_json":        [{filename, pan, customer_name, status, detail}, ...],
        "report_html":      [...],
        "transactions":     {filename, inserted, skipped, detail},
        "portfolio":        {filename, inserted, skipped, detail},
        "asset_allocation": {filename, inserted, skipped, detail},
        "unrecognized":     [filenames...],
      }
    """
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "parsers"))
    from parse_plan_json import parse_plan_json
    from parse_transactions import parse_transactions
    from parse_portfolio import parse_portfolio
    from parse_asset_allocation import parse_asset_allocation
    from app.utils import classify_file, find_all_files, make_upload_path, extract_date_from_filename

    customer_lookup = build_customer_lookup()
    pan_lookup       = {row["pan"]: row for row in query(
        "SELECT id, pan, name FROM customers WHERE is_active = 1"
    )} if customer_lookup else {}

    results = {
        "plan_json": [], "report_html": [],
        "transactions": None, "portfolio": None, "asset_allocation": None,
        "unrecognized": [],
    }

    # Track HTML files by PAN so we can link them to plans
    # processed in the same batch (HTML may come before or after JSON)
    html_paths_by_pan = {}

    all_files = find_all_files(extract_dir)

    # ── Pass 1: classify everything, save permanent copies ─────
    classified = []
    for full_path, filename in all_files:
        file_type, pan = classify_file(filename)
        if file_type is None:
            results["unrecognized"].append(filename)
            continue
        classified.append((full_path, filename, file_type, pan))

    # ── Pass 2: handle report.html first (so JSON can link them) ─
    for full_path, filename, file_type, pan in classified:
        if file_type != "report_html":
            continue
        customer = pan_lookup.get(pan)
        if not customer:
            results["report_html"].append({
                "filename": filename, "pan": pan,
                "customer_name": None,
                "status": "unmatched", "detail": f"No customer found for PAN {pan}"
            })
            continue
        try:
            _, dest_path = make_upload_path(pan, "report", "html")
            shutil_copy(full_path, dest_path)
            html_paths_by_pan[pan] = dest_path
            results["report_html"].append({
                "filename": filename, "pan": pan,
                "customer_name": customer["name"],
                "status": "saved", "detail": "Report stored"
            })
        except Exception as e:
            results["report_html"].append({
                "filename": filename, "pan": pan,
                "customer_name": customer["name"],
                "status": "failed", "detail": str(e)
            })

    # ── Pass 3: handle plan.json ────────────────────────────────
    for full_path, filename, file_type, pan in classified:
        if file_type != "plan_json":
            continue
        customer = pan_lookup.get(pan)
        if not customer:
            results["plan_json"].append({
                "filename": filename, "pan": pan,
                "customer_name": None,
                "status": "unmatched", "detail": f"No customer found for PAN {pan}"
            })
            continue
        try:
            _, dest_path = make_upload_path(pan, "plan", "json")
            shutil_copy(full_path, dest_path)

            parsed = parse_plan_json(dest_path)

            archive_existing_plans(customer["id"])
            plan_id = create_plan_record(
                customer_id    = customer["id"],
                plan_data      = parsed["plan"],
                html_file_path = html_paths_by_pan.get(pan),
                json_file_path = dest_path,
                ingestion_source = "upload",
            )

            insert_family_members(plan_id, parsed["family"])
            insert_goals(plan_id, parsed["goals"])
            insert_cashflow(plan_id, parsed["cashflow"])
            insert_current_assets(plan_id, parsed["current_assets"])
            insert_retirement_expenses(plan_id, parsed["retirement_expenses"])
            insert_other_assets(customer["id"], plan_id, parsed["other_assets"])

            results["plan_json"].append({
                "filename": filename, "pan": pan,
                "customer_name": customer["name"],
                "status": "imported",
                "detail": f"{len(parsed['goals'])} goals, {len(parsed['cashflow'])} cashflow rows"
            })
        except Exception as e:
            results["plan_json"].append({
                "filename": filename, "pan": pan,
                "customer_name": customer["name"],
                "status": "failed", "detail": str(e)
            })

    # ── Pass 4: system-wide files (transactions, portfolio, asset_allocation) ─
    for full_path, filename, file_type, pan in classified:
        if file_type == "transactions":
            try:
                upload_date = extract_date_from_filename(filename)
                rows        = parse_transactions(full_path, upload_date)
                ins, skp    = insert_transactions(rows, customer_lookup)
                _, dest_path = make_upload_path(None, "transactions", "xlsx")
                shutil_copy(full_path, dest_path)
                results["transactions"] = {
                    "filename": filename, "inserted": ins, "skipped": skp,
                    "detail": f"{ins} inserted, {skp} skipped (unmatched PAN)"
                }
            except Exception as e:
                results["transactions"] = {
                    "filename": filename, "inserted": 0, "skipped": 0,
                    "detail": f"Error: {e}"
                }

        elif file_type == "portfolio":
            try:
                snapshot_date = extract_date_from_filename(filename)
                rows          = parse_portfolio(full_path, snapshot_date)
                ins, skp      = insert_portfolio_snapshots(rows, customer_lookup)
                _, dest_path  = make_upload_path(None, "portfolio", "xlsx")
                shutil_copy(full_path, dest_path)
                results["portfolio"] = {
                    "filename": filename, "inserted": ins, "skipped": skp,
                    "detail": f"{ins} inserted, {skp} skipped (unmatched PAN)"
                }
            except Exception as e:
                results["portfolio"] = {
                    "filename": filename, "inserted": 0, "skipped": 0,
                    "detail": f"Error: {e}"
                }

        elif file_type == "asset_allocation":
            try:
                snapshot_date = extract_date_from_filename(filename)
                aa_result     = parse_asset_allocation(full_path, snapshot_date)
                ins, skp      = insert_asset_allocation_snapshots(
                                    aa_result["rows"], customer_lookup)
                _, dest_path  = make_upload_path(None, "asset_allocation", "xlsx")
                shutil_copy(full_path, dest_path)
                results["asset_allocation"] = {
                    "filename": filename, "inserted": ins, "skipped": skp,
                    "detail": f"{ins} inserted, {skp} skipped (unmatched PAN), "
                              f"format={aa_result['format']}"
                }
            except Exception as e:
                results["asset_allocation"] = {
                    "filename": filename, "inserted": 0, "skipped": 0,
                    "detail": f"Error: {e}"
                }

    return results


def shutil_copy(src, dst):
    """Small wrapper so admin_service doesn't need a top-level shutil import clash."""
    import shutil
    shutil.copy2(src, dst)


def get_or_create_plan_id(customer_id):
    """
    Return the current plan_id for a customer.
    If no plan exists (manual-entry customer), create a minimal
    financial_plans row so asset tables have somewhere to attach.
    """
    plan = query(
        "SELECT id FROM financial_plans WHERE customer_id = %s AND is_current = 1",
        params=(customer_id,), fetchone=True
    )
    if plan:
        return plan["id"]

    # No plan exists — create minimal manual-entry plan
    from datetime import date
    today = date.today()
    query(
        """
        INSERT INTO financial_plans (
            customer_id, plan_year, file_path,
            plan_start_date, is_current, ingestion_source
        ) VALUES (%s, %s, %s, %s, 1, 'manual')
        """,
        params=(customer_id, today.year, "", today),
        commit=True
    )
    result = query(
        "SELECT id FROM financial_plans WHERE customer_id = %s AND is_current = 1",
        params=(customer_id,), fetchone=True
    )
    return result["id"] if result else None


# ── Current Financial Assets CRUD ──────────────────────────────

def add_current_asset(customer_id, asset_name, asset_class, current_value):
    plan_id = get_or_create_plan_id(customer_id)
    query(
        """
        INSERT INTO plan_current_assets (plan_id, asset_name, asset_class, current_value)
        VALUES (%s, %s, %s, %s)
        """,
        params=(plan_id, asset_name, asset_class, current_value),
        commit=True
    )


def update_current_asset(row_id, asset_name, asset_class, current_value):
    query(
        """
        UPDATE plan_current_assets
        SET asset_name = %s, asset_class = %s, current_value = %s
        WHERE id = %s
        """,
        params=(asset_name, asset_class, current_value, row_id),
        commit=True
    )


def delete_current_asset(row_id):
    query(
        "DELETE FROM plan_current_assets WHERE id = %s",
        params=(row_id,), commit=True
    )


def get_current_asset_row(row_id):
    return query(
        "SELECT * FROM plan_current_assets WHERE id = %s",
        params=(row_id,), fetchone=True
    )

# ── Other Financial Investments (Held Away Assets) CRUD ────────

def add_other_asset(customer_id, asset_name, asset_type, current_value,
                     maturity_value, annual_contribution, maturity_date, notes):
    query(
        """
        INSERT INTO other_assets (
            customer_id, asset_name, asset_type, current_value,
            maturity_value, annual_contribution, maturity_date, notes
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        params=(
            customer_id, asset_name, asset_type, current_value,
            maturity_value, annual_contribution, maturity_date, notes,
        ),
        commit=True
    )


def update_other_asset(row_id, asset_name, asset_type, current_value,
                        maturity_value, annual_contribution, maturity_date, notes):
    query(
        """
        UPDATE other_assets
        SET asset_name = %s, asset_type = %s, current_value = %s,
            maturity_value = %s, annual_contribution = %s,
            maturity_date = %s, notes = %s
        WHERE id = %s
        """,
        params=(
            asset_name, asset_type, current_value,
            maturity_value, annual_contribution, maturity_date, notes, row_id,
        ),
        commit=True
    )


def delete_other_asset(row_id):
    query("DELETE FROM other_assets WHERE id = %s", params=(row_id,), commit=True)


def get_other_asset_row(row_id):
    return query(
        "SELECT * FROM other_assets WHERE id = %s",
        params=(row_id,), fetchone=True
    )