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

# ===== /sync 路由：同步並清除快取 =====
@app.route("/sync", methods=["GET"])
def manual_sync():
    try:
        from sync_drive_to_gsheet import (
            upload_to_google_sheet,
            download_latest_schedule,
        )

        # 1. 把最新 Excel 推上 Google Sheet
        upload_to_google_sheet(download_latest_schedule())

        # 2. 清除 DataFrame 快取
        load_schedule.cache_clear()

        return "✅ 課表同步成功", 200
    except Exception as e:
        return f"❌ 同步失敗：{str(e)}", 500


@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect("/")


# ===== Google Sheets 課表快取讀取 =====
@lru_cache(maxsize=1)
def load_schedule():
    """讀取 Google Sheet，快取 1 份 DataFrame 以加速查詢。"""
    credentials_info = json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    credentials = ServiceAccountCredentials.from_json_keyfile_dict(
        credentials_info, scope
    )
    client = gspread.authorize(credentials)

    worksheet_name = os.environ.get("GOOGLE_SHEET_TAB", "工作表1")
    sheet = client.open_by_key(os.environ["GOOGLE_SHEET_ID"]).worksheet(worksheet_name)

    df = pd.DataFrame(sheet.get_all_records())
    
    # 數據清理和調試
    print(f"原始數據共 {len(df)} 行")
    
    # 移除完全空白的行
    df = df.dropna(how='all')
    print(f"移除空行後剩餘 {len(df)} 行")
    
    # 基本數據清理
    for col in ['班級名稱', '教師名稱', '科目名稱']:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()
            df[col] = df[col].replace('nan', '')
    
    # 只保留有效的課程記錄
    valid_df = df[
        (df["班級名稱"].notna()) & 
        (df["班級名稱"] != "") &
        (df["班級名稱"] != "nan") &
        (df["教師名稱"].notna()) & 
        (df["教師名稱"] != "") &
        (df["教師名稱"] != "nan") &
        (df["星期"].notna()) & 
        (df["節次"].notna())
    ].copy()
    
    print(f"過濾後有效數據 {len(valid_df)} 行")
    
    # 檢查重複記錄
    duplicates = valid_df.groupby(['班級名稱', '教師名稱', '星期', '節次']).size()
    duplicate_records = duplicates[duplicates > 1]
    if not duplicate_records.empty:
        print("發現重複記錄：")
        for idx, count in duplicate_records.items():
            print(f"  {idx}: {count} 筆")
    
    # 移除完全重複的記錄，保留第一筆
    valid_df = valid_df.drop_duplicates(subset=['班級名稱', '教師名稱', '星期', '節次'], keep='first')
    print(f"去重後最終數據 {len(valid_df)} 行")
    
    return valid_df


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
    yesterday = (
        (datetime.now() - timedelta(days=1))
        .strftime("%m月%d日")
        .lstrip("0")
        .replace(" 0", " ")
    )

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


# ===== 課表查詢 API (修復版) =====
@app.route("/schedule/<mode>/<target>")
def schedule(mode, target):
    df = load_schedule()
    col_map = {"class": "班級名稱", "teacher": "教師名稱", "room": "教室名稱"}
    if mode not in col_map:
        return jsonify({"error": "無效的查詢模式"}), 400

    col = col_map[mode]
    sub_df = df[df[col] == target].copy()
    
    # 添加調試信息
    print(f"查詢 {mode}: {target}")
    print(f"找到 {len(sub_df)} 筆記錄")
    print("記錄內容：")
    for _, row in sub_df.iterrows():
        print(f"  星期{row['星期']} 第{row['節次']}節: {row['科目名稱']} - {row['教師名稱']} - {row['班級名稱']}")

    data = {}
    for _, row in sub_df.iterrows():
        try:
            # 確保星期和節次是有效數值
            weekday = int(float(row['星期']))
            period = int(float(row['節次']))
            
            # 驗證星期和節次範圍
            if not (1 <= weekday <= 5 and 1 <= period <= 8):
                print(f"警告：無效的時間 - 星期{weekday} 第{period}節，跳過此記錄")
                continue
                
            key = f"{weekday}-{period}"
            
            # 檢查是否有重複的時段
            if key in data:
                print(f"警告：時段 {key} 有重複課程")
                print(f"  現有: {data[key]}")
                print(f"  新的: {row['科目名稱']} - {row['教師名稱']} - {row['班級名稱']}")
                # 可以選擇跳過或覆蓋，這裡選擇跳過重複項
                continue
            
            data[key] = {
                "subject": str(row["科目名稱"]).strip(),
                "teacher": str(row["教師名稱"]).strip(),
                "room": str(row["教室名稱"]).strip() if pd.notna(row.get("教室名稱")) else "",
                "class": str(row["班級名稱"]).strip(),
            }
        except (ValueError, TypeError) as e:
            print(f"數據轉換錯誤：{e}")
            print(f"問題記錄：星期={row['星期']}, 節次={row['節次']}")
            continue
    
    print(f"最終返回 {len(data)} 個時段的課程")
    return jsonify(data)


# ===== 調試路由，幫助排查問題 =====
@app.route("/debug/<mode>/<target>")
def debug_schedule(mode, target):
    """調試用路由，顯示原始查詢結果"""
    user = session.get("user")
    if not user:
        return "請先登入", 401
        
    df = load_schedule()
    col_map = {"class": "班級名稱", "teacher": "教師名稱", "room": "教室名稱"}
    if mode not in col_map:
        return "無效查詢模式", 400

    col = col_map[mode]
    sub_df = df[df[col] == target]
    
    # 返回詳細的調試信息
    debug_info = {
        "total_records": len(df),
        "matched_records": len(sub_df),
        "query": f"{mode}: {target}",
        "matches": []
    }
    
    for _, row in sub_df.iterrows():
        debug_info["matches"].append({
            "星期": row.get("星期"),
            "節次": row.get("節次"), 
            "科目名稱": row.get("科目名稱"),
            "教師名稱": row.get("教師名稱"),
            "班級名稱": row.get("班級名稱"),
            "教室名稱": row.get("教室名稱", "")
        })
    
    return jsonify(debug_info)


# ===== 可調課建議 API =====
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
                }
            )
    return jsonify(options)

# ===== 主程式 =====
if __name__ == "__main__":
    app.run(debug=True)
