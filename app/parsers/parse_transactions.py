import pandas as pd
from datetime import date
from pathlib import Path
import numpy as np

def parse_transactions(file_path, upload_date=None):
    """
    Parse transaction history Excel file using vectorized operations.
    """
    if upload_date is None:
        upload_date = date.today()

    try:
        df = pd.read_excel(file_path, engine='openpyxl')
    except Exception as e:
        raise ValueError(f"Failed to read Excel file {Path(file_path).name}: {str(e)}")

    # 1. Validate Columns
    required = {"PAN", "TRANSACTION DATE", "TOTAL AMOUNT", "PAN_SOURCE"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{Path(file_path).name}: missing columns {missing}")

    initial_row_count = len(df)

    # 2. Clean PAN and Filter
    df["PAN"] = df["PAN"].astype(str).str.strip()
    # Keep rows where PAN is not empty and not 'nan'
    df = df[(df["PAN"] != "") & (df["PAN"].str.lower() != "nan")].copy()

    # 3. Clean Dates and Filter
    # Coerce invalid dates to NaT (Not a Time)
    df["TRANSACTION DATE"] = pd.to_datetime(df["TRANSACTION DATE"], errors="coerce")
    df = df.dropna(subset=["TRANSACTION DATE"])
    # Convert Pandas Timestamp to standard Python date objects for MySQL
    df["TRANSACTION DATE"] = df["TRANSACTION DATE"].dt.date

    # 4. Clean Numeric Amounts
    cleaned_amount = df["TOTAL AMOUNT"].astype(str).str.replace(',', '', regex=False)
    df["TOTAL AMOUNT"] = pd.to_numeric(cleaned_amount, errors="coerce").fillna(0.0)

    # 5. Extract Optional Columns Safely
    source_row_id = pd.to_numeric(df["Unnamed: 0"], errors="coerce").fillna(0).astype(int) if "Unnamed: 0" in df.columns else None
    
    applicant_name = df["APPLICANT"].astype(str).str.strip() if "APPLICANT" in df.columns else None
    if applicant_name is not None:
        applicant_name = applicant_name.replace({"nan": None, "": None})

    # 6. Extract Account Type from PAN_SOURCE
    df["PAN_SOURCE"] = df["PAN_SOURCE"].astype(str).str.strip().replace({"nan": "", "None": ""})
    
    # Vectorized extraction: Find rows with '_', split them from the right, take the last piece
    has_underscore = df["PAN_SOURCE"].str.contains('_', na=False)
    df["account_type"] = None
    df.loc[has_underscore, "account_type"] = df.loc[has_underscore, "PAN_SOURCE"].str.rsplit('_', n=1).str[-1]

    # 7. Construct Final DataFrame
    final_df = pd.DataFrame({
        "source_row_id": source_row_id,
        "pan": df["PAN"],
        "pan_source": df["PAN_SOURCE"],
        "account_type": df["account_type"],
        "transaction_date": df["TRANSACTION DATE"],
        "total_amount": df["TOTAL AMOUNT"],
        "applicant_name": applicant_name,
        "upload_date": upload_date,
        "entry_type": "excel_upload"
    })

    # Convert to list of dicts, ensuring SQL compatibility (NaN -> None)
    rows = final_df.replace({np.nan: None, pd.NA: None}).to_dict(orient="records")

    skipped = initial_row_count - len(rows)
    print(f"[parse_transactions] {Path(file_path).name}: {len(rows)} rows processed, {skipped} skipped")
    
    return rows