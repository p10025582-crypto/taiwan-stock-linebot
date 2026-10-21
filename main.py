import os
import requests
import datetime

# 從 GitHub Secrets 讀取金鑰
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
    print(f"LINE 推播狀態: {resp.status_code}, 回應: {resp.text}")

def get_twse_summary():
    today = datetime.datetime.now()
    today_str = today.strftime("%Y%m%d")
    today_display = today.strftime("%Y/%m/%d")
    
    url = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={today_str}&type=MS&response=json"
    
    try:
        res = requests.get(url, timeout=15).json()
        if res.get("stat") != "OK":
            return f"【台股盤後快訊】\n日期：{today_display}\n今日非交易日，或證交所尚未產出盤後資料。"
        
        # 取得大盤總成交金額
        turnover_table = res.get("tables", [])[0]
        total_trade_val = turnover_table["data"][0][1].replace(",", "")
        total_trade_billion = round(float(total_trade_val) / 100000000, 2)
        
        msg = f"📊 【台股籌碼與盤後通知】\n"
        msg += f"日期：{today_display}\n"
        msg += f"大盤總成交量：{total_trade_billion} 億元\n\n"
        msg += f"💡 官股買賣超速算參考：\n"
        msg += f"公式：大盤總成交量 × 官股買賣超比重(%)\n"
        msg += f"若當日比重為 +0.50%，則約買超：{round(total_trade_billion * 0.005, 2)} 億元。"
        return msg
    except Exception as e:
        return f"資料抓取失敗：{e}"

if __name__ == "__main__":
    content = get_twse_summary()
    send_line_message(content)
