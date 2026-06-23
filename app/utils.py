import os
import shutil
import tempfile
import zipfile
import re
from datetime import datetime
from werkzeug.utils import secure_filename

# ── FILENAME CONVENTIONS ──
FILENAME_PATTERNS = {
    "plan_json":        re.compile(r"^\d{8}_\d{6}_([A-Za-z0-9]{8,64})_plan\.json$", re.IGNORECASE),
    "report_html":      re.compile(r"^\d{8}_\d{6}_([A-Za-z0-9]{8,64})_report\.html?$", re.IGNORECASE),
    "transactions":     re.compile(r"^\d{8}_\d{6}_transactions\.xlsx?$", re.IGNORECASE),
    "portfolio":        re.compile(r"^\d{8}_\d{6}_portfolio\.xlsx?$", re.IGNORECASE),
    "asset_allocation": re.compile(r"^\d{8}_\d{6}_asset_allocation\.xlsx?$", re.IGNORECASE),
}

# ── UTILITY FUNCTIONS ──

def make_upload_path(pan, filetype, extension):
    now       = datetime.now()
    datestamp = now.strftime("%Y%m%d")
    timestamp = now.strftime("%H%M%S")
    if pan:
        filename = f"{datestamp}_{timestamp}_{pan}_{filetype}.{extension}"
    else:
        filename = f"{datestamp}_{timestamp}_{filetype}.{extension}"
    folder = os.path.join("uploads", filetype)
    os.makedirs(folder, exist_ok=True)
    full_path = os.path.join(folder, filename)
    return filename, full_path

def extract_date_from_filename(filename):
    try:
        datepart = os.path.basename(filename).split("_")[0]
        return datetime.strptime(datepart, "%Y%m%d").date()
    except (ValueError, IndexError):
        return None

def mask_pan(pan):
    if not pan or len(pan) < 6:
        return "****"
    return f"{pan[:3]}****{pan[-3:]}"

def extract_zip(zip_file_storage):
    temp_dir = tempfile.mkdtemp(prefix="track_act_bulk_")
    zip_path = os.path.join(temp_dir, "_upload.zip")
    zip_file_storage.save(zip_path)
    extract_dir = os.path.join(temp_dir, "extracted")
    os.makedirs(extract_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.namelist():
            if member.startswith("__MACOSX") or "/." in member or member.endswith("/"):
                continue
            zf.extract(member, extract_dir)
    os.remove(zip_path)
    return extract_dir, temp_dir

def cleanup_temp_dir(temp_dir):
    shutil.rmtree(temp_dir, ignore_errors=True)

def classify_file(filename):
    base = os.path.basename(filename)
    for file_type, pattern in FILENAME_PATTERNS.items():
        match = pattern.match(base)
        if match:
            pan = match.group(1) if match.groups() else None
            return file_type, pan
    return None, None

def find_all_files(directory):
    results = []
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            if f.startswith("."):
                continue
            results.append((os.path.join(root, f), f))
    return results
