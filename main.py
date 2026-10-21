import os
import requests
import datetime
import time
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
    """證交所官方 FMTQIK 報表：近 5 交易日大盤成交量（億元）"""
    today = datetime.date.today()
    date_str = today.strftime("%Y%m%d")
    url = f"https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?date={date_str}&response=json"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    turnover_map = {}
    twse_dates = []
    try:
        res = requests.get(url, headers=headers, timeout=10).json()
        if res.get("stat") == "OK" and "data" in res:
            recent_rows = res["data"][-5:]
            recent_rows.reverse()
            for row in recent_rows:
                # row[0] 格式為 '115/10/21'
                d_parts = row[0].split('/')
                roc_y = int(d_parts[0])
                month = int(d_parts[1])
                day = int(d_parts[2])
                ad_y = roc_y + 1911
                ad_date_str = f"{ad_y}{month:02d}{day:02d}"
                m_d = f"{month:02d}/{day:02d}"
                turnover_billion = round(float(row[2].replace(",", "")) / 100000000, 1)
                turnover_map[m_d] = turnover_billion
                twse_dates.append((m_d, ad_date_str))
    except Exception as e:
        print(f"抓取大盤成交量異常: {e}")
    return turnover_map, twse_dates

def get_three_institutions_5days(twse_dates):
    """逐日查詢三大法人合計買賣超，並加入間隔與防快取標頭"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache"
    }
    institutions_map = {}
    
    for m_d, ad_date_str in twse_dates:
        # 加上時間戳參數 _ 徹底打碎證交所快取
        timestamp = int(time.time() * 1000)
        url = f"https://www.twse.com.tw/rwd/zh/fund/BFI82U?date={ad_date_str}&response=json&_={timestamp}"
        try:
            res = requests.get(url, headers=headers, timeout=8).json()
            if res.get("stat") == "OK" and "data" in res:
                # 倒數第一筆通常是「合計」
                total_row = res["data"][-1]
                diff_str = total_row[-1].replace(",", "").strip()
                diff_billion = round(float(diff_str) / 100000000, 2)
                institutions_map[m_d] = f"{diff_billion:+.2f}億"
            else:
                institutions_map[m_d] = "結算中"
        except Exception:
            institutions_map[m_d] = "未取得"
        
        # 間隔 0.6 秒避免被證交所防爬機制攔截並返回重複快取
        time.sleep(0.6)

    return institutions_map

def is_etf_or_etn(code, name):
    """排除 ETF 與 ETN（代號 00、01、02 開頭或名稱含特定關鍵字）"""
    code = code.strip()
    name = name.strip()
    if code.startswith(('00', '01', '02')):
        return True
    if any(keyword in name for keyword in ["ETF", "反1", "正2", "債"]):
        return True
    return False

def get_tse_market_rankings():
    """證交所官方 T86 API：上市全市場三大法人合計買超/賣超 Top 10（排除 ETF）"""
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
                
                if is_etf_or_etn(code, name):
                    continue

                try:
                    shares = int(row[18].replace(",", ""))
                    sheets = round(shares / 1000)
                    stock_data.append((name, sheets))
                except (ValueError, IndexError):
                    continue
            
            stock_data.sort(key=lambda x: x[1], reverse=True)
            for idx, (name, sheets) in enumerate(stock_data[:10], 1):
                buy_top10.append(f"{idx:>2}. {name} ({sheets:+,}張)")
            for idx, (name, sheets) in enumerate(stock_data[-10:][::-1], 1):
                sell_top10.append(f"{idx:>2}. {name} ({sheets:+,}張)")
    except Exception as e:
        print(f"上市排行異常: {e}")
    return buy_top10, sell_top10

def get_otc_market_rankings():
    """櫃買中心官方日報：上櫃全市場三大法人合計買超/賣超 Top 10（排除 ETF）"""
    today = datetime.date.today()
    roc_year = today.year - 1911
    d_query = f"{roc_year}/{today.month}/{today.day}"
    alt_url = f"https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&o=htm&d={d_query}&se=EW&t=D"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.tpex.org.tw/"
    }
    buy_top10, sell_top10 = [], []
    try:
        alt_res = requests.get(alt_url, headers=headers, timeout=12)
        if alt_res.status_code == 200:
            soup = BeautifulSoup(alt_res.text, "html.parser")
            rows = soup.find_all("tr")
            stock_data = []
            for r in rows:
                tds = [td.get_text(strip=True) for td in r.find_all("td")]
                if len(tds) >= 11 and tds[0].isdigit():
                    code = tds[0].strip()
                    name = tds[1].strip()

                    if is_etf_or_etn(code, name):
                        continue

                    try:
                        sheets = round(int(tds[-1].replace(",", "")) / 1000)
                        stock_data.append((name, sheets))
                    except ValueError:
                        continue
            if stock_data:
                stock_data.sort(key=lambda x: x[1], reverse=True)
                for idx, (name, sheets) in enumerate(stock_data[:10], 1):
                    buy_top10.append(f"{idx:>2}. {name} ({sheets:+,}張)")
                for idx, (name, sheets) in enumerate(stock_data[-10:][::-1], 1):
                    sell_top10.append(f"{idx:>2}. {name} ({sheets:+,}張)")
    except Exception as e:
        print(f"上櫃抓取異常: {e}")
    return buy_top10, sell_top10

def build_message():
    today_str = datetime.date.today().strftime("%Y/%m/%d")
    twse_map, twse_dates = get_twse_turnover_5days()
    inst_map = get_three_institutions_5days(twse_dates)
    tse_buy, tse_sell = get_tse_market_rankings()
    otc_buy, otc_sell = get_otc_market_rankings()

    msg = f"📊 【台股盤後籌碼總覽】\n"
    msg += f"📅 日期：{today_str}\n\n"

    # --- 近5日大盤量與三大法人買賣超 ---
    msg += "🏛️ 【近5日大盤量與三大法人動向】\n"
    msg += "─" * 24 + "\n"
    for d, turnover in twse_map.items():
        inst_net = inst_map.get(d, "未取得")
        msg += f"{d}  大盤:{turnover:>7.1f}億 | 法人:{inst_net}\n"
    msg += "─" * 24 + "\n\n"

    # --- 上市全市場三大法人排行（排除 ETF） ---
    msg += "📈 【上市三大法人買超 Top 10 (個股)】\n"
    msg += ("\n".join(tse_buy) if tse_buy else "資料未取得或結算中") + "\n\n"

    msg += "📉 【上市三大法人賣超 Top 10 (個股)】\n"
    msg += ("\n".join(tse_sell) if tse_sell else "資料未取得或結算中") + "\n\n"

    # --- 上櫃全市場三大法人排行（排除 ETF） ---
    msg += "📈 【上櫃三大法人買超 Top 10 (個股)】\n"
    msg += ("\n".join(otc_buy) if otc_buy else "資料未取得或結算中") + "\n\n"

    msg += "📉 【上櫃三大法人賣超 Top 10 (個股)】\n"
    msg += ("\n".join(otc_sell) if otc_sell else "資料未取得或結算中") + "\n\n"

    # --- 官股查詢直達連結 ---
    msg += "🏛️ 【八大官股即時動向查詢】\n"
    msg += "https://www.wantgoo.com/stock/public-bank/trend?market=-1&typeShow=amount"

    return msg

if __name__ == "__main__":
    content = build_message()
    send_line_message(content)
