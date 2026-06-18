from app.database import query
from datetime import date


# ── Plan retrieval ─────────────────────────────────────────────

def get_current_plan(customer_id):
    """Fetch active plan. Only queries columns that actually exist."""
    return query(
        """
        SELECT id, customer_id, plan_year, file_path,
               plan_start_date, end_of_plan_year,
               ret_age, end_age, ret_start_year,
               ret_expected_expenses, target_surplus,
               growth_target_surplus, risk_category,
               risk_description, weight_equity, weight_debt,
               weight_liquid, weight_gold, expected_return,
               std_deviation, advisor_comment, allocation_comment,
               html_file_path, json_file_path,
               ingestion_source, is_current, uploaded_at
        FROM financial_plans
        WHERE customer_id = %s AND is_current = 1
        ORDER BY uploaded_at DESC
        LIMIT 1
        """,
        params=(customer_id,),
        fetchone=True
    )


def get_archived_plans(customer_id):
    return query(
        """
        SELECT id, plan_year, plan_start_date,
               file_path, html_file_path, uploaded_at
        FROM financial_plans
        WHERE customer_id = %s AND is_current = 0
        ORDER BY uploaded_at DESC
        """,
        params=(customer_id,)
    )


def get_plan_by_id(plan_id):
    return query(
        "SELECT * FROM financial_plans WHERE id = %s",
        params=(plan_id,),
        fetchone=True
    )


# ── Plan cycle (computed in Python, no extra DB call) ─────────

def get_current_cycle(plan):
    """
    Calculate current cycle from plan_start_date.
    Falls back to plan_year if plan_start_date is missing.
    Returns None if plan has no date information.
    """
    if not plan:
        return None

    start = plan.get("plan_start_date")

    # Fallback: if plan_start_date not populated, use Jan 1 of plan_year
    if not start and plan.get("plan_year"):
        try:
            from datetime import datetime
            start = datetime(int(str(plan["plan_year"])), 1, 1).date()
        except Exception:
            return None

    if not start:
        return None

    if isinstance(start, str):
        from datetime import datetime
        start = datetime.strptime(start, "%Y-%m-%d").date()

    today = date.today()

    try:
        from dateutil.relativedelta import relativedelta
    except ImportError:
        # Fallback without dateutil
        years_elapsed = today.year - start.year
        if (today.month, today.day) < (start.month, start.day):
            years_elapsed -= 1
        cycle_number = max(1, years_elapsed + 1)
        return {
            "cycle_number":  cycle_number,
            "cycle_start":   start.replace(year=start.year + years_elapsed),
            "cycle_end":     start.replace(year=start.year + years_elapsed + 1),
            "current_year":  today.year,
            "plan_start":    start,
        }

    years_elapsed = (
        today.year - start.year
        - (1 if (today.month, today.day) < (start.month, start.day) else 0)
    )
    years_elapsed = max(0, years_elapsed)
    cycle_number  = years_elapsed + 1
    cycle_start   = start + relativedelta(years=years_elapsed)
    cycle_end     = start + relativedelta(years=years_elapsed + 1, days=-1)

    return {
        "cycle_number": cycle_number,
        "cycle_start":  cycle_start,
        "cycle_end":    cycle_end,
        "current_year": today.year,
        "plan_start":   start,
    }


# ── Goals ──────────────────────────────────────────────────────

def get_goals(plan_id):
    """
    Fetch goals with computed track_status.
    Table: plan_goals (created by migration 3).
    """
    return query(
        """
        SELECT
            id, goal_name, goal_type, criticality,
            current_price, future_price, inflation_rate,
            goal_year, time_period_yrs,
            upfront_amount, emi_amount, loan_term_yrs,
            status_text, probability,
            CASE
                WHEN probability >= 95 THEN 'on_track'
                WHEN probability >= 80 THEN 'at_risk'
                ELSE 'off_track'
            END AS track_status
        FROM plan_goals
        WHERE plan_id = %s
        ORDER BY goal_year ASC
        """,
        params=(plan_id,)
    )


def get_goal_summary(plan_id):
    goals = get_goals(plan_id)
    summary = {"on_track": 0, "at_risk": 0, "off_track": 0, "total": 0}
    for g in goals:
        summary[g["track_status"]] += 1
        summary["total"] += 1
    return summary


# ── Cashflow ───────────────────────────────────────────────────

def get_cashflow(plan_id):
    return query(
        """
        SELECT calendar_year,
               projected_investments, projected_expenses,
               projected_portfolio_value,
               actual_investments, actual_portfolio_value
        FROM plan_cashflow
        WHERE plan_id = %s
        ORDER BY calendar_year ASC
        """,
        params=(plan_id,)
    )


def get_cashflow_for_year(plan_id, calendar_year):
    return query(
        """
        SELECT * FROM plan_cashflow
        WHERE plan_id = %s AND calendar_year = %s
        """,
        params=(plan_id, calendar_year),
        fetchone=True
    )


# ── Current financial assets ───────────────────────────────────

def get_current_assets(plan_id):
    return query(
        """
        SELECT id, asset_name, asset_class, current_value, is_manual_override
        FROM plan_current_assets
        WHERE plan_id = %s
        ORDER BY current_value DESC
        """,
        params=(plan_id,)
    )


def get_current_assets_total(plan_id):
    result = query(
        """
        SELECT COALESCE(SUM(current_value), 0) AS total
        FROM plan_current_assets
        WHERE plan_id = %s
        """,
        params=(plan_id,),
        fetchone=True
    )
    return result["total"] if result else 0


# ── Retirement expenses ────────────────────────────────────────

def get_retirement_expenses(plan_id):
    return query(
        """
        SELECT expense_name, annual_value, inflation_rate
        FROM plan_retirement_expenses
        WHERE plan_id = %s
        ORDER BY annual_value DESC
        """,
        params=(plan_id,)
    )


# ── Family members ─────────────────────────────────────────────

def get_family_members(plan_id):
    return query(
        """
        SELECT member_name, age, occupation, relation, risk_profile
        FROM plan_family_members
        WHERE plan_id = %s
        ORDER BY
            CASE relation
                WHEN 'Self'   THEN 1
                WHEN 'Spouse' THEN 2
                ELSE 3
            END
        """,
        params=(plan_id,)
    )


# ── Vasupradha Investments ─────────────────────────────────────
# Reads customer_transactions WHERE pan_source LIKE '%_adv'
# Confirmed columns: customer_id, pan_source, transaction_date,
#                    total_amount, applicant_name, uploaded_at

def get_vasupradha_investments(customer_id):
    """
    All advisory-channel transactions for this customer.
    Filters by pan_source ending in '_adv'.
    Confirmed against actual customer_transactions schema.
    """
    return query(
        """
        SELECT pan_source, transaction_date,
               total_amount, applicant_name
        FROM customer_transactions
        WHERE customer_id = %s
          AND pan_source LIKE '%_adv'
        ORDER BY transaction_date DESC
        """,
        params=(customer_id,)
    )


def get_vasupradha_investments_summary(customer_id):
    """
    Total invested and net invested (after redemptions).
    """
    result = query(
        """
        SELECT
            COALESCE(SUM(CASE WHEN total_amount > 0
                         THEN total_amount ELSE 0 END), 0) AS total_invested,
            COALESCE(SUM(CASE WHEN total_amount < 0
                         THEN ABS(total_amount) ELSE 0 END), 0) AS total_redeemed,
            COALESCE(SUM(total_amount), 0) AS net_invested,
            COUNT(*) AS transaction_count
        FROM customer_transactions
        WHERE customer_id = %s AND pan_source LIKE '%_adv'
        """,
        params=(customer_id,),
        fetchone=True
    )
    return result or {
        "total_invested": 0, "total_redeemed": 0,
        "net_invested": 0, "transaction_count": 0
    }


# ── Portfolio value snapshots ──────────────────────────────────
# Confirmed columns: customer_id, snapshot_date, pan_source,
#                    current_value, uploaded_at
# No 'pan' column — group by customer_id only

def get_latest_portfolio_value(customer_id):
    """
    Sum of current_value across all pan_source accounts
    for the most recent snapshot_date.
    """
    return query(
        """
        SELECT
            snapshot_date,
            SUM(current_value)  AS total_value,
            COUNT(*)            AS account_count
        FROM customer_portfolio_snapshots
        WHERE customer_id = %s
          AND snapshot_date = (
              SELECT MAX(snapshot_date)
              FROM customer_portfolio_snapshots
              WHERE customer_id = %s
          )
        GROUP BY snapshot_date
        """,
        params=(customer_id, customer_id),
        fetchone=True
    )


def get_portfolio_history(customer_id):
    """
    Portfolio value over time — one row per snapshot date.
    Used for the portfolio trend chart.
    """
    return query(
        """
        SELECT
            snapshot_date,
            SUM(current_value) AS total_value
        FROM customer_portfolio_snapshots
        WHERE customer_id = %s
        GROUP BY snapshot_date
        ORDER BY snapshot_date ASC
        """,
        params=(customer_id,)
    )


def get_portfolio_by_account(customer_id):
    """
    Breakdown of latest portfolio by pan_source account.
    """
    return query(
        """
        SELECT pan_source, current_value, snapshot_date
        FROM customer_portfolio_snapshots
        WHERE customer_id = %s
          AND snapshot_date = (
              SELECT MAX(snapshot_date)
              FROM customer_portfolio_snapshots
              WHERE customer_id = %s
          )
        ORDER BY current_value DESC
        """,
        params=(customer_id, customer_id)
    )


# ── Asset allocation snapshots ─────────────────────────────────
# Confirmed columns: customer_id, snapshot_date, asset_name,
#                    target_pct, current_pct, current_value,
#                    target_value, raw_diff, final_trade

def get_latest_asset_allocation(customer_id):
    return query(
        """
        SELECT
            asset_name, target_pct, current_pct,
            current_value, target_value, raw_diff, final_trade,
            snapshot_date
        FROM customer_asset_allocation_snapshots
        WHERE customer_id = %s
          AND snapshot_date = (
              SELECT MAX(snapshot_date)
              FROM customer_asset_allocation_snapshots
              WHERE customer_id = %s
          )
        ORDER BY current_value DESC
        """,
        params=(customer_id, customer_id)
    )

def get_asset_allocation_total(customer_id):
    result = query(
        """
        SELECT
            COALESCE(SUM(current_value), 0) AS total_value,
            snapshot_date
        FROM customer_asset_allocation_snapshots
        WHERE customer_id = %s
          AND snapshot_date = (
              SELECT MAX(snapshot_date)
              FROM customer_asset_allocation_snapshots
              WHERE customer_id = %s
          )
        GROUP BY snapshot_date
        """,
        params=(customer_id, customer_id),
        fetchone=True
    )
    return result or {"total_value": 0, "snapshot_date": None}


# ── Held Away Assets ───────────────────────────────────────────
# Confirmed columns: customer_id, asset_name, asset_type,
#                    current_value, maturity_value,
#                    annual_contribution, maturity_date, notes

def get_other_assets(customer_id):
    return query(
        """
        SELECT id, asset_name, asset_type, current_value,
               maturity_value, annual_contribution,
               maturity_date, notes, is_manual_override
        FROM other_assets
        WHERE customer_id = %s
        ORDER BY maturity_date ASC
        """,
        params=(customer_id,)
    )

def get_current_assets_with_id(plan_id):
    """
    Same as get_current_assets but includes the row id.
    Used for admin edit/delete controls.
    """
    return query(
        """
        SELECT id, asset_name, asset_class, current_value
        FROM plan_current_assets
        WHERE plan_id = %s
        ORDER BY current_value DESC
        """,
        params=(plan_id,)
    )

def get_other_assets_with_id(customer_id):
    """
    Same as get_other_assets but includes row id for admin edit/delete.
    other_assets has direct customer_id — no plan dependency.
    """
    return query(
        """
        SELECT id, asset_name, asset_type, current_value, 
               maturity_value, annual_contribution, maturity_date, notes
        FROM other_assets
        WHERE customer_id = %s
        ORDER BY maturity_date ASC
        """,
        params=(customer_id,)
    )

def get_dashboard_data(customer_id):
    plan = get_current_plan(customer_id)
    if not plan:
        return None
        
    plan_id = plan["id"]
    cycle   = get_current_cycle(plan)
    family  = get_family_members(plan_id)
    
    return {
        "plan": plan,
        "cycle": cycle,
        "goals": get_goals(plan_id),
        "goal_summary": get_goal_summary(plan_id),
        "family": family,
        "self_member": next((m for m in family if m["relation"] == "Self"), None),
        "current_year_cf": get_cashflow_for_year(plan_id, cycle["current_year"] if cycle else plan.get("plan_year", 2026)),
        "current_assets": get_current_assets(plan_id),
        "current_assets_with_id": get_current_assets_with_id(plan_id),
        "current_assets_total": get_current_assets_total(plan_id),
        "retirement_expenses": get_retirement_expenses(plan_id),
        "vasupradha_summary": get_vasupradha_investments_summary(customer_id),
        "vasupradha_txns": get_vasupradha_investments(customer_id),
        "portfolio_latest": get_latest_portfolio_value(customer_id),
        "portfolio_accounts": get_portfolio_by_account(customer_id),
        "portfolio_history": get_portfolio_history(customer_id),
        "allocation_rows": get_latest_asset_allocation(customer_id),
        "allocation_total": get_asset_allocation_total(customer_id),
        "other_assets": get_other_assets(customer_id),
        "other_assets_with_id": get_other_assets_with_id(customer_id),
        "archived_plans": get_archived_plans(customer_id)
    }