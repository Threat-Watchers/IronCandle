from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from googleapiclient.errors import HttpError
import io

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
DEFAULT_NAME = "iron_candle_market.db.gz"


def _creds() -> Credentials:
    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    refresh_token = os.getenv("GOOGLE_REFRESH_TOKEN", "").strip()
    if not all([client_id, client_secret, refresh_token]):
        raise RuntimeError("Google Drive credentials are incomplete. Need GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and GOOGLE_REFRESH_TOKEN.")
    return Credentials(token=None, refresh_token=refresh_token, token_uri="https://oauth2.googleapis.com/token", client_id=client_id, client_secret=client_secret, scopes=SCOPES)


def _service():
    return build("drive", "v3", credentials=_creds(), cache_discovery=False)


def _find_file(service, name: str, folder_id: str | None):
    q = [f"name = '{name.replace(chr(39), chr(92)+chr(39))}'", "trashed = false"]
    if folder_id:
        q.append(f"'{folder_id}' in parents")
    resp = service.files().list(q=" and ".join(q), spaces="drive", fields="files(id,name,parents,modifiedTime)", pageSize=10).execute()
    return (resp.get("files") or [None])[0]


def pull(local_path: Path, name: str):
    service = _service()
    folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "").strip() or None
    remote = _find_file(service, name, folder_id)
    if not remote:
        print("No Drive archive found; continuing without restore (bootstrap may be required).")
        return False
    request = service.files().get_media(fileId=remote["id"])
    local_path.parent.mkdir(parents=True, exist_ok=True)
    with local_path.open("wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _status, done = downloader.next_chunk()
    print(f"Restored {remote['name']} from Drive to {local_path}")
    return True


def push(local_path: Path, name: str):
    if not local_path.exists():
        raise FileNotFoundError(local_path)
    service = _service()
    folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "").strip() or None
    remote = _find_file(service, name, folder_id)
    media = MediaFileUpload(str(local_path), mimetype="application/gzip", resumable=True)
    if remote:
        service.files().update(fileId=remote["id"], media_body=media, fields="id,name,modifiedTime").execute()
        print(f"Updated Drive archive: {name}")
    else:
        body = {"name": name}
        if folder_id:
            body["parents"] = [folder_id]
        service.files().create(body=body, media_body=media, fields="id,name,modifiedTime").execute()
        print(f"Created Drive archive: {name}")


def main():
    p = argparse.ArgumentParser(description="IronCandle Google Drive market DB sync")
    p.add_argument("action", choices=["pull", "push"])
    p.add_argument("--local", default="data/iron_candle_market.db.gz")
    p.add_argument("--name", default=os.getenv("GOOGLE_DRIVE_FILENAME", DEFAULT_NAME))
    args = p.parse_args()
    try:
        if args.action == "pull":
            pull(Path(args.local), args.name)
        else:
            push(Path(args.local), args.name)
    except HttpError as exc:
        print(f"Google Drive API error: {exc}", file=sys.stderr)
        raise

if __name__ == "__main__":
    main()
