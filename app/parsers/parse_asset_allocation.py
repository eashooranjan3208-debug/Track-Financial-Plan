import pandas as pd
from datetime import date
from pathlib import Path

def parse_asset_allocation(file_path, snapshot_date=None, pan_override=None):
    """
    Parse asset allocation Excel file using vectorized operations.
    """
    if snapshot_date is None:
        snapshot_date = date.today()

    try:
        df = pd.read_excel(file_path, engine='openpyxl')
    except Exception as e:
        raise ValueError(f"Failed to read Excel file {Path(file_path).name}: {str(e)}")

    cols = set(df.columns)

    # Dispatcher
    if "PAN" in cols:
        return _parse_system_wide(df, file_path, snapshot_date)
    else:
        return _parse_per_customer(df, file_path, snapshot_date, pan_override)


def _parse_system_wide(df, file_path, snapshot_date):
    """FORMAT A: PAN column present. System-wide file."""
    initial_row_count = len(df)
    
    # Identify the asset column
    asset_col = "Asset_Class1" if "Asset_Class1" in df.columns else "Scheme Type" if "Scheme Type" in df.columns else None
    if not asset_col:
        raise ValueError(
            f"{Path(file_path).name}: has PAN but missing asset class column. "
            f"Expected 'Asset_Class1' or 'Scheme Type'. Found: {list(df.columns)}"
        )

    if "CURRENT VALUE" not in df.columns:
        raise ValueError(f"{Path(file_path).name}: missing 'CURRENT VALUE' column")

    # 1. Clean and filter mandatory strings (PAN and Asset Class)
    df = df.dropna(subset=["PAN", asset_col])
    df["PAN"] = df["PAN"].astype(str).str.strip()
    df[asset_col] = df[asset_col].astype(str).str.strip()
    
    # Drop rows that became empty strings after stripping
    df = df[(df["PAN"] != "") & (df["PAN"].str.lower() != "nan") & 
            (df[asset_col] != "") & (df[asset_col].str.lower() != "nan")].copy()

    # 2. Safely extract optional columns
    customer_cat = df["Customer_Cat"].astype(str).str.strip() if "Customer_Cat" in df.columns else None
    source_row_id = pd.to_numeric(df["Unnamed: 0"], errors='coerce').fillna(0).astype(int) if "Unnamed: 0" in df.columns else None

    # 3. Clean numeric values
    cleaned_values = df["CURRENT VALUE"].astype(str).str.replace(',', '', regex=False)
    
    # 4. Construct final DataFrame
    final_df = pd.DataFrame({
        "pan": df["PAN"],
        "customer_cat": customer_cat,
        "asset_class": df[asset_col],
        "current_value": pd.to_numeric(cleaned_values, errors='coerce').fillna(0.0),
        "source_row_id": source_row_id,
        "snapshot_date": snapshot_date
    })

    # Convert to list of dictionaries
    rows = final_df.replace({pd.NA: None, float('nan'): None}).to_dict(orient="records")
    
    skipped = initial_row_count - len(rows)
    print(f"[parse_asset_allocation] {Path(file_path).name} (system-wide): {len(rows)} rows, {skipped} skipped")
    
    return {"format": "system_wide", "rows": rows, "summary": None}


def _parse_per_customer(df, file_path, snapshot_date, pan_override):
    """FORMAT B: No PAN column. Per-customer advisor report."""
    expected = {"Asset", "Target%", "Current%", "Current Value", "Target Value", "Raw Diff", "Final Trade"}
    missing = expected - set(df.columns)
    if missing:
        raise ValueError(f"{Path(file_path).name}: unrecognised format. Missing {missing}.")

    # 1. Clean Asset strings to separate TOTAL row from data rows
    df["Asset_Clean"] = df["Asset"].astype(str).str.upper().str.strip()
    is_total_mask = df["Asset_Clean"] == "TOTAL"
    
    # Extract TOTAL row
    total_df = df[is_total_mask]
    
    # Extract Data rows (exclude TOTAL, drop empty assets)
    data_df = df[~is_total_mask].dropna(subset=["Asset"]).copy()
    data_df = data_df[(data_df["Asset_Clean"] != "") & (data_df["Asset_Clean"] != "NAN")]

    # 2. Bulk Vectorized Numeric Cleaning
    numeric_cols = ["Target%", "Current%", "Current Value", "Target Value", "Raw Diff", "Final Trade"]
    for col in numeric_cols:
        cleaned_str = data_df[col].astype(str).str.replace(',', '', regex=False)
        data_df[col] = pd.to_numeric(cleaned_str, errors='coerce').fillna(0.0)

    # 3. Construct final rows DataFrame
    final_df = pd.DataFrame({
        "pan": pan_override,
        "customer_cat": None,
        "asset_class": data_df["Asset"].astype(str).str.strip(),
        "current_value": data_df["Current Value"],
        "target_pct": data_df["Target%"],
        "current_pct": data_df["Current%"],
        "target_value": data_df["Target Value"],
        "raw_diff": data_df["Raw Diff"],
        "final_trade": data_df["Final Trade"],
        "source_row_id": None,
        "snapshot_date": snapshot_date
    })

    rows = final_df.replace({pd.NA: None, float('nan'): None}).to_dict(orient="records")

    # 4. Construct Summary Dictionary safely
    summary = None
    if not total_df.empty:
        # Helper to safely clean a single scalar value for the summary
        def _clean_scalar(val):
            return pd.to_numeric(str(val).replace(',', ''), errors='coerce') or 0.0
            
        t = total_df.iloc[0]
        summary = {
            "total_current_value": _clean_scalar(t.get("Current Value", 0)),
            "total_target_value":  _clean_scalar(t.get("Target Value", 0)),
            "total_raw_diff":      _clean_scalar(t.get("Raw Diff", 0)),
        }

    print(f"[parse_asset_allocation] {Path(file_path).name} (per-customer): {len(rows)} asset rows.")
    return {"format": "per_customer", "rows": rows, "summary": summary}