"""
parse_transactions.py
─────────────────────
Parser for: YYYYMMDD_HHMMSS_transactions.xlsx  (system-wide)

Confirmed columns from real file:
  Unnamed: 0       → source_row_id  (non-sequential source system IDs)
  PAN              → pan
  TRANSACTION DATE → transaction_date
  TOTAL AMOUNT     → total_amount   (signed int: negative = redemption)
  APPLICANT        → applicant_name
  PAN_SOURCE       → pan_source     (PAN_suffix, e.g. ABMPM2997D_adv)
"""
import pandas as pd
from datetime import date
from pathlib import Path


def parse_transactions(file_path, upload_date=None):
    if upload_date is None:
        upload_date = date.today()

    df = pd.read_excel(file_path)
    _require_cols(df, {"PAN","TRANSACTION DATE","TOTAL AMOUNT","PAN_SOURCE"}, file_path)

    rows, skipped = [], 0
    for _, row in df.iterrows():
        pan = _str(row.get("PAN"))
        if not pan:
            skipped += 1; continue

        tx_date = _to_date(row.get("TRANSACTION DATE"))
        if tx_date is None:
            skipped += 1; continue

        pan_source   = _str(row.get("PAN_SOURCE")) or ""
        account_type = pan_source.split("_")[-1] if "_" in pan_source else None

        rows.append({
            "source_row_id":    _int(row.get("Unnamed: 0")),
            "pan":              pan,
            "pan_source":       pan_source,
            "account_type":     account_type,
            "transaction_date": tx_date,
            "total_amount":     _decimal(row.get("TOTAL AMOUNT", 0)),
            "applicant_name":   _str(row.get("APPLICANT")),
            "upload_date":      upload_date,
            "entry_type":       "excel_upload",
        })

    print(f"[parse_transactions] {Path(file_path).name}: {len(rows)} rows, {skipped} skipped")
    return rows


def _require_cols(df, required, path):
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{Path(path).name}: missing columns {missing}")

def _str(v):
    if v is None: return None
    s = str(v).strip()
    return s if s and s.lower() not in ("nan","none","") else None

def _to_date(v):
    if v is None: return None
    if hasattr(v, "date"): return v.date()
    try: return pd.to_datetime(str(v)).date()
    except: return None

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