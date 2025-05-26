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

# ===== Google Sheets 課表快取讀取 =====
def load_schedule():
    credentials_info = json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    credentials = ServiceAccountCredentials.from_json_keyfile_dict(credentials_info, scope)
    client = gspread.authorize(credentials)
    sheet = client.open_by_key(os.environ["GOOGLE_SHEET_ID"]).worksheet(os.environ.get("GOOGLE_SHEET_TAB", "工作表1"))
    df = pd.DataFrame(sheet.get_all_records())
    df = df[df["班級名稱"].notna() & df["教師名稱"].notna() & df["星期"].notna() & df["節次"].notna()]
    return df

# ===== 班級排序邏輯 =====
def sort_class_names(names):
    type_order = {"英": 1, "會": 2, "商": 3, "資": 4, "多": 5}
    def sort_key(name):
        match = re.match(r"(.)(\d)", name)
        prefix = match.group(1) if match else ""
        year = int(match.group(2)) if match else 0
        if any(x in name for x in ["彈", "本土", "選"]):
            return (999, 999, name)
        return (type_order.get(prefix, 998), -year, name)
    return sorted(names, key=sort_key)

# ===== 首頁路由 =====
@app.route("/")
def index():
    user = session.get("user")
    if not user:
        return redirect("/login")
    df = load_schedule()
    class_names = sort_class_names(df["班級名稱"].dropna().unique())
    teacher_names = sorted(df["教師名稱"].dropna().unique())
    room_names = sorted(df["教室名稱"].dropna().unique())
    date_map = df.groupby("星期")["日期"].first().to_dict()
    update_time = datetime.now().strftime("%m月%d日").lstrip("0").replace(" 0", " ")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%m月%d日").lstrip("0").replace(" 0", " ")
    return render_template("index.html",
        class_names=class_names,
        teacher_names=teacher_names,
        room_names=room_names,
        weekday_dates=date_map,
        update_time=update_time,
        email=user["email"],
        yesterday=yesterday
    )

# ===== 課表查詢 API =====
@app.route("/schedule/<mode>/<target>")
def schedule(mode, target):
    df = load_schedule()
    col_map = {"class": "班級名稱", "teacher": "教師名稱", "room": "教室名稱"}
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

# ===== 可調課建議 API =====
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

# ===== ✅ 新增 /sync 路由 =====
@app.route("/sync", methods=["GET"])
def manual_sync():
    try:
        from sync_drive_to_gsheet import upload_to_google_sheet, download_latest_schedule
        upload_to_google_sheet(download_latest_schedule())
        return "✅ 課表同步成功", 200
    except Exception as e:
        return f"❌ 同步失敗：{str(e)}", 500

if __name__ == "__main__":
    app.run(debug=True)
