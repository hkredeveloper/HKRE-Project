"""
Google Drive Operations Module
Handles file uploads and folder creation in Google Drive
"""


import logging

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from .auth import _load_creds

_log = logging.getLogger("hkre.google_services.drive")


def create_drive_folder(folder_name, parent_id=None):
    """
    Create a folder in Google Drive.
    
    """
    creds = _load_creds()
    drive = build("drive", "v3", credentials=creds)
    body = {"name": folder_name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        body["parents"] = [parent_id]
    folder = drive.files().create(body=body, fields="id").execute()
    return folder["id"]


def find_folder_by_name(parent_id, folder_name, drive_service):
    """
    Find a folder by name under a parent. Use to reuse existing folders after script restart.
    """
    # Escape single quotes in name for Drive query (double them)
    name_escaped = str(folder_name).replace("\\", "\\\\").replace("'", "\\'")
    q = (
        f"'{parent_id}' in parents and "
        f"name = '{name_escaped}' and "
        "mimeType = 'application/vnd.google-apps.folder' and "
        "trashed = false"
    )
    try:
        result = drive_service.files().list(
            q=q, fields="files(id)", pageSize=1, supportsAllDrives=True
        ).execute()
        files = result.get("files", [])
        return files[0]["id"] if files else None
    except Exception:
        return None


def get_or_create_drive_folder(folder_name, parent_id, drive_service):
    """
    Get existing folder by name under parent, or create it. Avoids duplicate
    folders when the script is restarted after a crash (reuses empty folder).

    """
    existing = find_folder_by_name(parent_id, folder_name, drive_service)
    if existing is not None:
        return existing
    return create_drive_folder(folder_name, parent_id=parent_id)


def upload_file_to_gdrive(file_path, filename, drive_service, parent_folder_id=None):
    """
    Upload a file to Google Drive.
    
    """
    file_metadata = {'name': filename}
    if parent_folder_id:
        file_metadata['parents'] = [parent_folder_id]

    media = MediaFileUpload(file_path, mimetype='application/pdf')
    file = drive_service.files().create(
        body=file_metadata,
        media_body=media,
        fields='id'
    ).execute()
    _log.info("Uploaded: %s (ID: %s)", filename, file.get('id'))
