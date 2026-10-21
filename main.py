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
    """精確解析 HiStock 的八大行庫專屬歷史表格"""
    url = "https://histock.tw/stock/three.aspx?m=eight"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    daily_records = {}
    try:
        res = requests.get(url, headers=headers, timeout=12)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            tables = soup.find_all("table")
            # 遍歷所有表格，尋找表頭包含「八大」或「公股」或包含買超賣超合計的表格
            target_table = None
            for tbl in tables:
                text = tbl.get_text()
                if "八大" in text or "官股" in text or "行庫" in text:
                    target_table = tbl
                    break
            
            # 若無特徵文字，第二個表格通常是公股日明細
            if not target_table and len(tables) >= 2:
                target_table = tables[1]
            elif not target_table and len(tables) > 0:
                target_table = tables[0]

            if target_table:
                for r in target_table.find_all("tr")[1:]:
                    cols = [td.get_text(strip=True) for td in r.find_all(["td", "th"])]
                    if len(cols) >= 3:
                        d_text = cols[0].strip()
                        parts = d_text.split('/')
                        if len(parts) >= 2:
                            try:
                                m_d = f"{int(parts[-2]):02d}/{int(parts[-1]):02d}"
                                net_val = cols[-1].strip()
                                # 加上正負符號與單位格式化
                                if not net_val.startswith(("+", "-")) and float(net_val.replace(",", "")) > 0:
                                    net_val = f"+{net_val}"
                                daily_records[m_d] = f"{net_val}億"
                            except Exception:
                                continue
    except Exception as e:
        print(f"官股金額抓取異常: {e}")
    return daily_records

def get_tse_market_rankings():
    """證交所官方 API：上市全市場三大法人合計買賣超 Top 10"""
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
    """櫃買中心官方 API：上櫃全市場三大法人合計買賣超 Top 10"""
    today = datetime.date.today()
    roc_year = today.year - 1911
    date_str = f"{roc_year}/{today.strftime('%m/%d')}"
    
    # 支援新舊櫃買中心端點
    endpoints = [
        f"https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&d={date_str}&se=EW&t=D&response=json",
        f"https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_three_major_institutions_turnover?date={today.strftime('%Y%m%d')}"
    ]
    
    buy_top10, sell_top10 = [], []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.tpex.org.tw/"
    }

    stock_data = []
    for url in endpoints:
        try:
            res = requests.get(url, headers=headers, timeout=10).json()
            # 判斷是舊版結構或是新版結構
            rows = res.get("aaData") or res.get("data") or (res if isinstance(res, list) else [])
            if rows:
                for r in rows:
                    if isinstance(r, dict):
                        # OpenAPI 字典結構
                        name = r.get("SecuritiesCompany", "").strip() or r.get("CompanyName", "").strip()
                        net_val = r.get("TotalDifferenceSheets", "").replace(",", "") or r.get("Difference", "").replace(",", "")
                        if name and net_val:
                            try:
                                sheets = int(float(net_val))
                                stock_data.append((name, sheets))
                            except ValueError:
                                continue
                    elif isinstance(r, list) and len(r) >= 11:
                        # 舊版二維陣列結構
                        name = r[1].strip()
                        # 尋找合計差額欄位
                        net_val = r[10].replace(",", "").strip() if len(r) > 10 else r[-1].replace(",", "").strip()
                        try:
                            sheets = round(int(net_val) / 1000)
                            stock_data.append((name, sheets))
                        except ValueError:
                            continue
                if stock_data:
                    break
        except Exception as e:
            print(f"嘗試抓取上櫃資料異常: {e}")
            continue

    if stock_data:
        stock_data.sort(key=lambda x: x[1], reverse=True)
        for idx, (name, sheets) in enumerate(stock_data[:10], 1):
            buy_top10.append(f"{idx:>2}. {name} ({sheets:+,}張)")
        for idx, (name, sheets) in enumerate(stock_data[-10:][::-1], 1):
            sell_top10.append(f"{idx:>2}. {name} ({sheets:+,}張)")

    return buy_top10, sell_top10

def build_message():
    today_str = datetime.date.today().strftime("%Y/%m/%d")
    twse_map = get_twse_turnover_5days()
    gov_dict = get_eight_banks_5days()
    tse_buy, tse_sell = get_tse_market_rankings()
    otc_buy, otc_sell = get_otc_market_rankings()

    msg = f"📊 【台股盤後籌碼總覽】\n"
    msg += f"📅 日期：{today_str}\n\n"

    # --- 近5日大盤量與官股總額 ---
    msg += "🏛️ 【近5日大盤量與官股動向】\n"
    msg += "─" * 22 + "\n"
    for d, turnover in twse_map.items():
        gov_net = gov_dict.get(d, "已結算")
        msg += f"{d}  大盤:{turnover:>7.1f}億 | 官股:{gov_net}\n"
    msg += "─" * 22 + "\n\n"

    # --- 上市三大法人排行 ---
    msg += "📈 【上市三大法人買超 Top 10】\n"
    msg += ("\n".join(tse_buy) if tse_buy else "資料未取得或結算中") + "\n\n"

    msg += "📉 【上市三大法人賣超 Top 10】\n"
    msg += ("\n".join(tse_sell) if tse_sell else "資料未取得或結算中") + "\n\n"

    # --- 上櫃三大法人排行 ---
    msg += "📈 【上櫃三大法人買超 Top 10】\n"
    msg += ("\n".join(otc_buy) if otc_buy else "資料未取得或結算中") + "\n\n"

    msg += "📉 【上櫃三大法人賣超 Top 10】\n"
    msg += ("\n".join(otc_sell) if otc_sell else "資料未取得或結算中")

    return msg

if __name__ == "__main__":
    content = build_message()
    send_line_message(content)
