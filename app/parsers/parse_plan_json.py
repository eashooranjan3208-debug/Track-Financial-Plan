import json
import pandas as pd
from datetime import datetime
from pathlib import Path

def parse_plan_json(file_path_or_dict):
    """
    Parse a Vasupradah financial plan JSON file using Pandas for tabular data.
    """
    if isinstance(file_path_or_dict, dict):
        raw = file_path_or_dict
    else:
        with open(file_path_or_dict, "r", encoding="utf-8") as f:
            raw = json.load(f)

    # Unwrap top-level PAN key if present
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
    
    # Use safe scalar casting
    def safe_num(val, cast_type=float):
        if val is None or str(val).strip().lower() in ('', 'nan', 'none'): return None
        try: return cast_type(str(val).replace(',', ''))
        except: return None

    return {
        "plan_start_date":       start,
        "plan_year":             start.year,
        "end_of_plan_year":      safe_num(d.get("end_of_plan_year"), int),
        "ret_age":               safe_num(d.get("ret_age"), int),
        "end_age":               safe_num(d.get("end_age"), int),
        "ret_start_year":        safe_num(d.get("ret_start_year"), int),
        "ret_expected_expenses": safe_num(d.get("ret_expected_expenses")),
        "target_surplus":        safe_num(d.get("target_surplus")),
        "growth_target_surplus": safe_num(d.get("growth_target_surplus")),
        "risk_category":         d.get("risk_cat"),
        "risk_description":      d.get("risk_para"),
        "weight_equity":         safe_num(d.get("w_eq")),
        "weight_debt":           safe_num(d.get("w_de")),
        "weight_liquid":         safe_num(d.get("w_li")),
        "weight_gold":           safe_num(d.get("w_go")),
        "expected_return":       safe_num(d.get("expret")),
        "std_deviation":         safe_num(d.get("std_dev")),
        "advisor_comment":       d.get("comment_finplan"),
        "allocation_comment":    d.get("commnew_allocation") or d.get("commnew_allocaQon"),
        "ingestion_source":      "upload",
    }

def _family(d):
    raw = _df(d.get("dfcustdetails"))
    if not raw: return []
    
    df = pd.DataFrame(raw)
    if df.empty or "name" not in df.columns: return []
    
    df = df.rename(columns={
        "name": "member_name",
        "occupaQon": "occupation",
        "relaQon": "relation",
    })
    
    df["age"] = pd.to_numeric(df.get("age", pd.Series()), errors="coerce").fillna(0).astype(int)
    
    # Ensure all required columns exist
    for col in ["member_name", "occupation", "relation", "risk_profile"]:
        if col not in df.columns: df[col] = None

    return df[["member_name", "age", "occupation", "relation", "risk_profile"]].replace({pd.NA: None, float('nan'): None}).to_dict(orient="records")

def _goals(d):
    planned_raw = _df(d.get("dfplanned_goals"))
    status_raw = _df(d.get("dfgoal_status"))
    
    df_planned = pd.DataFrame(planned_raw) if planned_raw else pd.DataFrame()
    df_status = pd.DataFrame(status_raw) if status_raw else pd.DataFrame()

    if df_planned.empty and df_status.empty: return []

    # Clean and standardize planned goals
    if not df_planned.empty:
        df_planned = df_planned.rename(columns={
            "name": "goal_name",
            "inflaQon": "inflation_rate", "inflation": "inflation_rate",
            "criQcality": "criticality",
            "curr_price": "current_price",
            "upfront_amt": "upfront_amount",
            "emi_amt": "emi_amount",
            "loan_term": "loan_term_yrs",
            "Qme_period": "time_period_yrs", "time_period": "time_period_yrs"
        })
        
    # Clean and standardize status goals
    if not df_status.empty:
        df_status = df_status.rename(columns={
            "goal": "goal_name",
            "curr_price": "current_price",
            "criQcality": "criticality",
            "status": "status_text"
        })

    # Merge them like a SQL OUTER JOIN
    if not df_planned.empty and not df_status.empty:
        # Use goal_name as the primary key for the merge
        df = pd.merge(df_planned, df_status, on="goal_name", how="outer", suffixes=('', '_status'))
        # Coalesce overlapping columns
        for col in ["goal_type", "criticality", "current_price", "goal_year"]:
            if f"{col}_status" in df.columns:
                df[col] = df[col].combine_first(df[f"{col}_status"])
    else:
        df = df_planned if not df_planned.empty else df_status

    # Drop rows without a goal name
    df = df.dropna(subset=["goal_name"])

    # Define numeric conversions
    num_cols = ["current_price", "inflation_rate", "goal_year", "time_period_yrs", 
                "upfront_amount", "emi_amount", "loan_term_yrs", "future_price", "probability"]
    
    for col in num_cols:
        if col not in df.columns: df[col] = 0.0
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    # Cast year/term columns to int
    for col in ["goal_year", "time_period_yrs", "loan_term_yrs"]:
        df[col] = df[col].astype(int)
        
    # Ensure text columns exist
    # Ensure text columns exist and fit within strict database limits
    for col in ["goal_type", "criticality", "status_text"]:
        if col not in df.columns: 
            df[col] = None
        else:
            # Safely truncate to 10 characters to absolutely guarantee it fits
            df[col] = df[col].astype(str).str.slice(0, 10)

    # Deduplicate keeping the first occurrence, sort by year
    df = df.drop_duplicates(subset=["goal_name", "goal_year"]).sort_values(by="goal_year")
    
    expected_cols = ["goal_name", "goal_type", "criticality", "current_price", "inflation_rate", 
                     "goal_year", "time_period_yrs", "upfront_amount", "emi_amount", 
                     "loan_term_yrs", "future_price", "status_text", "probability"]
                     
    return df[expected_cols].replace({pd.NA: None, float('nan'): None}).to_dict(orient="records")

def _cashflow(d):
    raw = _df(d.get("dfcashflow5"))
    if not raw: return []
    
    df = pd.DataFrame(raw)
    if df.empty or "Year" not in df.columns: return []

    df = df.rename(columns=lambda x: str(x).replace('\\/', '/'))
    df = df.rename(columns={
        "Year": "calendar_year",
        "Yearly_Investments/Inflows": "projected_investments",
        "Expenses_Goals/Outflows": "projected_expenses",
        "Expenses_Goals/Oullows": "projected_expenses",
        "Expected_Portfolio_Value": "projected_portfolio_value",
        "Expected_Porlolio_Value": "projected_portfolio_value"
    })
    
    for col in ["projected_investments", "projected_expenses", "projected_portfolio_value"]:
        if col not in df.columns: df[col] = 0.0
        df[col] = pd.to_numeric(
            df[col].astype(str).str.replace(',', '', regex=False),
            errors="coerce"
        ).fillna(0.0)

    df["calendar_year"] = pd.to_numeric(df["calendar_year"], errors="coerce").fillna(0).astype(int)
    df = df[df["calendar_year"] > 0].sort_values("calendar_year")
    
    return df[["calendar_year", "projected_investments", "projected_expenses", "projected_portfolio_value"]].to_dict(orient="records")

def _current_assets(d):
    raw = _df(d.get("dfcurrent_assets"))
    if not raw: return []
    df = pd.DataFrame(raw)
    if df.empty: return []
    
    df = df.rename(columns={"name": "asset_name"})
    df["current_value"] = pd.to_numeric(
        df.get("current_value", pd.Series()).astype(str).str.replace(',', '', regex=False),
        errors="coerce"
    ).fillna(0.0)
    if "asset_class" not in df.columns: df["asset_class"] = None
    
    return df[["asset_name", "asset_class", "current_value"]].replace({pd.NA: None, float('nan'): None}).to_dict(orient="records")

def _retirement_expenses(d):
    raw = _df(d.get("dfret_expense"))
    if not raw: return []
    df = pd.DataFrame(raw)
    if df.empty: return []

    df = df.rename(columns={
        "name": "expense_name",
        "inflaQon_rate": "inflation_rate"
    })
    
    df["annual_value"] = pd.to_numeric(df.get("annual_value", pd.Series()), errors="coerce").fillna(0.0)
    df["inflation_rate"] = pd.to_numeric(df.get("inflation_rate", pd.Series()), errors="coerce").fillna(5.0)

    return df[["expense_name", "annual_value", "inflation_rate"]].replace({pd.NA: None, float('nan'): None}).to_dict(orient="records")

def _other_assets(d):
    raw = _df(d.get("dfheldaway"))
    if not raw: return []
    df = pd.DataFrame(raw)
    if df.empty: return []

    # 1. FIX: Mapped to 'maturity_date' to match the database exactly
    df = df.rename(columns={
        "name": "asset_name",
        "date_maturity": "maturity_date", 
        "maturity_amt": "maturity_value",
        "asset_class": "asset_type"
    })
    
    df["maturity_value"] = pd.to_numeric(
        df.get("maturity_value", pd.Series()).astype(str).str.replace(',', '', regex=False),
        errors="coerce"
    ).fillna(0.0)
    
    for col in ["asset_type", "notes"]:
        if col not in df.columns: df[col] = None
        
    df["current_value"] = 0.0
    df["annual_contribution"] = 0.0

    # ── COMPRESSOR ENGINE FOR RENTAL INCOME ──
    is_rental = df["asset_type"].str.lower() == "rental"
    final_rows = []
    
    if is_rental.any():
        rental_df = df[is_rental]
        
        # Calculate the summary math
        total_value = rental_df["maturity_value"].sum()
        
        # Safely extract years to build our label
        years = pd.to_numeric(rental_df["maturity_date"], errors="coerce").dropna().astype(int)
        start_year = years.min() if not years.empty else "Unknown"
        end_year = years.max() if not years.empty else "Unknown"
        
        # Create the single, elegant summary row
        final_rows.append({
            "asset_name": f"Projected Rental Income ({start_year} - {end_year})",
            "asset_type": "Real Estate",
            "current_value": 0.0,
            "maturity_value": float(total_value),
            "annual_contribution": 0.0,
            # 2. FIX: Format as YYYY-MM-DD so MySQL 'date' column accepts it unconditionally
            "maturity_date": f"{end_year}-12-31" if end_year != "Unknown" else None, 
            "notes": None
        })
        
    # Format the normal assets (FD, LIC, etc.) so their dates don't crash MySQL either
    normal_df = df[~is_rental].copy()
    if not normal_df.empty:
        # Convert raw years like '2026' into '2026-01-01'
        def format_date(y):
            try:
                return f"{int(y)}-01-01"
            except:
                return None
        normal_df["maturity_date"] = normal_df["maturity_date"].apply(format_date)
        
        normal_assets = normal_df.replace({pd.NA: None, float('nan'): None}).to_dict(orient="records")
        final_rows.extend(normal_assets)

    return final_rows

# ── Helpers ────────────────────────────────────────────────────

def _df(raw):
    """Parse a JSON-encoded DataFrame string or a native list into a dict/list."""
    # FIX: We added 'list' here so it accepts native JSON arrays!
    if isinstance(raw, (dict, list)): 
        return raw
    if not isinstance(raw, str): 
        return None
    try: 
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError): 
        return None

def _parse_date(raw):
    fmts = ["%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y"]
    for fmt in fmts:
        try: return datetime.strptime(str(raw).strip(), fmt).date()
        except ValueError: continue
    raise ValueError(f"Cannot parse date: {raw!r}")


