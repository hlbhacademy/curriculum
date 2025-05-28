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
SHEET_ID = "1dKSBHVOhvRF6sQRjkchxMNoHr2Wm-uer"
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
    scopes=[
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
)

drive_service = build('drive', 'v3', credentials=drive_creds)
gc = gspread.authorize(sheets_creds)

# === 1. 自動複製最新的 schedule.xlsx 並下載 Service Account 擁有的副本 ===
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

    origin_file_id = files[0]["id"]  # ✅ 定義檔案 ID

    # 複製為自己的副本
    copied_file_metadata = {
        "name": "schedule_copy.xlsx",
        "parents": [FOLDER_ID]
    }
    copied_file = drive_service.files().copy(
        fileId=origin_file_id,
        body=copied_file_metadata
    ).execute()
    copied_file_id = copied_file["id"]

    # 下載副本
    request = drive_service.files().get_media(fileId=copied_file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
    fh.seek(0)
    return fh

# === 2. 上傳至 Google Sheet ===
def upload_to_google_sheet(file_stream):
    df = pd.read_excel(file_stream, sheet_name=0)
    sheet = gc.open_by_key(SHEET_ID).worksheet(SHEET_TAB)

    # ✅ 徹底清除所有資料，包括多餘的列
    sheet.resize(rows=1)  # 只留下標題列
    sheet.clear()

    # ✅ 上傳最新資料
    sheet.update([df.columns.values.tolist()] + df.values.tolist())
    print(f"✅ 已從 Google Drive 同步 {len(df)} 筆資料至 Google Sheet。")

# === 測試執行 ===
if __name__ == "__main__":
    upload_to_google_sheet(download_latest_schedule())
