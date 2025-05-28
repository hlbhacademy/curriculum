import os
import json
import io
import pandas as pd
import gspread
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.oauth2.service_account import Credentials
from oauth2client.service_account import ServiceAccountCredentials  # for gspread

# === 1. 設定環境變數與憑證 ===
FOLDER_ID = "11BU1pxjEWMQJp8vThcC7thp4Mog0YEaJ"
SHEET_ID = "1dKSBHVOhvRF6sQRjkchxMNoHr2Wm-uer"
SHEET_TAB = os.environ.get("GOOGLE_SHEET_TAB", "工作表1")
CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON")

credentials_info = json.loads(CREDENTIALS_JSON)

# === 2. 授權 Drive / Sheets API ===
drive_creds = Credentials.from_service_account_info(
    credentials_info,
    scopes=["https://www.googleapis.com/auth/drive"]
)

sheets_creds = ServiceAccountCredentials.from_json_keyfile_dict(
    credentials_info,
    scopes=[
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
)

drive_service = build('drive', 'v3', credentials=drive_creds)
gc = gspread.authorize(sheets_creds)

# === 3. 下載最新 schedule.xlsx 為二進位 Excel ===
def download_latest_schedule():
    results = drive_service.files().list(
        q=f"'{FOLDER_ID}' in parents and name='schedule.xlsx' and mimeType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'",
        fields="files(id, name)",
        orderBy="modifiedTime desc",
        pageSize=1
    ).execute()

    files = results.get("files", [])
    print("🔍 Google Drive 回傳的檔案列表：", files)

    if not files:
        raise FileNotFoundError("❌ 找不到 schedule.xlsx")

    file_id = files[0]["id"]
    print("✅ 找到檔案 ID：", file_id)

    # === 下載 Excel 原始檔為二進位 ===
    request = drive_service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done =
