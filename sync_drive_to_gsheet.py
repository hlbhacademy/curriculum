import os
import json
import io
import pandas as pd
import gspread
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.oauth2.service_account import Credentials
from oauth2client.service_account import ServiceAccountCredentials  # for gspread

# === 環境變數與憑證讀取 ===
FOLDER_ID = "11BU1pxjEWMQJp8vThcC7thp4Mog0YEaJ"
SHEET_ID = "1dKSBHVOhvRF6sQRjkchxMNoHr2Wm-uer"  # ← 直接指定試算表 ID
SHEET_TAB = os.environ.get("GOOGLE_SHEET_TAB", "工作表1")
CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON")

credentials_info = json.loads(CREDENTIALS_JSON)

# Google Drive API 授權（下載用）
drive_creds = Credentials.from_service_account_info(
    credentials_info,
    scopes=["https://www.googleapis.com/auth/drive"]
)

# Google Sheets API 授權（gspread 上傳用）
sheets_creds = ServiceAccountCredentials.from_json_keyfile_dict(
    credentials_info,
    scopes=["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
)

drive_service = build('drive', 'v3', credentials=drive_creds)
gc = gspread.authorize(sheets_creds)

# === 1. 從 Google Drive 抓取最新的 schedule.xlsx ===
def download_latest_schedule():
    results = drive_service.files().list(
        q=f"'{FOLDER_ID}' in parents and name='schedule.xlsx' and mimeType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'",
        fields="files(id, name)",
        orderBy="modifiedTime desc",
        pageSize=1
    ).execute()
    files = results.get("files", [])
    if not files:
        raise FileNotFoundError("❌ 找不到 schedule.xlsx")

    file_id = files[0]["id"]
    request = drive_service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
    fh.seek(0)
    return fh

# === 2. 上傳到 Google Sheets 指定工作表 ===
def upload_to_google_sheet(file_stream):
    df = pd.read_excel(file_stream, sheet_name=0)

    sheet = gc.open_by_key(SHEET_ID).worksheet(SHEET_TAB)
    sheet.clear()
    sheet.update([df.columns.values.tolist()] + df.values.tolist())
    print("✅ 已從 Google Drive 同步 schedule.xlsx 至 Google Sheet。")

# 測試執行
if __name__ == "__main__":
    upload_to_google_sheet(download_latest_schedule())
