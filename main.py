import os
import requests
import datetime
from bs4 import BeautifulSoup

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

def get_twse_turnover_5days():
    """從證交所官方抓取近 5 個交易日的大盤成交金額（億元）"""
    today = datetime.date.today()
    date_str = today.strftime("%Y%m%d")
    url = f"https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?date={date_str}&response=json"
    headers = {"User-Agent": "Mozilla/5.0"}
    turnover_map = {}
    try:
        res = requests.get(url, headers=headers, timeout=10).json()
        if res.get("stat") == "OK" and "data" in res:
            recent_rows = res["data"][-5:]
            recent_rows.reverse()
            for row in recent_rows:
                d_parts = row[0].split('/')
                month_day = f"{int(d_parts[1]):02d}/{int(d_parts[2]):02d}"
                turnover_billion = round(float(row[2].replace(",", "")) / 100000000, 1)
                turnover_map[month_day] = turnover_billion
    except Exception as e:
        print(f"抓取大盤成交量異常: {e}")
    return turnover_map

def get_eight_banks_5days():
    """抓取玩股網八大官股近 5 日買賣超金額"""
    url = "https://www.wantgoo.com/stock/public-bank/trend"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.wantgoo.com/"
    }
    daily_records = []
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            table = soup.find("table")
            if table:
                rows = table.find_all("tr")[1:6]
                for r in rows:
                    cols = [td.get_text(strip=True) for td in r.find_all(["td", "th"])]
                    if len(cols) >= 3:
                        d_str = cols[0].replace("-", "/")
                        parts = d_str.split("/")
                        m_d = f"{int(parts[1]):02d}/{int(parts[2]):02d}" if len(parts) >= 3 else cols[0]
                        daily_records.append((m_d, cols[-1]))
    except Exception as e:
        print(f"官股金額抓取異常: {e}")
    return daily_records

def get_tse_market_rankings():
    """從證交所官方 API 抓取全市場上市三大法人買超/賣超 Top 10"""
    today_str = datetime.date.today().strftime("%Y%m%d")
    url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={today_str}&selectType=ALL&response=json"
    headers = {"User-Agent": "Mozilla/5.0"}
    buy_top10, sell_top10 = [], []
    try:
        res = requests.get(url, headers=headers, timeout=12).json()
        if res.get("stat") == "OK" and "data" in res:
            stock_data = []
            for row in res["data"]:
                code = row[0].strip()
                name = row[1].strip()
                # row[18] 是三大法人合計買賣超股數
                try:
                    shares = int(row[18].replace(",", ""))
                    sheets = round(shares / 1000)
                    stock_data.append((name, sheets))
                except (ValueError, IndexError):
                    continue
            
            # 依張數排序
            stock_data.sort(key=lambda x: x[1], reverse=True)
            for name, sheets in stock_data[:10]:
                buy_top10.append(f"{name} ({sheets:+,}張)")
            for name, sheets in stock_data[-10:][::-1]:
                sell_top10.append(f"{name} ({sheets:+,}張)")
    except Exception as e:
        print(f"上市全市場排行抓取異常: {e}")
    return buy_top10, sell_top10

def get_otc_market_rankings():
    """從櫃買中心官方 API 抓取全市場上櫃三大法人買超/賣超 Top 10"""
    today = datetime.date.today()
    roc_year = today.year - 1911
    date_str = f"{roc_year}/{today.strftime('%m/%d')}"
    url = f"https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&d={date_str}&se=EW&t=D&response=json"
    headers = {"User-Agent": "Mozilla/5.0"}
    buy_top10, sell_top10 = [], []
    try:
        res = requests.get(url, headers=headers, timeout=12).json()
        if "aaData" in res and res["aaData"]:
            stock_data = []
            for row in res["aaData"]:
                code = row[0].strip()
                name = row[1].strip()
                # row[10] 或 row[13] 是上櫃三大法人合計買賣超股數
                try:
                    shares = int(row[10].replace(",", ""))
                    sheets = round(shares / 1000)
                    stock_data.append((name, sheets))
                except (ValueError, IndexError):
                    continue
            
            stock_data.sort(key=lambda x: x[1], reverse=True)
            for name, sheets in stock_data[:10]:
                buy_top10.append(f"{name} ({sheets:+,}張)")
            for name, sheets in stock_data[-10:][::-1]:
                sell_top10.append(f"{name} ({sheets:+,}張)")
    except Exception as e:
        print(f"上櫃全市場排行抓取異常: {e}")
    return buy_top10, sell_top10

def build_message():
    today_str = datetime.date.today().strftime("%Y/%m/%d")
    twse_map = get_twse_turnover_5days()
    daily_gov = get_eight_banks_5days()
    tse_buy, tse_sell = get_tse_market_rankings()
    otc_buy, otc_sell = get_otc_market_rankings()

    msg = f"📊 【台股盤後籌碼總覽】\n"
    msg += f"📅 日期：{today_str}\n\n"

    # --- 近5日大盤量與官股總額 ---
    msg += "🏛️ 【近5日大盤量與官股動向】\n"
    msg += "─" * 22 + "\n"
    for d, turnover in twse_map.items():
        gov_net = "已結算"
        for g_d, g_val in daily_gov:
            if d in g_d:
                gov_net = g_val
                break
        msg += f"{d}  大盤:{turnover:>7.1f}億 | 官股:{gov_net}\n"
    msg += "─" * 22 + "\n\n"

    # --- 上市全市場三大法人排行 ---
    msg += "📈 【上市三大法人買超 Top 10】\n"
    msg += ("\n".join(tse_buy) if tse_buy else "資料未取得或結算中") + "\n\n"

    msg += "📉 【上市三大法人賣超 Top 10】\n"
    msg += ("\n".join(tse_sell) if tse_sell else "資料未取得或結算中") + "\n\n"

    # --- 上櫃全市場三大法人排行 ---
    msg += "📈 【上櫃三大法人買超 Top 10】\n"
    msg += ("\n".join(otc_buy) if otc_buy else "資料未取得或結算中") + "\n\n"

    msg += "📉 【上櫃三大法人賣超 Top 10】\n"
    msg += ("\n".join(otc_sell) if otc_sell else "資料未取得或結算中")

    return msg

if __name__ == "__main__":
    content = build_message()
    send_line_message(content)
