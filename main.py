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

def get_twse_market_data_and_bias():
    """
    抓取一般撮合之大盤成交金額（排除鉅額等特定交易）以及近 5 日收盤 5MA 乖離率
    """
    today = datetime.date.today()
    date_str = today.strftime("%Y%m%d")
    headers = {"User-Agent": "Mozilla/5.0"}
    turnover_map = {}
    twse_days_list = []
    bias_map = {}

    # 1. 抓取當日純成交金額（不含鉅額）
    url_mi = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={date_str}&response=json"
    today_pure_billion = None
    try:
        res_mi = requests.get(url_mi, headers=headers, timeout=10).json()
        if res_mi.get("stat") == "OK":
            tables = res_mi.get("tables", [])
            for tbl in tables:
                if "市場成交資訊" in tbl.get("title", ""):
                    for row in tbl.get("data", []):
                        if "1.成交金額" in row[0]:
                            val_str = row[1].replace(",", "").strip()
                            today_pure_billion = round(float(val_str) / 100000000, 1)
                            break
    except Exception:
        pass

    # 2. 抓取整月 FMTQIK 歷史報表（取得成交金額與收盤指數計算 5MA）
    url = f"https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?date={date_str}&response=json"
    try:
        res = requests.get(url, headers=headers, timeout=10).json()
        if res.get("stat") == "OK" and "data" in res:
            all_rows = res["data"]
            # 近 5 日成交量
            recent_5_rows = all_rows[-5:][::-1]
            for idx, row in enumerate(recent_5_rows):
                d_parts = row[0].split('/')
                m_d = f"{int(d_parts[1]):02d}/{int(d_parts[2]):02d}"
                if idx == 0 and today_pure_billion:
                    turnover_billion = today_pure_billion
                else:
                    turnover_billion = round(float(row[2].replace(",", "")) / 100000000, 1)
                
                turnover_map[m_d] = turnover_billion
                twse_days_list.append(m_d)

            # 計算近 5 日每一天的 5MA 乖離率（需回溯至少近 9 筆收盤點數）
            if len(all_rows) >= 5:
                closes = [float(r[4].replace(",", "").strip()) for r in all_rows]
                dates = [r[0] for r in all_rows]
                
                calc_len = min(5, len(all_rows) - 4)
                for i in range(len(all_rows) - calc_len, len(all_rows)):
                    window = closes[i-4 : i+1]
                    ma5 = sum(window) / 5.0
                    close = closes[i]
                    bias = ((close - ma5) / ma5) * 100.0
                    
                    d_parts = dates[i].split('/')
                    m_d = f"{int(d_parts[1]):02d}/{int(d_parts[2]):02d}"
                    bias_map[m_d] = f"{bias:+.2f}%"
    except Exception as e:
        print(f"大盤資料抓取異常: {e}")

    return turnover_map, twse_days_list, bias_map

def get_three_institutions_histock_5days():
    """從 HiStock 歷史表抓取近 5 日三大法人買賣超合計（億元）"""
    url = "https://histock.tw/stock/three.aspx?m=three"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }
    inst_map = {}
    try:
        res = requests.get(url, headers=headers, timeout=12)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            target_table = None
            tables = soup.find_all("table")
            for tbl in tables:
                if "三大法人" in tbl.get_text():
                    target_table = tbl
                    break
            if not target_table and tables:
                target_table = tables[0]

            if target_table:
                for row in target_table.find_all("tr")[1:]:
                    tds = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                    if len(tds) >= 7:
                        date_parts = tds[0].replace("-", "/").split("/")
                        if len(date_parts) >= 2:
                            m_d = f"{int(date_parts[-2]):02d}/{int(date_parts[-1]):02d}"
                            net_str = tds[-1].replace(",", "").strip()
                            try:
                                net_float = float(net_str)
                                inst_map[m_d] = f"{net_float:+.2f}億"
                            except ValueError:
                                inst_map[m_d] = f"{net_str}億"
                        if len(inst_map) >= 5:
                            break
    except Exception as e:
        print(f"HiStock 三大法人抓取異常: {e}")
    return inst_map

def is_pure_stock(code, name):
    """
    只保留純普通個股：
    1. 台股普通股票代號必為 4 碼純數字（徹底排除 6 碼權證、特別股等）
    2. 排除 00 開頭 (ETF)、01 開頭 (REITs)、02 開頭 (ETN)
    3. 排除名稱中帶有權證特徵（購、售、牛、熊）或衍生品字樣
    """
    code = code.strip()
    name = name.strip()
    
    if len(code) != 4 or not code.isdigit():
        return False
        
    if code.startswith(('00', '01', '02')):
        return False
        
    exclude_keywords = ["ETF", "反1", "正2", "債", "購", "售", "牛", "熊"]
    if any(k in name for k in exclude_keywords):
        return False
        
    return True

def get_tse_market_rankings():
    """證交所官方 T86 API：上市全市場三大法人合計買超/賣超 Top 10（排除非普通股）"""
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
                
                if not is_pure_stock(code, name):
                    continue

                try:
                    shares = int(row[18].replace(",", ""))
                    sheets = round(shares / 1000)
                    stock_data.append((name, sheets))
                except (ValueError, IndexError):
                    continue
            
            stock_data.sort(key=lambda x: x[1], reverse=True)
            for idx, (name, sheets) in enumerate(stock_data[:10], 1):
                buy_top10.append(f"{idx}. {name} ({sheets:+,}張)")
            for idx, (name, sheets) in enumerate(stock_data[-10:][::-1], 1):
                sell_top10.append(f"{idx}. {name} ({sheets:+,}張)")
    except Exception as e:
        print(f"上市排行異常: {e}")
    return buy_top10, sell_top10

def get_otc_market_rankings():
    """櫃買中心官方日報：上櫃全市場三大法人合計買超/賣超 Top 10（排除非普通股）"""
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

                    if not is_pure_stock(code, name):
                        continue

                    try:
                        sheets = round(int(tds[-1].replace(",", "")) / 1000)
                        stock_data.append((name, sheets))
                    except ValueError:
                        continue
            if stock_data:
                stock_data.sort(key=lambda x: x[1], reverse=True)
                for idx, (name, sheets) in enumerate(stock_data[:10], 1):
                    buy_top10.append(f"{idx}. {name} ({sheets:+,}張)")
                for idx, (name, sheets) in enumerate(stock_data[-10:][::-1], 1):
                    sell_top10.append(f"{idx}. {name} ({sheets:+,}張)")
    except Exception as e:
        print(f"上櫃抓取異常: {e}")
    return buy_top10, sell_top10

def build_message():
    today_str = datetime.date.today().strftime("%Y/%m/%d")
    twse_map, twse_days_list, bias_map = get_twse_market_data_and_bias()
    inst_map = get_three_institutions_histock_5days()
    tse_buy, tse_sell = get_tse_market_rankings()
    otc_buy, otc_sell = get_otc_market_rankings()

    msg = f"📊【台股盤後籌碼總覽】\n"
    msg += f"📅 日期：{today_str}\n\n"

    # --- 近5日大盤量與三大法人買賣超 ---
    msg += "🏛️【近5日大盤量與三大法人動向】\n"
    msg += "─" * 22 + "\n"
    for d in twse_days_list:
        turnover = twse_map.get(d, 0.0)
        inst_net = inst_map.get(d, "已結算")
        msg += f"{d} 大盤:{turnover}億 | 法人:{inst_net}\n"
    msg += "─" * 22 + "\n\n"

    # --- 近5日大盤5MA乖離率 ---
    msg += "📐【近5日大盤5MA乖離率】\n"
    msg += "─" * 22 + "\n"
    for d in twse_days_list:
        bias_val = bias_map.get(d, "計算中")
        msg += f"{d} 乖離率:{bias_val}\n"
    msg += "─" * 22 + "\n\n"

    # --- 上市三大法人排行 ---
    msg += "📈【上市三大法人買超 Top 10】\n"
    msg += ("\n".join(tse_buy) if tse_buy else "資料未取得或結算中") + "\n\n"

    msg += "📉【上市三大法人賣超 Top 10】\n"
    msg += ("\n".join(tse_sell) if tse_sell else "資料未取得或結算中") + "\n\n"

    # --- 上櫃三大法人排行 ---
    msg += "📈【上櫃三大法人買超 Top 10】\n"
    msg += ("\n".join(otc_buy) if otc_buy else "資料未取得或結算中") + "\n\n"

    msg += "📉【上櫃三大法人賣超 Top 10】\n"
    msg += ("\n".join(otc_sell) if otc_sell else "資料未取得或結算中") + "\n\n"

    # --- 官股查詢直達連結 ---
    msg += "🏛️【八大官股即時動向查詢】\n"
    msg += "https://www.wantgoo.com/stock/public-bank/trend?market=-1&typeShow=amount"

    return msg

if __name__ == "__main__":
    content = build_message()
    send_line_message(content)
