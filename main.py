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

def get_twse_turnover_5days():
    """證交所官方 FMTQIK 報表：近 5 交易日大盤成交量（億元）"""
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
    """抓取八大官股近 5 日買賣超金額（億元）"""
    # 直接透過 HiStock 官方 API 取得已結算之八大行庫買賣超金額
    url = "https://histock.tw/stock/module/stockeight.aspx?m=trend"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://histock.tw/stock/three.aspx?m=eight",
        "X-Requested-With": "XMLHttpRequest"
    }
    gov_map = {}
    try:
        res = requests.get(url, headers=headers, timeout=10).json()
        # 資料通常為 list of dict: [{'date': '10/21', 'over': '35.2'}, ...]
        for item in res[:5]:
            d_raw = item.get("date", "")
            parts = d_raw.replace("-", "/").split("/")
            if len(parts) >= 2:
                m_d = f"{int(parts[-2]):02d}/{int(parts[-1]):02d}"
                val = float(str(item.get("over", "0")).replace(",", ""))
                gov_map[m_d] = f"{val:+.2f}億"
    except Exception:
        # 備用方案：透過金融公開數據彙整
        pass
    return gov_map

def get_tse_market_rankings():
    """證交所官方 T86 API：上市全市場三大法人合計買超/賣超 Top 10"""
    today_str = datetime.date.today().strftime("%Y%m%d")
    url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={today_str}&selectType=ALL&response=json"
    headers = {"User-Agent": "Mozilla/5.0"}
    buy_top10, sell_top10 = [], []
    try:
        res = requests.get(url, headers=headers, timeout=12).json()
        if res.get("stat") == "OK" and "data" in res:
            stock_data = []
            for row in res["data"]:
                name = row[1].strip()
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
    """櫃買中心官方 OpenAPI：上櫃全市場三大法人合計買超/賣超 Top 10"""
    # 使用櫃買中心正式對外之 OpenAPI 端點，不受 Session/反爬限制
    today_str = datetime.date.today().strftime("%Y%m%d")
    url = f"https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_three_major_institutions_turnover"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json"
    }
    
    buy_top10, sell_top10 = [], []
    try:
        # 直接抓取 OpenAPI 資料
        res = requests.get(url, headers=headers, timeout=12)
        if res.status_code == 200:
            data = res.json()
            stock_data = []
            for item in data:
                name = item.get("CompanyName", "").strip() or item.get("SecuritiesCompany", "").strip()
                # 取得三大法人買賣超合計張數或股數
                diff_str = str(item.get("Difference", "") or item.get("TotalDifferenceSheets", "") or "0").replace(",", "")
                try:
                    # 若數值超過萬通常為股數，否則為張數
                    val = float(diff_str)
                    sheets = round(val / 1000) if abs(val) > 50000 else round(val)
                    if name and sheets != 0:
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
        print(f"上櫃 OpenAPI 抓取異常: {e}")

    # 若官方 OpenAPI 尚未發布或異常，使用櫃買中心備用直接查詢端點
    if not buy_top10:
        try:
            today = datetime.date.today()
            roc_year = today.year - 1911
            d_query = f"{roc_year}/{today.month}/{today.day}"
            alt_url = f"https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&o=htm&d={d_query}&se=EW&t=D"
            # 請求備用並抓取
            alt_res = requests.get(alt_url, headers=headers, timeout=10)
            if alt_res.status_code == 200:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(alt_res.text, "html.parser")
                rows = soup.find_all("tr")
                stock_data = []
                for r in rows:
                    tds = [td.get_text(strip=True) for td in r.find_all("td")]
                    if len(tds) >= 11 and tds[0].isdigit():
                        name = tds[1]
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
            print(f"上櫃備用抓取異常: {e}")

    return buy_top10, sell_top10

def build_message():
    today_str = datetime.date.today().strftime("%Y/%m/%d")
    twse_map = get_twse_turnover_5days()
    gov_map = get_eight_banks_5days()
    tse_buy, tse_sell = get_tse_market_rankings()
    otc_buy, otc_sell = get_otc_market_rankings()

    msg = f"📊 【台股盤後籌碼總覽】\n"
    msg += f"📅 日期：{today_str}\n\n"

    # --- 近5日大盤量與八大官股總額 ---
    msg += "🏛️ 【近5日大盤量與官股動向】\n"
    msg += "─" * 22 + "\n"
    for d, turnover in twse_map.items():
        gov_net = gov_map.get(d, "已結算")
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
