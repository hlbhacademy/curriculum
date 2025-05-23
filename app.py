from flask import Flask, render_template
import pandas as pd
import os
import json
from collections import defaultdict

# ✅ 自動轉換 .xls → .xlsx
def convert_xls_to_xlsx(xls_path, xlsx_path):
    import win32com.client
    excel = win32com.client.Dispatch("Excel.Application")
    excel.Visible = False
    wb = excel.Workbooks.Open(xls_path)
    wb.SaveAs(xlsx_path, FileFormat=51)  # 51 = .xlsx
    wb.Close()
    excel.Quit()
    print(f"✅ Excel 已將 {xls_path} 轉為 {xlsx_path}")

# ✅ Flask 初始化
app = Flask(__name__)

# ✅ 原始課表檔案位置
xls_path = r'C:\查詢最新課表網頁\v_claspv15.xls'
xlsx_path = xls_path.replace('.xls', '.xlsx')

# ✅ 若不存在 .xlsx 就轉檔
if not os.path.exists(xlsx_path):
    convert_xls_to_xlsx(xls_path, xlsx_path)

# ✅ 讀取轉換後的課表
df = pd.read_excel(xlsx_path, engine='openpyxl')

# ✅ 過濾無教師、週六（僅保留週一～週五）
df = df[df['原始教師名稱'].notna() & df['原始星期'].isin([1, 2, 3, 4, 5])]

# ✅ 初始化課表結構
class_schedule = defaultdict(lambda: defaultdict(dict))
teacher_schedule = defaultdict(lambda: defaultdict(dict))
room_schedule = defaultdict(lambda: defaultdict(dict))
teacher_busy = defaultdict(set)

# ✅ 將課程填入三種查詢結構
for _, row in df.iterrows():
    day = int(row["原始星期"])
    period = int(row["原始節次"])
    teacher = row["原始教師名稱"]
    class_name = row["班級名稱"]
    subject = row["原始科目名稱"]
    room = row["原始教室名稱"] if pd.notna(row["原始教室名稱"]) else "未指定教室"

    display_class = f"{subject}<br><span class='text-xs text-gray-700'>{teacher}</span>"
    display_teacher = f"{subject}<br><span class='text-xs text-gray-500'>{class_name}</span>"
    display_room = f"{subject}<br><span class='text-xs text-gray-500'>{class_name}/{teacher}</span>"

    class_schedule[class_name][day][period] = display_class
    teacher_schedule[teacher][day][period] = class_name
    room_schedule[room][day][period] = display_room
    teacher_busy[teacher].add((day, period))

# ✅ 建立教師空堂表（teacher_free）
teacher_free = defaultdict(list)
for teacher in teacher_busy:
    for day in range(1, 6):  # 週一～週五
        for period in range(1, 9):  # 第1～8節
            if (day, period) not in teacher_busy[teacher]:
                teacher_free[teacher].append([day, period])  # ⚠️ 一定要是 list 才能給 JS 用

# ✅ 將資料傳送給 index.html 模板
@app.route('/')
def index():
    return render_template(
        'index.html',
        class_schedule=json.dumps(class_schedule),
        teacher_schedule=json.dumps(teacher_schedule),
        room_schedule=json.dumps(room_schedule),
        teacher_free=json.dumps(teacher_free)
    )

# ✅ 啟動伺服器（區域網路可查詢）
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
