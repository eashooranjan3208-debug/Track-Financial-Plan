"""
parse_portfolio.py
──────────────────
Parser for: YYYYMMDD_HHMMSS_portfolio.xlsx  (system-wide, todays_val)

Confirmed columns from real file:
  Unnamed: 0    → (ignored, not needed for dedup)
  PAN_SOURCE    → pan_source  (e.g. ABMPM2997D_adv)
  CURRENT VALUE → current_value (float)

PAN extracted by splitting PAN_SOURCE on '_', taking all but last segment.
account_type = last segment ('adv' or 'mfd').

Real sample: 10 rows, all _adv. Production may include _mfd.
snapshot_date comes from filename YYYYMMDD prefix.
"""
import pandas as pd
from datetime import date
from pathlib import Path


def parse_portfolio(file_path, snapshot_date=None):
    if snapshot_date is None:
        snapshot_date = date.today()

    df = pd.read_excel(file_path)

    if "PAN_SOURCE" not in df.columns:
        raise ValueError(f"{Path(file_path).name}: missing 'PAN_SOURCE' column")
    if "CURRENT VALUE" not in df.columns:
        raise ValueError(f"{Path(file_path).name}: missing 'CURRENT VALUE' column")

    rows, skipped = [], 0
    for _, row in df.iterrows():
        pan_source = _str(row.get("PAN_SOURCE"))
        if not pan_source or "_" not in pan_source:
            skipped += 1; continue

        parts        = pan_source.split("_")
        account_type = parts[-1]
        pan          = "_".join(parts[:-1])

        if not pan:
            skipped += 1; continue

        rows.append({
            "pan":           pan,
            "pan_source":    pan_source,
            "account_type":  account_type,
            "snapshot_date": snapshot_date,
            "current_value": _decimal(row.get("CURRENT VALUE", 0)),
        })

    print(f"[parse_portfolio] {Path(file_path).name}: {len(rows)} rows, {skipped} skipped")
    return rows


def _str(v):
    if v is None: return None
    s = str(v).strip()
    return s if s and s.lower() not in ("nan","none","") else None

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