from flask import Flask, jsonify, request, render_template, redirect, url_for, session
from datetime import datetime, timedelta
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from authlib.integrations.flask_client import OAuth
from functools import lru_cache
import os, secrets, json
import re

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "supersecret")

# ===== Google OAuth 設定 =====
oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=os.environ["GOOGLE_CLIENT_ID"],
    client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
    authorize_params={"hd": "hlbh.hlc.edu.tw"}
)

@app.route("/login")
def login():
    nonce = secrets.token_urlsafe(16)
    session["nonce"] = nonce
    return google.authorize_redirect(
        redirect_uri=url_for("callback", _external=True),
        nonce=nonce
    )

@app.route("/callback")
def callback():
    token = google.authorize_access_token()
    nonce = session.pop("nonce", None)
    if not nonce:
        return "❌ 驗證失敗，請重新登入", 400
    user_info = google.parse_id_token(token, nonce=nonce)
    if not user_info["email"].endswith("@hlbh.hlc.edu.tw"):
        return "🚫 僅限 @hlbh.hlc.edu.tw 網域帳號登入", 403
    session["user"] = user_info
    return redirect("/")

@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect("/")

# ===== 課表讀取（快取） =====
@lru_cache(maxsize=1)
def load_schedule():
    credentials_info = json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    credentials = ServiceAccountCredentials.from_json_keyfile_dict(credentials_info, scope)
    client = gspread.authorize(credentials)

    spreadsheet_id = "17sI2YSDCec_Olm3CiqW57wSS63fJiGXN7x-9-jbcCJo"
    worksheet_name = "工作表1"
    sheet = client.open_by_key(spreadsheet_id).worksheet(worksheet_name)

    data = sheet.get_all_records()
    df = pd.DataFrame(data)
    df = df[df["班級名稱"].notna() & df["教師名稱"].notna() & df["星期"].notna() & df["節次"].notna()]
    return df

# ===== 班級排序函式：依「英會商資多」類型、高三至高一順序 =====
def sort_class_names(names):
    type_order = {"英": 1, "會": 2, "商": 3, "資": 4, "多": 5}
    def sort_key(name):
        match = re.match(r"(.)(\d)", name)
        prefix = match.group(1) if match else ""
        year = int(match.group(2)) if match else 0
        return (type_order.get(prefix, 99), -year, name)
    return sorted(names, key=sort_key)

# ===== 主畫面顯示 =====
@app.route("/")
def index():
    user = session.get("user")
    if not user:
        return redirect("/login")

    df = load_schedule()
    raw_classes = df["班級名稱"].dropna().unique()
    class_names = sort_class_names(raw_classes)
    teacher_names = sorted(df["教師名稱"].dropna().unique())
    room_names = sorted(df["教室名稱"].dropna().unique())
    update_time = datetime.now().strftime("%m月%d日").lstrip("0").replace(" 0", " ")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%m月%d日").lstrip("0").replace(" 0", " ")

    # 加入 weekday ➜ 對應日期（例如 星期一 ➜ 5/27）
    date_map = df.groupby("星期")["日期"].first().to_dict()

    return render_template("index.html",
        class_names=class_names,
        teacher_names=teacher_names,
        room_names=room_names,
        update_time=update_time,
        email=user["email"],
        yesterday=yesterday,
        weekday_dates=date_map  # 🆕 日期對應星期
    )
