from flask import Flask, jsonify, request, render_template, redirect, url_for, session
from datetime import datetime, timedelta
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from authlib.integrations.flask_client import OAuth
import os, secrets

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "supersecret")

# Google OAuth 設定
oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
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

# ===== Google Sheets 讀取 =====
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
credentials = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
client = gspread.authorize(credentials)
spreadsheet_id = "17sI2YSDCec_Olm3CiqW57wSS63fJiGXN7x-9-jbcCJo"
worksheet_name = "工作表1"

def load_schedule():
    sheet = client.open_by_key(spreadsheet_id).worksheet(worksheet_name)
    data = sheet.get_all_records()
    df = pd.DataFrame(data)
    df = df[df["班級名稱"].notna() & df["教師名稱"].notna() & df["星期"].notna() & df["節次"].notna()]
    return df

@app.route("/")
def index():
    user = session.get("user")
    if not user:
        return redirect("/login")

    df = load_schedule()
    class_names = sorted(df["班級名稱"].dropna().unique())
    teacher_names = sorted(df["教師名稱"].dropna().unique())
    room_names = sorted(df["教室名稱"].dropna().unique())
    update_time = datetime.now().strftime("%m月%d日").lstrip("0").replace(" 0", " ")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%m月%d日").lstrip("0").replace(" 0", " ")

    return render_template("index.html",
        class_names=class_names,
        teacher_names=teacher_names,
        room_names=room_names,
        update_time=update_time,
        email=user["email"],
        yesterday=yesterday
    )

# ===== 調課查詢 API =====
@app.route("/swap-options", methods=["POST"])
def swap_options():
    df = load_schedule()
    req = request.json
    class_name = req["target"]
    day, period = int(req["weekday"]), int(req["period"])

    src = df[(df["班級名稱"] == class_name) & (df["星期"] == day) & (df["節次"] == period)]
    if src.empty:
        return jsonify([])
    src = src.iloc[0]

    if src["科目名稱"] in ["團體活動時間", "本土語文", "多元選修", "彈性學習時間"]:
        return jsonify([])

    options = []
    others = df[df["班級名稱"] == class_name].groupby("教師名稱")
    for b_teacher, b_rows in others:
        if b_teacher == src["教師名稱"]:
            continue
        for _, b in b_rows.iterrows():
            b_day, b_period = int(b["星期"]), int(b["節次"])

            # 禁止條件：第1~7節與第8節不得對調（只允許8對8）
            if (period == 8 and b_period <= 7) or (b_period == 8 and period <= 7):
                continue

            if (b_day == day and b_period == period):
                continue

            if b["科目名稱"] in ["團體活動時間", "本土語文", "多元選修", "彈性學習時間"]:
                continue

            a_empty = df[(df["教師名稱"] == src["教師名稱"]) & (df["星期"] == b_day) & (df["節次"] == b_period)].empty
            b_empty = df[(df["教師名稱"] == b_teacher) & (df["星期"] == day) & (df["節次"] == period)].empty
            if not (a_empty and b_empty):
                continue

            reasons = []

            if df[(df["教師名稱"] == b_teacher) & (df["班級名稱"] == class_name) & (df["星期"] == day)].shape[0] >= 2:
                reasons.append("同一老師一天對同班授課三次以上")
            if df[(df["教師名稱"] == src["教師名稱"]) & (df["班級名稱"] == class_name) & (df["星期"] == b_day)].shape[0] >= 2:
                reasons.append("同一老師一天對同班授課三次以上")
            if {period, b_period} == {4, 5}:
                same_day = df[(df["教師名稱"] == src["教師名稱"]) & (df["星期"] == day) & (df["班級名稱"] == class_name)]
                if same_day.shape[0] > 1:
                    reasons.append("教師同天四五節連堂")

            options.append({
                "day": b_day,
                "period": b_period,
                "original_period": period,
                "recommended": not reasons,
                "reason": "、".join(reasons)
            })

    return jsonify(options)

# ===== 課表資料查詢 API =====
@app.route("/schedule/<mode>/<target>")
def schedule(mode, target):
    df = load_schedule()
    col_map = {
        "class": "班級名稱",
        "teacher": "教師名稱",
        "room": "教室名稱"
    }
    if mode not in col_map:
        return jsonify({"error": "無效的查詢模式"}), 400

    col = col_map[mode]
    sub_df = df[df[col] == target]
    data = {}
    for _, row in sub_df.iterrows():
        key = f"{int(row['星期'])}-{int(row['節次'])}"
        data[key] = {
            "subject": row["科目名稱"],
            "teacher": row["教師名稱"],
            "room": row["教室名稱"],
            "class": row["班級名稱"]
        }
    return jsonify(data)

if __name__ == '__main__':
    app.run(debug=True)
