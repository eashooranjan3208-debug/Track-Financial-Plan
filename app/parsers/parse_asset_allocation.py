"""
parse_asset_allocation.py
─────────────────────────
Parser for: YYYYMMDD_HHMMSS_asset_allocation.xlsx  (system-wide)

TWO formats are handled automatically:

FORMAT A — System-wide (production, confirmed from screenshots):
  Columns: PAN, Customer_Cat, Asset_Class1, CURRENT VALUE
       or: PAN, Customer_Cat, Scheme Type,  CURRENT VALUE
  PAN column present. No TOTAL row. Multiple PANs per file.

FORMAT B — Per-customer advisor report (old format / sample file):
  Columns: Asset, Target%, Current%, Current Value, Target Value, Raw Diff, Final Trade
  No PAN column. Has TOTAL row. Single customer per file.
  For MVP: cannot map to customer without PAN — rows are returned
  with pan=None so caller can decide (skip or attach to known customer).

snapshot_date comes from filename YYYYMMDD prefix.
"""
import pandas as pd
from datetime import date
from pathlib import Path


def parse_asset_allocation(file_path, snapshot_date=None, pan_override=None):
    """
    Parse asset allocation Excel file.

    Args:
        file_path     : path to .xlsx file
        snapshot_date : date from filename YYYYMMDD. Defaults to today.
        pan_override  : PAN string to use when file has no PAN column
                        (FORMAT B / per-customer file). Required for
                        that format to produce usable output.

    Returns:
        dict with keys:
          format   : 'system_wide' or 'per_customer'
          rows     : list of dicts for customer_asset_allocation_snapshots
          summary  : dict of totals (only for per_customer format)
    """
    if snapshot_date is None:
        snapshot_date = date.today()

    df = pd.read_excel(file_path)
    cols = set(df.columns)

    # Detect format
    if "PAN" in cols:
        return _parse_system_wide(df, file_path, snapshot_date)
    else:
        return _parse_per_customer(df, file_path, snapshot_date, pan_override)


def _parse_system_wide(df, file_path, snapshot_date):
    """
    FORMAT A: PAN column present. System-wide file.
    Asset class column is 'Asset_Class1' or 'Scheme Type'.
    """
    if "Asset_Class1" in df.columns:
        asset_col = "Asset_Class1"
    elif "Scheme Type" in df.columns:
        asset_col = "Scheme Type"
    else:
        raise ValueError(
            f"{Path(file_path).name}: has PAN but missing asset class column. "
            f"Expected 'Asset_Class1' or 'Scheme Type'. Found: {list(df.columns)}"
        )

    if "CURRENT VALUE" not in df.columns:
        raise ValueError(f"{Path(file_path).name}: missing 'CURRENT VALUE' column")

    rows, skipped = [], 0
    for _, row in df.iterrows():
        pan = _str(row.get("PAN"))
        if not pan:
            skipped += 1; continue

        asset_class = _str(row.get(asset_col))
        if not asset_class:
            skipped += 1; continue

        rows.append({
            "pan":           pan,
            "customer_cat":  _str(row.get("Customer_Cat")),
            "asset_class":   asset_class,
            "current_value": _decimal(row.get("CURRENT VALUE", 0)),
            "source_row_id": _int(row.get("Unnamed: 0")),
            "snapshot_date": snapshot_date,
        })

    print(
        f"[parse_asset_allocation] {Path(file_path).name} (system-wide, col='{asset_col}'): "
        f"{len(rows)} rows, {skipped} skipped"
    )
    return {"format": "system_wide", "rows": rows, "summary": None}


def _parse_per_customer(df, file_path, snapshot_date, pan_override):
    """
    FORMAT B: No PAN column. Per-customer advisor report.
    Columns: Asset, Target%, Current%, Current Value, Target Value, Raw Diff, Final Trade
    Excludes TOTAL row. Returns pan=pan_override (may be None).
    """
    expected = {"Asset", "Target%", "Current%", "Current Value"}
    missing  = expected - set(df.columns)
    if missing:
        raise ValueError(
            f"{Path(file_path).name}: unrecognised format. "
            f"Missing {missing}. Found: {list(df.columns)}"
        )

    # Exclude TOTAL row
    data = df[~df["Asset"].astype(str).str.upper().str.strip().eq("TOTAL")].copy()

    rows    = []
    summary = None

    for _, row in data.iterrows():
        asset_name = _str(row.get("Asset"))
        if not asset_name:
            continue
        rows.append({
            "pan":            pan_override,      # None if not provided
            "customer_cat":   None,
            "asset_class":    asset_name,
            "current_value":  _decimal(row.get("Current Value", 0)),
            "target_pct":     _decimal(row.get("Target%", 0)),
            "current_pct":    _decimal(row.get("Current%", 0)),
            "target_value":   _decimal(row.get("Target Value", 0)),
            "raw_diff":       _decimal(row.get("Raw Diff", 0)),
            "final_trade":    _decimal(row.get("Final Trade", 0)),
            "source_row_id":  None,
            "snapshot_date":  snapshot_date,
        })

    # Capture TOTAL row as summary
    total_rows = df[df["Asset"].astype(str).str.upper().str.strip().eq("TOTAL")]
    if not total_rows.empty:
        t = total_rows.iloc[0]
        summary = {
            "total_current_value": _decimal(t.get("Current Value", 0)),
            "total_target_value":  _decimal(t.get("Target Value", 0)),
            "total_raw_diff":      _decimal(t.get("Raw Diff", 0)),
        }

    print(
        f"[parse_asset_allocation] {Path(file_path).name} (per-customer): "
        f"{len(rows)} asset rows, pan={'provided' if pan_override else 'NOT PROVIDED'}"
    )
    return {"format": "per_customer", "rows": rows, "summary": summary}


def _str(v):
    if v is None: return None
    s = str(v).strip()
    return s if s and s.lower() not in ("nan","none","") else None

def _int(v):
    if v is None: return None
    try: return int(float(str(v)))
    except: return None

def _decimal(v):
    if v is None: return 0.0
    # 1. Convert to string, remove commas, strip spaces, make lowercase
    s = str(v).replace(",", "").strip().lower()
    # 2. Catch Pandas empty cells and blanks
    if s in ('nan', 'none', ''): 
        return 0.0
    # 3. Convert to float
    try: return float(s)
    except: return 0.0