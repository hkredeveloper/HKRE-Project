"""
File Download Module
Handles PDF downloading, uploading, and CSV conversion
"""

import os
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

from src.converters.wrapper import convert_into
from src.google_services import (
    update_log,
    upload_file_to_gdrive,
)
from .diagnostics import get_logger

_log = get_logger("file_download")

_SRPE_DOWNLOAD_HEADERS = {
    "Referer": "https://www.srpe.gov.hk/opip/selected_dev_all_development",
    "Origin": "https://www.srpe.gov.hk",
    "User-Agent": "Mozilla/5.0",
}


def filename_from_url(url):
    """Derive a .PDF filename from a download URL."""
    url_path = urlparse(url).path
    base = os.path.splitext(os.path.basename(url_path))[0]
    return f"{base}.PDF"


def filename_from_srpe_download_url(url: str) -> str:
    """
    The last path segment is often ``en`` or ``zh``, not the PDF name — using
    basename alone yields ``en.PDF``. Walk path segments and return the last
    ``*.pdf`` segment (preserved stem, ``.PDF`` extension for downstream checks).
    """
    path = urlparse(url).path
    segments = [p for p in path.split("/") if p]
    locale_like = frozenset({"en", "zh", "zhcn", "zhhk", "tc", "sc"})
    for seg in reversed(segments):
        if seg.lower() in locale_like:
            continue
        if seg.lower().endswith(".pdf"):
            stem, _ = os.path.splitext(seg)
            return f"{stem}.PDF"
    return filename_from_url(url)


def get_download_directories(version, script_dir):
    """
    Get local download directories based on version (t18m or non-t18m).
    
    Args:
        version: "t18m" or "non-t18m"
        script_dir: Base directory path for the script
    
    """
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from config import DATA_DIR
    
    base_dir = DATA_DIR / version
    sales_brochure_dir = base_dir / "sales brochure"
    register_of_transactions_dir = base_dir / "register of transactions"
    price_lists_dir = base_dir / "price lists"
    
    # Create directories if they don't exist
    os.makedirs(sales_brochure_dir, exist_ok=True)
    os.makedirs(register_of_transactions_dir, exist_ok=True)
    os.makedirs(price_lists_dir, exist_ok=True)
    
    return str(sales_brochure_dir), str(register_of_transactions_dir), str(price_lists_dir)


def upload_pdf_with_retry(file_path, filename, drive_service, parent_folder_id, max_attempts=3):
    """
    Upload a PDF file to Google Drive with retry logic and exponential backoff.
    
    Returns:
        True if upload successful, False otherwise
    """
    for attempt in range(max_attempts):
        try:
            upload_file_to_gdrive(file_path, filename, drive_service, parent_folder_id)
            return True
        except Exception as e:
            _log.warning("Upload failed for %s, attempt %s: %s", filename, attempt + 1, e)
            if attempt < max_attempts - 1:
                time.sleep(2 ** attempt)  # Exponential backoff: 1s, 2s, 4s
    return False


def convert_and_upload_csv(
    file_path,
    filename,
    dir,
    drive_service,
    prices_folder_id,
    trans_folder_id,
):
    """
    Convert PDF to CSV and upload to appropriate Google Drive folder.
    
    Returns:
        True if conversion and upload successful, False otherwise
    """
    filename_basename = os.path.basename(file_path)
    name_upper = filename_basename.upper()

    # Only convert specific PDF types
    if not name_upper.endswith(("PO.PDF", "PR.PDF", "RT.PDF")):
        return False

    csv_temp_path = os.path.join(dir, os.path.splitext(filename_basename)[0] + ".csv")

    # Convert PDF to CSV
    try:
        convert_into(file_path, csv_temp_path, pages='all', stream=True)
        if not os.path.exists(csv_temp_path):
            return False
    except Exception as e:
        _log.error("CSV conversion failed for %s: %s", filename_basename, e)
        return False

    # Determine which folder to upload CSV to based on file type
    if ("PO.PDF" in name_upper) or ("PR.PDF" in name_upper):
        gdrive_folder_id = prices_folder_id
    elif "RT.PDF" in name_upper:
        gdrive_folder_id = trans_folder_id
    else:
        gdrive_folder_id = prices_folder_id  # default if unknown

    # Upload CSV to Google Drive
    try:
        upload_file_to_gdrive(
            csv_temp_path,
            os.path.basename(csv_temp_path),
            drive_service,
            gdrive_folder_id
        )

        # Delete the temporary CSV file after upload
        try:
            if os.path.exists(csv_temp_path):
                os.remove(csv_temp_path)
        except Exception as e:
            _log.warning("Could not remove temp CSV %s: %s", csv_temp_path, e)

        return True

    except Exception as e:
        _log.error("Failed to upload CSV for %s: %s", filename_basename, e)
        return False


def process_single_pdf_http(
    url,
    filename,
    file_path,
    dir,
    parent_folder_id,
    drive_service,
    prices_folder_id,
    trans_folder_id,
    docs,
    timeout=120,
):
    """
    Download one PDF via plain HTTP (requests), then upload and convert to CSV.

    Returns (success, timeout_occurred). For HTTP downloads, timeouts are surfaced as failures
    and timeout_occurred is always False here.
    """
    try:
        resp = requests.get(
            url,
            headers=_SRPE_DOWNLOAD_HEADERS,
            timeout=timeout,
            stream=True,
        )
        resp.raise_for_status()
        Path(file_path).parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    fh.write(chunk)
    except Exception as exc:
        update_log(docs, f"HTTP download failed for {filename}: {exc}. Skipping.\n")
        return False, False

    if not os.path.exists(file_path):
        update_log(docs, f"Expected downloaded file not found: {file_path}\n")
        return False, False

    update_log(docs, f"Downloaded: {filename}.\n")

    upload_pdf_with_retry(file_path, filename, drive_service, parent_folder_id)
    convert_and_upload_csv(
        file_path, filename, dir, drive_service, prices_folder_id, trans_folder_id
    )

    try:
        os.remove(file_path)
    except Exception as exc:
        _log.warning("Could not remove PDF %s: %s", file_path, exc)

    return True, False


def download_pdf_http(
    pdf,
    dir,
    parent_folder_id,
    drive_service,
    prices_folder_id,
    trans_folder_id,
    development_name,
    version,
    docs,
    already_uploaded=None,
):
    """
    Batch-download PDF URLs via HTTPS (requests), upload to Drive, convert PO/PR/RT to CSV when applicable.

    Returns False (no timeout concept); compatible with callers that expect a boolean retry signal.
    """
    for text_key, url in pdf.items():
        filename = filename_from_srpe_download_url(url)
        file_path = os.path.join(dir, filename)

        success, _ = process_single_pdf_http(
            url=url,
            filename=filename,
            file_path=file_path,
            dir=dir,
            parent_folder_id=parent_folder_id,
            drive_service=drive_service,
            prices_folder_id=prices_folder_id,
            trans_folder_id=trans_folder_id,
            docs=docs,
        )

        if success and already_uploaded is not None:
            already_uploaded.add(text_key)

        time.sleep(2)

    return False  # no timeout for HTTP path

