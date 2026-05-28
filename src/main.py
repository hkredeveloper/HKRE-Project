# Standard library imports
import os
import sys
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Local imports - Configuration
from config import (
    PARENT_FOLDER_ID,
)

# Local imports - Scraping
from src.scraping import build_lookup_index
from src.scraping.diagnostics import configure_scraper_logging
from src.scraping.srpe_api import main_api, list_dev_ids

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


if __name__ == "__main__":
    # ============================================================================
    # Main Execution
    # ============================================================================
    configure_scraper_logging()

    # Get today's date for folder naming and logging
    today_date = datetime.now().strftime("%Y-%m-%d")
    
    # Create main folder for this scraping session
    folder_name = f"Metric Job - {today_date}"
    folder_id = create_drive_folder(folder_name, parent_id=PARENT_FOLDER_ID)
    
    # Load both devm tabs and build single combined lookup for comparison
    sheet_t18m, sheet_non_t18m, combined_df = get_both_devm(spreadsheet)
    combined_df = combined_df.apply(
        lambda col: col.map(lambda x: x.replace('\n', ' ').strip() if isinstance(x, str) else x)
    )
    devm_lookup = build_lookup_index(combined_df)

    # # Create subfolder for t18m files
    # t18ms_folder_id = create_drive_folder('t18m files', parent_id=folder_id)
    #
    # # ============================================================================
    # # Scrape t18m properties
    # # ============================================================================
    # update_log(docs, f"Date of Scrape: {today_date}\nFor t18m\n\n")
    # t18m_dev_ids = list_dev_ids(version="t18m")
    # main_api(
    #     version="t18m",
    #     run_folder_id=t18ms_folder_id,
    #     start_idx=1,
    #     sheet=sheet_t18m,
    #     devm_lookup=devm_lookup,
    #     drive_service=drive_service,
    #     docs=docs,
    #     dev_ids=t18m_dev_ids,
    # )
    # update_log(docs, "finished t18m\n\n")

    # ============================================================================
    # Scrape non-t18m properties
    # ============================================================================
    non_t18ms_folder_id = create_drive_folder('non-t18m files', parent_id=folder_id)
    update_log(
        docs,
        f"Date of Scrape: {today_date}\nFor non-t18m only (resume from development index 514)\n\n",
    )

    non_t18m_dev_ids = list_dev_ids(version="non-t18m")
    NON_T18M_START_INDEX = 514  # 1-based index into SRPE listing order (manual resume cursor)
    main_api(
        version="non-t18m",
        run_folder_id=non_t18ms_folder_id,
        start_idx=NON_T18M_START_INDEX,
        sheet=sheet_non_t18m,
        devm_lookup=devm_lookup,
        drive_service=drive_service,
        docs=docs,
        dev_ids=non_t18m_dev_ids,
    )

    update_log(docs, "finished non-t18m and automation")
