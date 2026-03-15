# Standard library imports
import os
import sys
import time
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Third-party imports
from selenium.webdriver.support.ui import WebDriverWait

# Local imports - Configuration
from config import (
    WEBLOAD_TIMEOUT,
    CHROME_EXE_PATH,
    PARENT_FOLDER_ID,
    T18M_URL,
    NON_T18M_URL,
    BASE_DIR,
)

# Local imports - Scraping
from src.scraping import launch_web, process_single_property, restart_browser, get_download_directories, build_lookup_index

# Local imports - Google services
from src.google_services import (
    initialize_google_services,
    get_both_devm,
    update_log,
    create_drive_folder,
)

# ============================================================================
# Global Initialization
# ============================================================================

# Initialize all Google services in one call (Sheets, Docs, and Drive)
spreadsheet, docs, drive_service = initialize_google_services()


def main(target_web, version, run_folder_id, j, sheet, devm_lookup):
    """
    Main scraping function for property developments.
    Uses combined devm_lookup (from both tabs); inserts new rows into the given sheet.
    """
    # Create folders for organizing converted files
    # PO and PR files → Prices folder, RT files → Transactions folder
    prices = create_drive_folder('Prices', parent_id=run_folder_id)
    transactions = create_drive_folder('Transactions', parent_id=run_folder_id)

    # Get download directories based on version
    sales_brochure_files_dir, register_of_transactions_files_dir, price_lists_files_dir = get_download_directories(version, str(BASE_DIR))

    # Launch the web browser
    driver = launch_web(target_web, webload_timeout=WEBLOAD_TIMEOUT, chrome_exe_path=CHROME_EXE_PATH)
    time.sleep(2)

    # Fetch the list of items
    el_list = WebDriverWait(driver, WEBLOAD_TIMEOUT).until(
        lambda driver: driver.find_elements("xpath", "//*[@id='sort_table']/tbody/tr")
    )

    end = len(el_list)
    tl_loop = time.time()
    cached_folder_ids = {}  # Cache folder IDs to avoid duplicates on timeout retries
    already_uploaded_pdfs = set()  # PDF text keys already uploaded this run; cleared on success, resume on retry
    logged_updates_for_rows = set()  # Row indices we've already logged "Updates to Existing File" for (avoid repeat on retry)

    # Process each property in the listing
    while j <= end:
        try:
            timeout_occurred, driver = process_single_property(
                driver=driver,
                row_index=j,
                target_web=target_web,
                webload_timeout=WEBLOAD_TIMEOUT,
                chrome_exe_path=CHROME_EXE_PATH,
                devm_lookup=devm_lookup,
                sheet=sheet,
                run_folder_id=run_folder_id,
                sales_brochure_files_dir=sales_brochure_files_dir,
                register_of_transactions_files_dir=register_of_transactions_files_dir,
                price_lists_files_dir=price_lists_files_dir,
                drive_service=drive_service,
                prices_folder_id=prices,
                transactions_folder_id=transactions,
                version=version,
                docs=docs,
                cached_folder_ids=cached_folder_ids,
                already_uploaded_pdfs=already_uploaded_pdfs,
                logged_updates_for_rows=logged_updates_for_rows,
            )
            
            # If timeout occurred, driver was already restarted in process_single_property
            if timeout_occurred:
                update_log(
                    docs,
                    f"Timeout flag received for row {j}. Retrying same row (j unchanged).\n",
                )
                time.sleep(2)
                continue
            
            # Move to next property
            j += 1
        
        except Exception as e:
            err_name = type(e).__name__
            err_msg = str(e).split('\n')[0].strip()
            if err_name == 'NoSuchElementException':
                explanation = "The table row or link was not found. The page may have fewer rows than expected, or the page structure may have changed."
            elif err_name in ('TimeoutException', 'WebDriverException') and 'timeout' in str(e).lower():
                explanation = "The page or request took too long to respond."
            elif err_name in ('SSLEOFError', 'SSLError', 'ConnectionError') or 'ssl' in str(e).lower() or 'connection' in str(e).lower():
                explanation = "The connection to the website or server was dropped unexpectedly."
            else:
                explanation = "An unexpected error occurred."
            update_log(
                docs,
                f"Error at row {j}: {err_name}: {err_msg}\n"
                f"({explanation})\n"
                f"Retrying same row.\n\n",
            )
            time.sleep(3)
            driver = restart_browser(driver, target_web, WEBLOAD_TIMEOUT, CHROME_EXE_PATH, delay=3)
            update_log(docs, f"Browser restarted after exception at row {j}. Retrying same row.\n")
            continue

    update_log(docs, f'Total time: {(time.time() - tl_loop) / 60:.2f} min\n\n')
    driver.quit()
    return j


if __name__ == "__main__":
    # ============================================================================
    # Main Execution
    # ============================================================================
    
    # Get today's date for folder naming and logging
    today_date = datetime.now().strftime("%Y-%m-%d")
    
    # Create main folder for this scraping session
    folder_name = f"Metric Job - {today_date}"
    folder_id = create_drive_folder(folder_name, parent_id=PARENT_FOLDER_ID)
    
    # Load both devm tabs and build single combined lookup for comparison
    sheet_t18m, sheet_non_t18m, combined_df = get_both_devm(spreadsheet)
    combined_df = combined_df.apply(lambda col: col.map(lambda x: x.replace('\n', '').strip() if isinstance(x, str) else x))
    devm_lookup = build_lookup_index(combined_df)

    # Create subfolder for t18m files
    t18ms_folder_id = create_drive_folder('t18m files', parent_id=folder_id)
    
    # Log scraping start
    # update_log(docs, f"Date of Scrape: {today_date}\nFor t18m\n\n")

    # # ============================================================================
    # # Scrape t18m properties
    # # ============================================================================
    # main(T18M_URL, "t18m", t18ms_folder_id, j=1, sheet=sheet_t18m, devm_lookup=devm_lookup)
    
    # update_log(docs, f"finished t18m\n\n")
    
    # ============================================================================
    # Scrape non-t18m properties
    # ============================================================================
    non_t18ms_folder_id = create_drive_folder('non-t18m files', parent_id=folder_id)
    update_log(docs, f"For non-t18m\n\n")
    main(NON_T18M_URL, "non-t18m", non_t18ms_folder_id, j=215, sheet=sheet_non_t18m, devm_lookup=devm_lookup)
    update_log(docs, "finished non-t18m and automation")
