"""
Google Services Module
Provides functions for interacting with Google Sheets, Drive, and Docs
"""

from .auth import google_auth, get_drive_service, initialize_google_services
from .sheets import (
    get_devm,
    get_both_devm,
    get_filenames_sheet,
    insert_new_data,
    add_file_to_database,
    number_to_column_name,
)
from .drive import create_drive_folder, get_or_create_drive_folder, upload_file_to_gdrive
from .docs import update_log
from .utils import format_file_size, parse_file_size, should_download_file

__all__ = [
    # Authentication
    "google_auth",
    "get_drive_service",
    "initialize_google_services",
    # Sheets
    "get_devm",
    "get_both_devm",
    "get_filenames_sheet",
    "insert_new_data",
    "add_file_to_database",
    "number_to_column_name",
    # Drive
    "create_drive_folder",
    "get_or_create_drive_folder",
    "upload_file_to_gdrive",
    # Docs
    "update_log",
    # Utils
    "format_file_size",
    "parse_file_size",
    "should_download_file",
]
