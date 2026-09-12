from __future__ import annotations

import io
import json
from typing import Iterator

from indexer.models import DriveBook


FOLDER_MIME = "application/vnd.google-apps.folder"
EPUB_MIME = "application/epub+zip"


class DriveClient:
    def __init__(self, service_account_json: str) -> None:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        info = json.loads(service_account_json)
        credentials = service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/drive.readonly"]
        )
        self.service = build("drive", "v3", credentials=credentials, cache_discovery=False)

    def folder_metadata(self, folder_id: str) -> dict:
        return self.service.files().get(fileId=folder_id, fields="id,name,mimeType,parents", supportsAllDrives=True).execute()

    def assert_descendant(self, folder_id: str, root_id: str) -> None:
        current = folder_id
        seen: set[str] = set()
        while current and current not in seen:
            if current == root_id:
                return
            seen.add(current)
            metadata = self.folder_metadata(current)
            parents = metadata.get("parents", [])
            current = parents[0] if parents else ""
        raise PermissionError("Selected folder is not inside the configured Drive root")

    def iter_books(self, folder_id: str, recursive: bool = True, path: list[str] | None = None) -> Iterator[DriveBook]:
        folder = self.folder_metadata(folder_id)
        current_path = [*(path or []), folder.get("name", folder_id)]
        token = None
        while True:
            response = self.service.files().list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields="nextPageToken,files(id,name,mimeType,modifiedTime,md5Checksum,size,webViewLink,parents)",
                pageToken=token,
                pageSize=1000,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()
            for item in response.get("files", []):
                if item["mimeType"] == FOLDER_MIME and recursive:
                    yield from self.iter_books(item["id"], True, current_path)
                elif item["mimeType"] == EPUB_MIME or item["name"].casefold().endswith((".txt", ".epub")):
                    yield DriveBook(**item, folderPath=current_path)
            token = response.get("nextPageToken")
            if not token:
                break

    def download(self, file_id: str) -> bytes:
        from googleapiclient.http import MediaIoBaseDownload

        request = self.service.files().get_media(fileId=file_id, supportsAllDrives=True)
        handle = io.BytesIO()
        downloader = MediaIoBaseDownload(handle, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return handle.getvalue()
