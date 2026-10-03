from __future__ import annotations

import argparse
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
FOLDER_NAME = "IronCandle_Backups"


def main():
    p = argparse.ArgumentParser(description="One-time Google Drive OAuth bootstrap for IronCandle")
    p.add_argument("--client-json", default="client_secret.json")
    p.add_argument("--folder-name", default=FOLDER_NAME)
    args = p.parse_args()
    path = Path(args.client_json)
    if not path.exists():
        raise SystemExit(f"Client JSON not found: {path}")

    flow = InstalledAppFlow.from_client_secrets_file(str(path), SCOPES)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    service = build("drive", "v3", credentials=creds, cache_discovery=False)

    q = f"name = '{args.folder_name.replace(chr(39), chr(92)+chr(39))}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    found = service.files().list(q=q, spaces="drive", fields="files(id,name)", pageSize=10).execute().get("files", [])
    if found:
        folder_id = found[0]["id"]
    else:
        folder = service.files().create(body={"name": args.folder_name, "mimeType": "application/vnd.google-apps.folder"}, fields="id,name").execute()
        folder_id = folder["id"]

    print("\nIRONCANDLE GOOGLE DRIVE GITHUB SECRETS\n")
    print("GOOGLE_CLIENT_ID=" + str(creds.client_id))
    print("GOOGLE_CLIENT_SECRET=" + str(creds.client_secret))
    print("GOOGLE_REFRESH_TOKEN=" + str(creds.refresh_token))
    print("GOOGLE_DRIVE_FOLDER_ID=" + str(folder_id))
    print("\nKeep GOOGLE_REFRESH_TOKEN private. The app only manages files it creates in the IronCandle_Backups folder.")

if __name__ == "__main__":
    main()
