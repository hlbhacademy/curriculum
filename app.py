from flask import Flask, jsonify, request, render_template, redirect, url_for, session
from datetime import datetime, timedelta
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from authlib.integrations.flask_client import OAuth
from functools import lru_cache
import os, secrets, json, re

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "supersecret")

# ✅ Google OAuth 認證
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

# ✅ 讀取 Sheets 並快取
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

    # ✅ 過濾必要欄位
    df = df[
        df["原始班級名稱"].notna() &
        df["原始教師名稱"].notna() &
        df["原始星期"].notna() &
        df["原始節次"].notna()
    ]
    return df

# ✅ 班級排序：英 > 會 > 商 > 資 > 多，並依高三至高一
def sort_class_names(names):
    type_order = {"英": 1, "會": 2, "商": 3, "資": 4, "多": 5}
    def sort_key(name):
        match = re.match(r"(.)(\d)", name)
        prefix = match.group(1) if match else ""
        year = int(match.group(2)) if match else 0
        return (type_order.get(prefix, 99), -year, name)
    return sorted(names, key=sort_key)

# ✅ 主畫面
@app.route("/")
def index():
    user = session.get("user")
    if not user:
        return redirect("/login")

    df = load_schedule()
    class_names = sort_class_names(df["原始班級名稱"].dropna().unique())
    teacher_names = sorted(df["原始教師名稱"].dropna().unique())
    room_names = sorted(df["原始教室名稱"].dropna().unique())

    update_time = datetime.now().strftime("%m月%d日").lstrip("0").replace(" 0", " ")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%m月%d日").lstrip("0").replace(" 0", " ")

    # 日期對應星期（取第一筆出現日期）
    weekday_dates = df.groupby("原始星期")["日期"].first().to_dict()

    return render_template("index.html",
        class_names=class_names,
        teacher_names=teacher_names,
        room_names=room_names,
        update_time=update_time,
        email=user["email"],
        yesterday=yesterday,
        weekday_dates=weekday_dates
    )

# ✅ 查詢課表 API
@app.route("/schedule/<mode>/<target>")
def schedule(mode, target):
    df = load_schedule()
    col_map = {
        "class": "原始班級名稱",
        "teacher": "原始教師名稱",
        "room": "原始教室名稱"
    }
    if mode not in col_map:
        return jsonify({"error": "無效的查詢模式"}), 400

    col = col_map[mode]
    sub_df = df[df[col] == target]
    data = {}
    for _, row in sub_df.iterrows():
        key = f"{int(row['原始星期'])}-{int(row['原始節次'])}"
        data[key] = {
            "subject": row["原始科目名稱"],
            "teacher": row["原始教師名稱"],
            "room": row["原始教室名稱"],
            "class": row["原始班級名稱"]
        }
    return jsonify(data)

if __name__ == '__main__':
    app.run(debug=True)
