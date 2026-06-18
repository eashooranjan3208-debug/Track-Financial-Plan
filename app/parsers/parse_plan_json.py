"""
parse_plan_json.py
──────────────────
Parser for: YYYYMMDD_HHMMSS_PAN_plan.json  (per-customer)

JSON structure confirmed from real client sample:
  Top-level key = PAN (e.g. "ANPPfxxF0")
  Value = dict with all plan fields

Key fields parsed:
  today_date / last_updated → plan_start_date
  dfcustdetails             → plan_family_members
  dfplanned_goals           → plan_goals (definition)
  dfgoal_status             → plan_goals (status + probability)
  dfcashflow5               → plan_cashflow (41 year projections)
  dfcurrent_assets          → plan_current_assets
  dfret_expense             → plan_retirement_expenses
  dbeldaway                 → other_assets

Note: JSON strings use ligature chars in some field names
(e.g. "inflaQon" for "inflation", "relaQon" for "relation",
"criQcality" for "criticality"). Both variants handled.

Input  : file path or already-loaded dict
Output : dict with 7 keys (plan, family, goals, cashflow,
         current_assets, retirement_expenses, other_assets)
"""
import json
from datetime import datetime
from pathlib import Path


def parse_plan_json(file_path_or_dict):
    """
    Parse a Vasupradah financial plan JSON file.

    Returns dict:
      plan                : dict → financial_plans row
      family              : list → plan_family_members rows
      goals               : list → plan_goals rows
      cashflow            : list → plan_cashflow rows
      current_assets      : list → plan_current_assets rows
      retirement_expenses : list → plan_retirement_expenses rows
      other_assets        : list → other_assets rows
    """
    if isinstance(file_path_or_dict, dict):
        raw = file_path_or_dict
    else:
        with open(file_path_or_dict, "r", encoding="utf-8") as f:
            raw = json.load(f)

    # Unwrap top-level PAN key if present
    # Format: { "ANPPfxxF0": { "pan_no": ..., "today_date": ..., ... } }
    if len(raw) == 1:
        only_key = list(raw.keys())[0]
        if "today_date" not in raw and "pan_no" not in raw:
            raw = raw[only_key]

    return {
        "plan":                  _plan_fields(raw),
        "family":                _family(raw),
        "goals":                 _goals(raw),
        "cashflow":              _cashflow(raw),
        "current_assets":        _current_assets(raw),
        "retirement_expenses":   _retirement_expenses(raw),
        "other_assets":          _other_assets(raw),
    }


# ── Section parsers ────────────────────────────────────────────

def _plan_fields(d):
    date_raw = d.get("today_date") or d.get("last_updated")
    if not date_raw:
        raise ValueError("JSON missing 'today_date' — cannot determine plan_start_date")

    start = _parse_date(date_raw)
    return {
        "plan_start_date":       start,
        "plan_year":             start.year,
        "end_of_plan_year":      _int(d.get("end_of_plan_year")),
        "ret_age":               _int(d.get("ret_age")),
        "end_age":               _int(d.get("end_age")),
        "ret_start_year":        _int(d.get("ret_start_year")),
        "ret_expected_expenses": _dec(d.get("ret_expected_expenses")),
        "target_surplus":        _dec(d.get("target_surplus")),
        "growth_target_surplus": _dec(d.get("growth_target_surplus")),
        "risk_category":         d.get("risk_cat"),
        "risk_description":      d.get("risk_para"),
        "weight_equity":         _dec(d.get("w_eq")),
        "weight_debt":           _dec(d.get("w_de")),
        "weight_liquid":         _dec(d.get("w_li")),
        "weight_gold":           _dec(d.get("w_go")),
        "expected_return":       _dec(d.get("expret")),
        "std_deviation":         _dec(d.get("std_dev")),
        "advisor_comment":       d.get("comment_finplan"),
        "allocation_comment":    d.get("commnew_allocation") or d.get("commnew_allocaQon"),
        "ingestion_source":      "upload",
    }


def _family(d):
    df = _df(d.get("dfcustdetails"))
    if not df:
        return []

    names  = df.get("name", {})
    ages   = df.get("age", {})
    occs   = df.get("occupation", {}) or df.get("occupaQon", {})
    rels   = df.get("relation", {}) or df.get("relaQon", {})
    risk   = df.get("risk_profile", {})

    return [
        {
            "member_name":  names.get(k),
            "age":          _int(ages.get(k)),
            "occupation":   occs.get(k),
            "relation":     rels.get(k),
            "risk_profile": risk.get(k),
        }
        for k in names
    ]


def _goals(d):
    defined = {}

    # Parse goal definitions from dfplanned_goals
    df = _df(d.get("dfplanned_goals"))
    if df:
        names    = df.get("name", {})
        price    = df.get("curr_price", {})
        infl     = df.get("inflaQon", {}) or df.get("inflation", {})
        crit     = df.get("criQcality", {}) or df.get("criticality", {})
        gtype    = df.get("goal_type", {})
        upfront  = df.get("upfront_amt", {})
        emi      = df.get("emi_amt", {})
        loan     = df.get("loan_term", {})
        yr       = df.get("goal_year", {})
        period   = df.get("Qme_period", {}) or df.get("time_period", {})

        for k in names:
            name = names.get(k)
            if not name:
                continue
            defined[name] = {
                "goal_name":       name,
                "goal_type":       gtype.get(k),
                "criticality":     crit.get(k),
                "current_price":   _dec(price.get(k)),
                "inflation_rate":  _dec(infl.get(k)),
                "goal_year":       _int(yr.get(k)),
                "time_period_yrs": _int(period.get(k)),
                "upfront_amount":  _dec(upfront.get(k)),
                "emi_amount":      _dec(emi.get(k)),
                "loan_term_yrs":   _int(loan.get(k)),
                "future_price":    None,
                "status_text":     None,
                "probability":     None,
            }

    # Merge status + probability from dfgoal_status
    ds = _df(d.get("dfgoal_status"))
    if ds:
        s_names  = ds.get("goal", {})
        fp       = ds.get("future_price", {})
        status   = ds.get("status", {})
        prob     = ds.get("probability", {})
        yr_s     = ds.get("goal_year", {})
        cp_s     = ds.get("curr_price", {})
        crit_s   = ds.get("criQcality", {}) or ds.get("criticality", {})
        gtype_s  = ds.get("goal_type", {})

        for k in s_names:
            name = s_names.get(k)
            if not name:
                continue
            if name in defined:
                defined[name]["future_price"] = _dec(fp.get(k))
                defined[name]["status_text"]  = status.get(k)
                defined[name]["probability"]  = _dec(prob.get(k))
            else:
                # Goal only in status (e.g. Retirement)
                defined[name] = {
                    "goal_name":       name,
                    "goal_type":       gtype_s.get(k),
                    "criticality":     crit_s.get(k),
                    "current_price":   _dec(cp_s.get(k)),
                    "inflation_rate":  None,
                    "goal_year":       _int(yr_s.get(k)),
                    "time_period_yrs": None,
                    "upfront_amount":  0,
                    "emi_amount":      0,
                    "loan_term_yrs":   0,
                    "future_price":    _dec(fp.get(k)),
                    "status_text":     status.get(k),
                    "probability":     _dec(prob.get(k)),
                }

    # Deduplicate by (goal_name, goal_year) and sort
    seen, out = set(), []
    for g in defined.values():
        key = (g["goal_name"], g["goal_year"])
        if key not in seen:
            seen.add(key)
            out.append(g)
    return sorted(out, key=lambda g: g.get("goal_year") or 9999)


def _cashflow(d):
    df = _df(d.get("dfcashflow5"))
    if not df:
        return []

    years = df.get("Year", {})
    # Handle both normal and ligature-encoded keys
    inv   = (df.get("Yearly_Investments/Inflows")
             or df.get("Yearly_Investments\\/Inflows")
             or {})
    exp   = (df.get("Expenses_Goals/Outflows")
             or df.get("Expenses_Goals\\/Oullows")
             or df.get("Expenses_Goals\\/Outflows")
             or {})
    port  = (df.get("Expected_Portfolio_Value")
             or df.get("Expected_Porlolio_Value")
             or {})

    rows = []
    for k in years:
        yr = _int(years.get(k))
        if not yr:
            continue
        rows.append({
            "calendar_year":             yr,
            "projected_investments":     _dec(inv.get(k, 0)),
            "projected_expenses":        _dec(exp.get(k, 0)),
            "projected_portfolio_value": _dec(port.get(k, 0)),
        })
    return sorted(rows, key=lambda r: r["calendar_year"])


def _current_assets(d):
    df = _df(d.get("dfcurrent_assets"))
    if not df:
        return []
    names = df.get("name", {})
    vals  = df.get("current_value", {})
    cls   = df.get("asset_class", {})
    return [
        {
            "asset_name":    names.get(k),
            "asset_class":   cls.get(k),
            "current_value": _dec(vals.get(k, 0)),
        }
        for k in names
    ]


def _retirement_expenses(d):
    df = _df(d.get("dfret_expense"))
    if not df:
        return []
    names = df.get("name", {})
    vals  = df.get("annual_value", {})
    infl  = df.get("inflaQon_rate", {}) or df.get("inflation_rate", {})
    return [
        {
            "expense_name":   names.get(k),
            "annual_value":   _dec(vals.get(k, 0)),
            "inflation_rate": _dec(infl.get(k, 5)),
        }
        for k in names
    ]


def _other_assets(d):
    df = _df(d.get("dfheldaway"))
    if not df:
        return []
    names   = df.get("name", {})
    cls     = df.get("asset_class", {})
    mat     = df.get("date_maturity", {})
    mat_amt = df.get("maturity_amt", {})
    return [
        {
            "asset_name":          names.get(k),
            "asset_type":          cls.get(k),
            "current_value":       0,
            "maturity_value":      _dec(mat_amt.get(k, 0)),
            "annual_contribution": 0,
            "maturity_year":       _int(mat.get(k)),
            "notes":               None,
        }
        for k in names
    ]


# ── Helpers ────────────────────────────────────────────────────

def _df(raw):
    """Parse a JSON-encoded DataFrame string into a plain dict."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _parse_date(raw):
    fmts = [
        "%d/%m/%Y %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%d/%m/%Y",
    ]
    for fmt in fmts:
        try:
            return datetime.strptime(str(raw).strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: {raw!r}")


def _int(v):
    if v is None: return None
    try: return int(float(str(v).strip()))
    except: return None

def _dec(v):
    if v is None: return None
    # 1. Convert to string, remove commas, strip spaces, make lowercase
    s = str(v).replace(",", "").strip().lower()
    # 2. Catch Pandas empty cells and blanks
    if s in ('nan', 'none', ''): 
        return None
    # 3. Convert to float
    try: return float(s)
    except: return None
