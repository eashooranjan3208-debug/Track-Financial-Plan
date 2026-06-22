import pandas as pd
from datetime import date
from pathlib import Path
import numpy as np

def parse_portfolio(file_path, snapshot_date=None):
    if snapshot_date is None:
        snapshot_date = date.today()

    # In production, we explicitly define the engine and handle potential file corruption
    try:
        df = pd.read_excel(file_path, engine='openpyxl')
    except Exception as e:
        raise ValueError(f"Failed to read Excel file {Path(file_path).name}: {str(e)}")

    # 1. Validate Columns
    required_columns = ["PAN_SOURCE", "CURRENT VALUE"]
    missing_cols = [col for col in required_columns if col not in df.columns]
    if missing_cols:
        raise ValueError(f"{Path(file_path).name}: missing columns {missing_cols}")

    initial_row_count = len(df)

    # 2. Clean and Filter PAN_SOURCE (Vectorized)
    # Drop rows where PAN_SOURCE is strictly empty/NaN
    df = df.dropna(subset=["PAN_SOURCE"])
    df["PAN_SOURCE"] = df["PAN_SOURCE"].astype(str).str.strip()
    
    # Keep only rows that actually contain an underscore
    df = df[df["PAN_SOURCE"].str.contains('_', na=False)].copy()

    # 3. Extract PAN and Account Type (Vectorized)
    # rsplit with n=1 splits from the right exactly once.
    # 'ABCDE1234F_XYZ_adv' becomes ['ABCDE1234F_XYZ', 'adv']
    split_data = df["PAN_SOURCE"].str.rsplit("_", n=1, expand=True)
    df["pan"] = split_data[0]
    df["account_type"] = split_data[1]

    # Filter out rows where the PAN part ended up empty
    df = df[df["pan"] != ""]

    # 4. Clean CURRENT VALUE (Vectorized)
    # Convert to string, remove commas, then aggressively coerce to numeric (invalid becomes NaN)
    cleaned_values = df["CURRENT VALUE"].astype(str).str.replace(',', '', regex=False)
    df["current_value"] = pd.to_numeric(cleaned_values, errors='coerce').fillna(0.0)

    # 5. Add static columns
    df["snapshot_date"] = snapshot_date

    # 6. Select and rename final columns for the output dictionary
    final_df = df[["pan", "PAN_SOURCE", "account_type", "snapshot_date", "current_value"]]
    
    # Convert to list of dicts natively
    rows = final_df.rename(columns={"PAN_SOURCE": "pan_source"}).to_dict(orient="records")
    
    skipped = initial_row_count - len(rows)
    print(f"[parse_portfolio] {Path(file_path).name}: {len(rows)} rows processed, {skipped} skipped")
    
    return rows