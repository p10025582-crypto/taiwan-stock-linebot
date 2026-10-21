import os
import requests
import datetime

LINE_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_USER_ID = os.environ.get("LINE_USER_ID")

def send_line_message(text):
    if not LINE_ACCESS_TOKEN or not LINE_USER_ID:
        print("未設定 LINE 金鑰或 User ID")
        return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"
    }
    payload = {
        "to": LINE_USER_ID,
        "messages": [{"type": "text", "text": text}]
    }
    resp = requests.post(url, headers=headers, json=payload)
    print(f"LINE 推播狀態: {resp.status_code}")

def get_recent_5_days_data():
    records = []
    current_date = datetime.date.today()
    checked_days = 0
    
    # 往前找最多 15 天，湊齊最近 5 個有開盤交易的資料
    while len(records) < 5 and checked_days < 15:
        date_str = current_date.strftime("%Y%m%d")
        url = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={date_str}&type=MS&response=json"
        
        try:
            res = requests.get(url, timeout=10).json()
            if res.get("stat") == "OK" and res.get("tables"):
                table = res["tables"][0]
                # 取得大盤成交金額 (元)
                total_val = table["data"][0][1].replace(",", "")
                total_billion = round(float(total_val) / 100000000, 1)
                
                date_display = current_date.strftime("%m/%d")
                weekday_tw = ["一", "二", "三", "四", "五", "六", "日"][current_date.weekday()]
                
                records.append({
                    "date": f"{date_display}({weekday_tw})",
                    "turnover": total_billion
                })
        except Exception:
            pass
        
        current_date -= datetime.timedelta(days=1)
        checked_days += 1
        
    return records

def build_message():
    data = get_recent_5_days_data()
    if not data:
        return "【台股盤後籌碼】\n今日非交易日，或證交所盤後資料尚未產出。"
    
    msg = "📊 【近5日大盤成交量與官股速算】\n"
    msg += "─" * 16 + "\n"
    msg += "日期       大盤成交量   比重1%金額\n"
    
    for r in data:
        # 每 1% 對應的買超金額（億元）
        val_1pct = round(r["turnover"] * 0.01, 2)
        msg += f"{r['date']}   {r['turnover']:>7.1f}億   約 {val_1pct:>5.2f}億\n"
    
    msg += "─" * 16 + "\n"
    msg += "💡 換算範例：\n"
    msg += "若今日官股買賣超比重為 +0.52%\n"
    latest_val = data[0]["turnover"]
    calc_val = round(latest_val * 0.0052, 2)
    msg += f"👉 換算買超 ＝ {latest_val}億 × 0.52% ≒ {calc_val} 億元"
    
    return msg

if __name__ == "__main__":
    message = build_message()
    send_line_message(message)
