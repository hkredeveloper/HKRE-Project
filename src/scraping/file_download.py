"""
File Download Module
Handles PDF downloading, uploading, and CSV conversion
"""

import os
import time
from urllib.parse import urlparse

from src.converters.wrapper import convert_into
from src.google_services import (
    update_log,
    upload_file_to_gdrive,
)
from .web_interaction import safe_driver_get


def filename_from_url(url):
    """Derive a .PDF filename from a download URL."""
    url_path = urlparse(url).path
    base = os.path.splitext(os.path.basename(url_path))[0]
    return f"{base}.PDF"


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


def wait_for_download(file_path, filename, docs, timeout=300):
    """
    Wait for a file download to complete by monitoring file size.
    
    
    Returns:
        True if download completed, False if timeout occurred
    """
    start_time = time.time()
    prev_size = -1

    while True:
        if os.path.exists(file_path):
            size = os.path.getsize(file_path)
            if size == prev_size:
                # File size stabilized - download complete
                return True
            prev_size = size

        # Check for timeout
        if time.time() - start_time > timeout:
            update_log(docs, f"Timeout downloading: {filename}.\nRestarting Process...\n")
            print(f"Timeout downloading: {filename}")
            return False

        time.sleep(2)


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
            print(f"Upload failed for {filename}, attempt {attempt+1}: {e}")
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
        print(f"[ERROR] Conversion failed for {filename_basename}: {e}")
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
            print(f"[WARN] Could not remove temp CSV {csv_temp_path}: {e}")

        return True

    except Exception as e:
        print(f"[ERROR] Failed to upload CSV for {filename_basename}: {e}")
        return False


def process_single_pdf(
    driver,
    filename,
    url,
    file_path,
    dir,
    parent_folder_id,
    drive_service,
    prices_folder_id,
    trans_folder_id,
    development_name,
    version,
    docs,
):
    """
    Process a single PDF: download, upload, and convert to CSV if applicable.
    
    Note: Download decision is now based on property-level changes detected in devm t18m database.

    
    Returns:
        tuple: (success, timeout_occurred)
            - success: True if file was processed successfully
            - timeout_occurred: True if download timed out
    """
    # Navigate to URL
    if not safe_driver_get(driver, url):
        update_log(docs, f"Failed to load {url} after retries, URL likely don't exist. Skipping {filename}.\n")
        return False, False

    # Wait for download to complete
    download_complete = wait_for_download(file_path, filename, docs)
    if not download_complete:
        # Timeout already logged in wait_for_download; just signal timeout upward
        return False, True  # Timeout occurred

    # Proceed if file exists
    if not os.path.exists(file_path):
        update_log(
            docs,
            f"Expected downloaded file not found on disk after wait: {file_path}\n",
        )
        return False, False

    update_log(docs, f"Downloaded: {filename}.\n")

    # Upload PDF to Google Drive
    uploaded = upload_pdf_with_retry(file_path, filename, drive_service, parent_folder_id)

    # Convert to CSV and upload if applicable
    convert_and_upload_csv(
        file_path,
        filename,
        dir,
        drive_service,
        prices_folder_id,
        trans_folder_id,
    )

    # Clean up local PDF file after upload
    try:
        os.remove(file_path)
    except Exception as e:
        print(f"[WARN] Could not remove PDF {file_path}: {e}")

    return True, False


def download_pdf(
    driver,
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
    Download PDFs from URLs, upload to Google Drive, and convert to CSV if applicable.

    If already_uploaded is a set (mutable), we add each successfully processed PDF's
    text key to it so that on retry we can skip them (resume where we left off).
    """
    # Configure download behavior
    params = {
        "behavior": "allow",
        "downloadPath": dir
    }
    driver.execute_cdp_cmd("Page.setDownloadBehavior", params)

    timeout_download = False

    for idx, (text_key, url) in enumerate(pdf.items(), start=1):
        filename = filename_from_url(url)
        file_path = os.path.join(dir, filename)

        # Process single PDF file
        success, timeout_occurred = process_single_pdf(
            driver=driver,
            filename=filename,
            url=url,
            file_path=file_path,
            dir=dir,
            parent_folder_id=parent_folder_id,
            drive_service=drive_service,
            prices_folder_id=prices_folder_id,
            trans_folder_id=trans_folder_id,
            development_name=development_name,
            version=version,
            docs=docs,
        )

        # If timeout occurred, break out of loop (timeout already logged)
        if timeout_occurred:
            timeout_download = True
            break

        # Record successful upload so we skip this PDF on retry (resume)
        if success and already_uploaded is not None:
            already_uploaded.add(text_key)

        # Add delay between downloads to reduce API stress
        time.sleep(3)

    return timeout_download

