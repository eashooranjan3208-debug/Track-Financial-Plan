from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import os 
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "parsers"))
from app.database import query, bulk_query, get_db
import re
from parse_plan_json import parse_plan_json
from parse_transactions import parse_transactions
from parse_portfolio import parse_portfolio
from parse_asset_allocation import parse_asset_allocation
import shutil
from app.utils import classify_file, find_all_files, make_upload_path, extract_date_from_filename

SHA256_RE = re.compile(r"^[a-f0-9]{64}$", re.IGNORECASE)

def decode_pan(raw_pan):
    """
    Safely cleans byte-encoded PANs from Excel cells.
    Leaves normal strings untouched for production.
    """
    if not raw_pan:
        return None
        
    if isinstance(raw_pan, bytes):
        return raw_pan.decode("utf-8").strip()
        
    raw_str = str(raw_pan).strip()
    if raw_str.startswith("b'") and raw_str.endswith("'"):
        return raw_str[2:-1]
    if raw_str.startswith('b"') and raw_str.endswith('"'):
        return raw_str[2:-1]
        
    return raw_str


def _sha256_pan(pan):
    if not pan:
        return None
    return hashlib.sha256(str(pan).strip().encode()).hexdigest()


def _lookup_keys_for_pan(raw_pan):
    """
    Return all identifiers that may appear in uploaded files for one customer PAN.
    Uploads can contain either the plain PAN or its SHA-256 digest.
    """
    clean_pan = decode_pan(raw_pan)
    if not clean_pan:
        return set()

    keys = {clean_pan}
    if SHA256_RE.match(clean_pan):
        keys.add(clean_pan.lower())
        return keys

    upper_pan = clean_pan.upper()
    lower_pan = clean_pan.lower()
    keys.update({upper_pan, lower_pan})
    keys.add(_sha256_pan(clean_pan))
    keys.add(_sha256_pan(upper_pan))
    keys.add(_sha256_pan(lower_pan))
    return {key for key in keys if key}


def _customer_lookup_from_rows(rows, value_factory):
    lookup = {}
    for row in rows or []:
        for key in _lookup_keys_for_pan(row.get("pan")):
            lookup[key] = value_factory(row)
    return lookup


def _lookup_customer_value(customer_lookup, raw_pan):
    for key in _lookup_keys_for_pan(raw_pan):
        value = customer_lookup.get(key)
        if value:
            return value
    return None


def _lookup_customer_id(customer_lookup, raw_pan):
    return _lookup_customer_value(customer_lookup, raw_pan)


CUSTOMER_PORTFOLIO_DELETE_SQL = """
    DELETE FROM customer_portfolio_snapshots
    WHERE customer_id = %s AND snapshot_date = %s
"""

CUSTOMER_ASSET_ALLOCATION_DELETE_SQL = """
    DELETE FROM customer_asset_allocation_snapshots
    WHERE customer_id = %s AND snapshot_date = %s
"""

ASSET_ALLOCATION_DELETE_SQL = """
    DELETE FROM asset_allocation
    WHERE customer_id = %s AND year = %s
"""

PORTFOLIO_VALUES_DELETE_SQL = """
    DELETE FROM portfolio_values
    WHERE customer_id = %s AND year = %s
"""

YEARLY_INVESTMENTS_DELETE_SQL = """
    DELETE FROM yearly_investments
    WHERE customer_id = %s AND year = %s
"""


def _hash_date(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value or "")


def _hash_amount(value):
    try:
        amount = Decimal(str(value if value is not None else 0)).normalize()
    except (InvalidOperation, TypeError, ValueError):
        return str(value or "")
    return format(amount, "f")


def generate_txn_hash(pan_source, txn_date, amount, source_row_id=None):
    """
    SHA256(pan_source + transaction_date + total_amount + source_row_id).

    Source files can contain multiple legitimate rows with the same PAN/date/amount.
    Including source_row_id only for colliding rows keeps those rows distinct while
    preserving existing hashes for rows that were already safely unique.
    """
    raw_string = f"{pan_source or ''}|{_hash_date(txn_date)}|{_hash_amount(amount)}"
    if source_row_id is not None:
        raw_string = f"{raw_string}|{source_row_id}"
    return hashlib.sha256(raw_string.encode("utf-8")).hexdigest()


def _transaction_pan_source(row):
    return row.get("pan_source") or row.get("pan")


def _transaction_source_row_id(row, row_index):
    source_row_id = row.get("source_row_id")
    return row_index if source_row_id is None else source_row_id


def _transaction_hash_for_row(row, row_index, pan_source, seen_base_hashes):
    base_hash = generate_txn_hash(
        pan_source,
        row["transaction_date"],
        row["total_amount"],
    )
    if base_hash not in seen_base_hashes:
        seen_base_hashes.add(base_hash)
        return base_hash

    return generate_txn_hash(
        pan_source,
        row["transaction_date"],
        row["total_amount"],
        _transaction_source_row_id(row, row_index),
    )


def _extract_snapshot_keys(rows, customer_lookup):
    keys = set()
    for row in rows or []:
        customer_id = _lookup_customer_id(customer_lookup, row.get("pan"))
        snapshot_date = row.get("snapshot_date")
        if customer_id and snapshot_date:
            keys.add((customer_id, snapshot_date))
    return keys


def _extract_year_keys(rows, customer_lookup, year_field="year"):
    keys = set()
    for row in rows or []:
        customer_id = _lookup_customer_id(customer_lookup, row.get("pan"))
        year = row.get(year_field)
        if customer_id and year:
            keys.add((customer_id, int(year)))
    return keys


def _delete_insert_transaction(delete_sql, affected_keys, insert_sql, insert_params):
    connection = get_db()
    cursor = None
    try:
        cursor = connection.cursor()
        for key in sorted(affected_keys):
            cursor.execute(delete_sql, key)
        cursor.executemany(insert_sql, insert_params)
        inserted = cursor.rowcount
        connection.commit()
        return inserted
    except Exception:
        connection.rollback()
        raise
    finally:
        if cursor:
            cursor.close()
        connection.close()

def get_dashboard_stats():
    customers = query(
        "SELECT COUNT(*) AS total FROM customers WHERE is_active = 1",
        fetchone=True
    )
    plans = query(
        "SELECT COUNT(*) AS total FROM financial_plans WHERE is_current = 1",
        fetchone=True
    )
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
    
    
    last_upload = query(
        "SELECT MAX(uploaded_at) AS last_date FROM financial_plans",
        fetchone=True
    )
    
    last_date_str = None
    if last_upload and last_upload.get("last_date"):
        
        last_date_str = last_upload["last_date"].strftime("%d %b %Y")

    return {
        "active_customers": customers["total"] if customers else 0,
        "active_plans":     plans["total"] if plans else 0,
        "goals_at_risk":    goals_at_risk["total"] if goals_at_risk else 0,
        "recent_uploads":   recent_uploads["total"] if recent_uploads else 0,
        "last_upload_date": last_date_str  
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
    param_list = []
    skipped = 0
    seen_base_hashes = set()
    
    for row_index, row in enumerate(rows):
        customer_id = _lookup_customer_id(customer_lookup, row.get("pan"))
        if not customer_id:
            print(
                "[insert_transactions] skipped "
                f"source_row_id={row.get('source_row_id')} reason=unmatched PAN pan={row.get('pan')}"
            )
            skipped += 1
            continue

        pan_source = _transaction_pan_source(row)
        transaction_hash = _transaction_hash_for_row(
            row,
            row_index,
            pan_source,
            seen_base_hashes,
        )

        param_list.append((
            customer_id,
            pan_source,
            row["transaction_date"],
            row["total_amount"],
            row.get("applicant_name"),
            transaction_hash,
        ))

    if not param_list:
        return 0, skipped

    sql = """
        INSERT IGNORE INTO customer_transactions (
            customer_id, pan_source, transaction_date, total_amount,
            applicant_name, transaction_hash
        ) VALUES (%s, %s, %s, %s, %s, %s)
    """
    inserted_count = bulk_query(sql, param_list)
    duplicate_count = len(param_list) - inserted_count
    return inserted_count, skipped + duplicate_count


def insert_portfolio_snapshots(rows, customer_lookup):
    """
    Actual customer_portfolio_snapshots columns:
    customer_id, snapshot_date, pan_source, current_value
    No pan, no account_type columns.
    """
    insert_params = []
    affected_keys = set()
    skipped = 0
    for row in rows:
        customer_id = _lookup_customer_id(customer_lookup, row.get("pan"))
        snapshot_date = row.get("snapshot_date")
        if not customer_id or not snapshot_date:
            skipped += 1
            continue
        affected_keys.add((customer_id, snapshot_date))
        insert_params.append((
            customer_id,
            snapshot_date,
            row.get("pan_source"),
            row.get("current_value", 0),
        ))

    if not insert_params:
        return 0, skipped

    insert_sql = """
        INSERT INTO customer_portfolio_snapshots (
            customer_id, snapshot_date, pan_source, current_value
        ) VALUES (%s,%s,%s,%s)
    """
    inserted = _delete_insert_transaction(
        CUSTOMER_PORTFOLIO_DELETE_SQL,
        affected_keys,
        insert_sql,
        insert_params,
    )
    return inserted, skipped


def insert_asset_allocation_snapshots(rows, customer_lookup):
    """
    Table: customer_asset_allocation_snapshots.
    Columns: customer_id, snapshot_date, asset_name,
             target_pct, current_pct, current_value,
             target_value, raw_diff, final_trade
    """
    insert_params = []
    affected_keys = set()
    skipped = 0
    
    for row in rows:
        customer_id = _lookup_customer_id(customer_lookup, row.get("pan"))
        snapshot_date = row.get("snapshot_date")

        if not customer_id or not snapshot_date:
            skipped += 1
            continue
            
        try:
            # Bulletproof Casting
            target_pct    = float(row.get("target_pct") or 0)
            current_pct   = float(row.get("current_pct") or 0)
            current_value = float(row.get("current_value") or 0)
            target_value  = float(row.get("target_value") or 0)
            raw_diff      = float(row.get("raw_diff") or 0)
            final_trade   = float(row.get("final_trade") or 0)
            
            # Safely get the asset name
            asset_name_val = row.get("asset_name") or row.get("asset_class") or "Unknown"

            affected_keys.add((customer_id, snapshot_date))
            insert_params.append((
                customer_id,
                snapshot_date,
                asset_name_val,
                target_pct,
                current_pct,
                current_value,
                target_value,
                raw_diff,
                final_trade,
            ))
            
        except Exception as e:
            print(f"❌ [DB ERROR] Failed to insert Asset Allocation row: {e}")
            skipped += 1

    if not insert_params:
        return 0, skipped

    insert_sql = """
        INSERT INTO customer_asset_allocation_snapshots (
            customer_id, snapshot_date, asset_name,
            target_pct, current_pct, current_value,
            target_value, raw_diff, final_trade
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """
    inserted = _delete_insert_transaction(
        CUSTOMER_ASSET_ALLOCATION_DELETE_SQL,
        affected_keys,
        insert_sql,
        insert_params,
    )
    return inserted, skipped

def build_customer_lookup():
    """
    Builds a dictionary mapping BOTH raw PANs and SHA-256 Hashed PANs to customer IDs.
    Used by the Excel parsers (transactions, portfolio, asset allocation).
    """
    rows = query("SELECT id, pan FROM customers WHERE is_active = 1")
    lookup = {}

    for row in rows or []:
        if row.get("pan"):
            raw_pan = str(row["pan"]).strip().upper()
            customer_id = row["id"]

            # 1. Map the raw PAN (e.g., 'TESTP1234X' -> ID 1)
            lookup[raw_pan] = customer_id

            # 2. Map the hashed PAN (e.g., 'a6e7f4af...' -> ID 1)
            hashed_pan = hashlib.sha256(raw_pan.encode('utf-8')).hexdigest()
            lookup[hashed_pan] = customer_id

    return lookup


def process_bulk_upload(extract_dir, save_uploads_to="uploads"):
    """
    Process a directory of extracted bulk-upload files.
    """
    customer_lookup = build_customer_lookup()
    customer_rows   = query("SELECT id, pan, name FROM customers WHERE is_active = 1")
    pan_lookup = _customer_lookup_from_rows(customer_rows, lambda row: row)

    hashed_pan_lookup = {}
    for row in customer_rows or []:
        if row.get("pan"):
            raw_pan = str(row["pan"]).strip().upper()
            hashed_val = hashlib.sha256(raw_pan.encode('utf-8')).hexdigest()
            hashed_pan_lookup[hashed_val] = row

    results = {
        "plan_json": [], "report_html": [],
        "transactions": [], "portfolio": [], "asset_allocation": [],
        "unrecognized": [],
    }

    html_paths_by_pan = {}
    all_files = find_all_files(extract_dir)

    classified = []
    for full_path, filename in all_files:
        file_type, clean_pan = classify_file(filename)
        if file_type is None:
            results["unrecognized"].append(filename)
            continue
        classified.append((full_path, filename, file_type, clean_pan))
        
    # --- 1. PROCESS HTML REPORTS ---
    for full_path, filename, file_type, pan in classified:
        if file_type != "report_html":
            continue
        customer = hashed_pan_lookup.get(pan) or _lookup_customer_value(pan_lookup, pan)
        if not customer:
            results["report_html"].append({
                "filename": filename, "pan": pan, "customer_name": None,
                "status": "unmatched", "detail": f"No customer found for PAN {pan}"
            })
            continue
        try:
            _, dest_path = make_upload_path(pan, "report", "html")
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            shutil.copy2(full_path, dest_path)
            html_paths_by_pan[pan] = dest_path
            
            results["report_html"].append({
                "filename": filename, "pan": pan, "customer_name": customer["name"],
                "status": "saved", "detail": "Report stored"
            })
        except Exception as e:
            results["report_html"].append({
                "filename": filename, "pan": pan, "customer_name": customer["name"],
                "status": "failed", "detail": str(e)
            })

    # --- 2. PROCESS JSON PLANS ---
    for full_path, filename, file_type, pan in classified:
        if file_type != "plan_json":
            continue
        customer = hashed_pan_lookup.get(pan) or _lookup_customer_value(pan_lookup, pan)
        if not customer:
            results["plan_json"].append({
                "filename": filename, "pan": pan, "customer_name": None,
                "status": "unmatched", "detail": f"No customer found for PAN {pan}"
            })
            continue
        try:
            _, dest_path = make_upload_path(pan, "plan", "json")
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            shutil.copy2(full_path, dest_path)

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
                "filename": filename, "pan": pan, "customer_name": customer["name"],
                "status": "imported", "detail": f"{len(parsed['goals'])} goals, {len(parsed['cashflow'])} cashflow rows"
            })
        except Exception as e:
            results["plan_json"].append({
                "filename": filename, "pan": pan, "customer_name": customer["name"],
                "status": "failed", "detail": str(e)
            })

    # --- 3. PROCESS SYSTEM FILES (Excel) ---
    for full_path, filename, file_type, pan in classified:
        if file_type == "transactions":
            try:
                upload_date = extract_date_from_filename(filename)
                rows        = parse_transactions(full_path, upload_date)
                ins, skp    = insert_transactions(rows, customer_lookup)
                
                _, dest_path = make_upload_path(None, "transactions", "xlsx")
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                shutil.copy2(full_path, dest_path)
                
                results["transactions"].append({
                    "filename": filename, "inserted": ins, "skipped": skp,
                    "detail": f"{ins} inserted, {skp} skipped (duplicates/unmatched PAN)"
                })
            except Exception as e:
                results["transactions"].append({
                    "filename": filename, "inserted": 0, "skipped": 0, "detail": f"Error: {e}"
                })

        elif file_type == "portfolio":
            try:
                snapshot_date = extract_date_from_filename(filename)
                rows          = parse_portfolio(full_path, snapshot_date)
                ins, skp      = insert_portfolio_snapshots(rows, customer_lookup)
                
                _, dest_path  = make_upload_path(None, "portfolio", "xlsx")
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                shutil.copy2(full_path, dest_path)
                
                results["portfolio"].append({
                    "filename": filename, "inserted": ins, "skipped": skp,
                    "detail": f"{ins} inserted, {skp} skipped (unmatched PAN)"
                })
            except Exception as e:
                results["portfolio"].append({
                    "filename": filename, "inserted": 0, "skipped": 0, "detail": f"Error: {e}"
                })

        elif file_type == "asset_allocation":
            try:
                snapshot_date = extract_date_from_filename(filename)
                aa_result     = parse_asset_allocation(full_path, snapshot_date)
                ins, skp      = insert_asset_allocation_snapshots(aa_result["rows"], customer_lookup)
                
                _, dest_path  = make_upload_path(None, "asset_allocation", "xlsx")
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                shutil.copy2(full_path, dest_path)
                
                results["asset_allocation"].append({
                    "filename": filename, "inserted": ins, "skipped": skp,
                    "detail": f"{ins} inserted, {skp} skipped (unmatched PAN), format={aa_result['format']}"
                })
            except Exception as e:
                results["asset_allocation"].append({
                    "filename": filename, "inserted": 0, "skipped": 0, "detail": f"Error: {e}"
                })

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


def classify_file(filename):
    """
    Backwards-compatible wrapper around the shared filename classifier.
    """
    from app.utils import classify_file as shared_classify_file
    return shared_classify_file(filename)
    
def calculate_file_hash(filepath):
    """Generates a SHA-256 hash of the entire file."""
    sha256_hash = hashlib.sha256()
    with open(filepath, "rb") as f:
        # Read in chunks to handle large files efficiently
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()

def process_transactions_upload(filepath, original_filename, customer_id):
    file_hash = calculate_file_hash(filepath)
    
    # 1. GATEKEEPER: Check if file was already uploaded
    existing_upload = query(
        "SELECT id FROM file_uploads WHERE customer_id = %s AND file_type = 'transactions' AND file_hash = %s",
        params=(customer_id, file_hash)
    )
    if existing_upload:
        # Delete the redundant file from the server immediately
        if os.path.exists(filepath):
            os.remove(filepath)
        return {"status": "error", "message": "This file has already been uploaded and processed."}

    # 2. Record the upload attempt
    query(
        "INSERT INTO file_uploads (customer_id, file_type, original_filename, file_hash, status) VALUES (%s, %s, %s, %s, 'processing')",
        params=(customer_id, 'transactions', original_filename, file_hash),
        commit=True
    )
    
    try:
        rows = parse_transactions(filepath)
        inserted_count = 0
        skipped_count = 0
        seen_base_hashes = set()
        
        # 3. Insert rows safely
        for row_index, row in enumerate(rows):
            txn_date = row.get("transaction_date")
            amount = row.get("total_amount")
            pan_source = _transaction_pan_source(row) or f"customer:{customer_id}"
            
            # Generate the unique row-level hash
            txn_hash = _transaction_hash_for_row(
                row,
                row_index,
                pan_source,
                seen_base_hashes,
            )
            
            # INSERT IGNORE completely bypasses the error if the txn_hash already exists
            result = query(
                """
                INSERT IGNORE INTO customer_transactions (
                    customer_id, pan_source, transaction_date, total_amount,
                    applicant_name, transaction_hash
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                params=(
                    customer_id,
                    pan_source,
                    txn_date,
                    amount,
                    row.get("applicant_name"),
                    txn_hash,
                ),
                commit=True 
            )
            
            # If rowAffected is 0, it means INSERT IGNORE skipped it (duplicate)
            if result and result > 0:
                inserted_count += 1
            else:
                skipped_count += 1

        # 4. Mark Batch as Success
        query(
            "UPDATE file_uploads SET status = 'success', row_count = %s, processed_at = NOW() WHERE file_hash = %s",
            params=(inserted_count, file_hash),
            commit=True
        )
        
        return {"status": "success", "message": f"Success: {inserted_count} new inserted, {skipped_count} duplicates skipped."}

    except Exception as e:
        # 5. Mark Batch as Failed
        print(f"Upload Error: {e}")
        query(
            "UPDATE file_uploads SET status = 'failed', error_message = %s WHERE file_hash = %s",
            params=(str(e), file_hash),
            commit=True
        )
        return {"status": "error", "message": "Failed to process file."}
