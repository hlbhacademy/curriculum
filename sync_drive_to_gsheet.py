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
        fields="files(id, name, mimeType)",
        orderBy="modifiedTime desc",
        pageSize=1
    ).execute()

    files = results.get("files", [])
    print("🔍 Google Drive 回傳的檔案列表：", files)

    if not files:
        raise FileNotFoundError("❌ 找不到 schedule.xlsx")

    source_file_id = files[0]["id"]
    print("✅ 找到原始檔案 ID：", source_file_id)

    # === 複製為自己的副本，指定 .xlsx MIME 類型避免轉為 Google Sheet ===
    copied_file_metadata = {
        "name": "schedule_copy.xlsx",
        "parents": [FOLDER_ID],
        "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    }
    copied_file = drive_service.files().copy(
        fileId=source_file_id,
        body=copied_file_metadata
    ).execute()

    copied_file_id = copied_file["id"]
    print("📄 複製副本 ID：", copied_file_id)

    # === 下載為二進位 ===
    request = drive_service.files().get_media(fileId=copied_file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
    fh.seek(0)
    return fh

# === 4. 上傳至 Google Sheet ===
def upload_to_google_sheet(file_stream):
    df = pd.read_excel(file_stream, sheet_name=0)
    sheet = gc.open_by_key(SHEET_ID).worksheet(SHEET_TAB)

    # === 清除所有資料，避免殘影 ===
    sheet.resize(rows=1)
    sheet.clear()

    # === 上傳新資料 ===
    sheet.update([df.columns.values.tolist()] + df.values.tolist())
    print(f"✅ 上傳完成，共 {len(df)} 筆資料")
