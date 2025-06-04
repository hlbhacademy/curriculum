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
    name="google",
    client_id=os.environ["GOOGLE_CLIENT_ID"],
    client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
    authorize_params={"hd": "hlbh.hlc.edu.tw"},
)

@app.route("/login")
def login():
    nonce = secrets.token_urlsafe(16)
    session["nonce"] = nonce
    return google.authorize_redirect(
        redirect_uri=url_for("callback", _external=True), nonce=nonce
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

# ===== 課表讀取與快取 =====
@lru_cache(maxsize=1)
def load_schedule():
    credentials_info = json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    credentials = ServiceAccountCredentials.from_json_keyfile_dict(credentials_info, scope)
    client = gspread.authorize(credentials)

    worksheet_name = os.environ.get("GOOGLE_SHEET_TAB", "工作表1")
    sheet = client.open_by_key(os.environ["GOOGLE_SHEET_ID"]).worksheet(worksheet_name)
    df = pd.DataFrame(sheet.get_all_records())

    print(f"📦 原始資料共 {len(df)} 筆")
    df = df.dropna(how="all")
    print(f"🧹 移除空白列後剩 {len(df)} 筆")

    for col in ["班級名稱", "教師名稱", "科目名稱"]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()
            df[col] = df[col].replace("nan", "")

    df["星期"] = pd.to_numeric(df["星期"], errors="coerce")
    df["節次"] = pd.to_numeric(df["節次"], errors="coerce")

    valid_df = df[
        (df["班級名稱"] != "") &
        (df["教師名稱"] != "") &
        (df["科目名稱"] != "") &
        (df["星期"].notna()) &
        (df["節次"].notna())
    ].copy()
    print(f"✅ 有效課程筆數：{len(valid_df)}")

    return valid_df

# ===== 首頁路由 =====
@app.route("/")
def index():
    user = session.get("user")
    if not user:
        return redirect("/login")

    df = load_schedule()
    class_names = sorted(df["班級名稱"].dropna().unique())
    teacher_names = sorted(df["教師名稱"].dropna().unique())
    room_names = sorted(df["教室名稱"].dropna().unique())

    date_map = df.groupby("星期")["日期"].first().to_dict()
    update_time = datetime.now().strftime("%m月%d日").lstrip("0").replace(" 0", " ")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%m月%d日").lstrip("0").replace(" 0", " ")

    return render_template(
        "index.html",
        class_names=class_names,
        teacher_names=teacher_names,
        room_names=room_names,
        weekday_dates=date_map,
        update_time=update_time,
        email=user["email"],
        yesterday=yesterday,
    )

# ===== 查詢對象 options API =====
@app.route("/schedule/<mode>/__options__")
def schedule_options(mode):
    df = load_schedule()
    col_map = {"class": "班級名稱", "teacher": "教師名稱", "room": "教室名稱"}
    if mode not in col_map:
        return jsonify([])
    options = sorted(df[col_map[mode]].dropna().unique().tolist())
    return jsonify(options)

# ===== 查詢課表 API（支援同時段多課程、不重複）=====
@app.route("/schedule/<mode>/<target>")
def schedule(mode, target):
    df = load_schedule()
    col_map = {"class": "班級名稱", "teacher": "教師名稱", "room": "教室名稱"}
    if mode not in col_map:
        return jsonify({"error": "無效的查詢模式"}), 400

    col = col_map[mode]
    sub_df = df[df[col] == target].copy()
    # 防止重複
    sub_df = sub_df.drop_duplicates(subset=["班級名稱", "科目名稱", "教師名稱", "星期", "節次"])

    data = {}
    for _, row in sub_df.iterrows():
        try:
            weekday = int(float(row['星期']))
            period = int(float(row['節次']))
            if not (1 <= weekday <= 7 and 1 <= period <= 8):
                continue
            key = f"{weekday}-{period}"
            entry = {
                "subject": str(row["科目名稱"]).strip(),
                "teacher": str(row["教師名稱"]).strip(),
                "room": str(row["教室名稱"]).strip() if pd.notna(row.get("教室名稱")) else "",
                "class": str(row["班級名稱"]).strip(),
            }
            if key not in data:
                data[key] = []
            data[key].append(entry)
        except (ValueError, TypeError):
            continue

    return jsonify(data)

# ===== 可調課 API =====
@app.route("/swap-options", methods=["POST"])
def swap_options():
    df = load_schedule()
    req = request.json

    class_name = req["target"]
    day, period = int(req["weekday"]), int(req["period"])

    src = df[
        (df["班級名稱"] == class_name)
        & (df["星期"] == day)
        & (df["節次"] == period)
    ]
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
            try:
                b_day, b_period = int(float(b["星期"])), int(float(b["節次"]))
            except (ValueError, TypeError):
                continue

            # 只處理 1~7 與第8節的互換限制
            if (period == 8 and b_period <= 7) or (b_period == 8 and period <= 7):
                continue
            # 同一天同一節
            if (b_day == day and b_period == period):
                continue
            # 不可互調的科目
            if b["科目名稱"] in ["團體活動時間", "本土語文", "多元選修", "彈性學習時間"]:
                continue

            a_empty = df[
                (df["教師名稱"] == src["教師名稱"])
                & (df["星期"] == b_day)
                & (df["節次"] == b_period)
            ].empty
            b_empty = df[
                (df["教師名稱"] == b_teacher)
                & (df["星期"] == day)
                & (df["節次"] == period)
            ].empty
            if not (a_empty and b_empty):
                continue

            reasons = []
            # 同師同班一天 3 節以上
            if (
                df[
                    (df["教師名稱"] == b_teacher)
                    & (df["班級名稱"] == class_name)
                    & (df["星期"] == day)
                ].shape[0]
                >= 2
            ):
                reasons.append("同一老師一天對同班授課三次以上")
            if (
                df[
                    (df["教師名稱"] == src["教師名稱"])
                    & (df["班級名稱"] == class_name)
                    & (df["星期"] == b_day)
                ].shape[0]
                >= 2
            ):
                reasons.append("同一老師一天對同班授課三次以上")
            # 四五節連堂限制
            if {period, b_period} == {4, 5}:
                same_day = df[
                    (df["教師名稱"] == src["教師名稱"])
                    & (df["星期"] == day)
                    & (df["班級名稱"] == class_name)
                ]
                if same_day.shape[0] > 1:
                    reasons.append("教師同天四五節連堂")

            options.append(
                {
                    "day": b_day,
                    "period": b_period,
                    "original_period": period,
                    "recommended": not reasons,
                    "reason": "、".join(reasons),
                    "swap_teacher": b_teacher,
                    "swap_subject": b["科目名稱"],
                }
            )
    return jsonify(options)

# ===== 手動同步 Google Sheet =====
@app.route("/sync", methods=["GET"])
def manual_sync():
    try:
        from sync_drive_to_gsheet import (
            upload_to_google_sheet,
            download_latest_schedule,
        )

        upload_to_google_sheet(download_latest_schedule())
        load_schedule.cache_clear()

        return "✅ 課表同步成功", 200
    except Exception as e:
        return f"❌ 同步失敗：{str(e)}", 500

# ===== 主程式 =====
if __name__ == "__main__":
    app.run(debug=True)
