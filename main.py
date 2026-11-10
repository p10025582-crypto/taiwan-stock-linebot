import os
import sys
import requests
import datetime
from bs4 import BeautifulSoup

LINE_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")

def is_trading_day():
    """
    向證交所官方 FMTQIK 報表確認今天是否有實際開盤交易：
    若今日為國定假日、週末或颱風假，證交所最新一筆成交紀錄不會是今天，回傳 False。
    """
    today = datetime.date.today()
    date_str = today.strftime("%Y%m%d")
    roc_year = today.year - 1911
    today_roc_str = f"{roc_year}/{today.month:02d}/{today.day:02d}"

    url = f"https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?date={date_str}&response=json"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=10).json()
        if res.get("stat") == "OK" and "data" in res and len(res["data"]) > 0:
            last_record_date = str(res["data"][-1][0]).strip()
            if last_record_date == today_roc_str:
                return True
    except Exception as e:
        print(f"判斷開市狀態異常: {e}")
        return True
    return False

def send_line_message(text):
    if not LINE_ACCESS_TOKEN:
        print("未設定 LINE 金鑰 (LINE_CHANNEL_ACCESS_TOKEN)")
        return
    url = "https://api.line.me/v2/bot/message/broadcast"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"
    }
    payload = {
        "messages": [{"type": "text", "text": text}]
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        print(f"LINE 廣播推播狀態: {resp.status_code}")
    except Exception as e:
        print(f"LINE 發送異常: {e}")

def get_twse_market_data_and_bias():
    """
    抓取大盤成交量與近 5 日 5MA 乖離率。
    具備「跨月份自動拼接」功能：當月資料不足 8 筆時，自動向前抓取上月補齊。
    """
    today = datetime.date.today()
    date_str = today.strftime("%Y%m%d")
    headers = {"User-Agent": "Mozilla/5.0"}
    turnover_map = {}
    twse_days_list = []
    bias_map = {}

    today_pure_billion = None
    try:
        url_mi = f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={date_str}&response=json"
        res_mi = requests.get(url_mi, headers=headers, timeout=10).json()
        if res_mi.get("stat") == "OK":
            tables = res_mi.get("tables", [])
            for tbl in tables:
                if "市場成交資訊" in tbl.get("title", ""):
                    for row in tbl.get("data", []):
                        if "1.成交金額" in str(row[0]):
                            val_str = str(row[1]).replace(",", "").strip()
                            today_pure_billion = round(float(val_str) / 100000000, 1)
                            break
    except Exception as e:
        print(f"MI_INDEX 成交量抓取異常: {e}")

    all_rows = []
    try:
        url_curr = f"https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?date={date_str}&response=json"
        res_curr = requests.get(url_curr, headers=headers, timeout=10).json()
        if res_curr.get("stat") == "OK" and "data" in res_curr:
            all_rows = res_curr["data"]
    except Exception as e:
        print(f"當月 FMTQIK 抓取異常: {e}")

    if len(all_rows) < 8:
        first_of_this_month = today.replace(day=1)
        last_day_prev_month = first_of_this_month - datetime.timedelta(days=1)
        prev_date_str = last_day_prev_month.strftime("%Y%m%d")
        try:
            url_prev = f"https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?date={prev_date_str}&response=json"
            res_prev = requests.get(url_prev, headers=headers, timeout=10).json()
            if res_prev.get("stat") == "OK" and "data" in res_prev:
                all_rows = res_prev["data"] + all_rows
        except Exception as e:
            print(f"上月 FMTQIK 補齊異常: {e}")

    try:
        if len(all_rows) >= 5:
            recent_5_rows = all_rows[-5:][::-1]
            for idx, row in enumerate(recent_5_rows):
                d_parts = str(row[0]).split('/')
                m_d = f"{int(d_parts[1]):02d}/{int(d_parts[2]):02d}"
                if idx == 0 and today_pure_billion:
                    turnover_billion = today_pure_billion
                else:
                    turnover_billion = round(float(str(row[2]).replace(",", "")) / 100000000, 1)
                
                turnover_map[m_d] = turnover_billion
                twse_days_list.append(m_d)

            closes = [float(str(r[4]).replace(",", "").strip()) for r in all_rows]
            dates = [str(r[0]) for r in all_rows]
            
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
        print(f"大盤資料與乖離率解析異常: {e}")

    return turnover_map, twse_days_list, bias_map

def get_taifex_fx_rate_5days():
    """
    抓取近 5 日新台幣/美元匯率及漲跌（三層備援機制）：
    1. 第一通道：期交所官方 OpenAPI
    2. 第二通道：中央銀行官方每日收盤匯率網頁 (cbc.gov.tw)
    3. 第三通道：Yahoo Finance 即時收盤 JSON 端點
    """
    today = datetime.date.today()
    today_md = f"{today.month:02d}/{today.day:02d}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    fx_map = {}
    valid_list = []

    # 第一通道：期交所官方 OpenAPI
    try:
        url = "https://openapi.taifex.com.tw/v1/DailyForeignExchangeRates"
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json()
            for item in data:
                date_str = str(item.get("Date", "")).strip()
                usd_ntd = str(item.get("USD/NTD", "")).strip()
                if len(date_str) == 8 and usd_ntd:
                    try:
                        rate_val = float(usd_ntd)
                        m_d = f"{int(date_str[4:6]):02d}/{int(date_str[6:8]):02d}"
                        valid_list.append((m_d, rate_val))
                    except ValueError:
                        continue
    except Exception as e:
        print(f"第一通道（期交所外匯 API）異常: {e}")

    # 第二通道：中央銀行官方每日收盤匯率網頁
    has_today = any(d == today_md for d, _ in valid_list)
    if not has_today:
        try:
            url_cbc = "https://www.cbc.gov.tw/tw/lp-645-1.html"
            res_cbc = requests.get(url_cbc, headers=headers, timeout=10)
            if res_cbc.status_code == 200:
                soup = BeautifulSoup(res_cbc.text, "html.parser")
                roc_year = today.year - 1911
                today_roc_slash = f"{roc_year}/{today.month:02d}/{today.day:02d}"
                today_roc_dash = f"{roc_year}-{today.month:02d}-{today.day:02d}"
                today_ad_slash = f"{today.year}/{today.month:02d}/{today.day:02d}"

                for tr in soup.find_all("tr"):
                    row_text = tr.get_text(" ", strip=True)
                    if any(dt in row_text for dt in [today_roc_slash, today_roc_dash, today_ad_slash, today_md]):
                        tds = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                        for cell in tds:
                            try:
                                val = float(cell.replace(",", ""))
                                if 25.0 <= val <= 38.0:
                                    mid_rate = round(val, 3)
                                    valid_list.append((today_md, mid_rate))
                                    has_today = True
                                    print(f"第二通道成功：透過中央銀行網頁補齊今日匯率 {mid_rate}")
                                    break
                            except ValueError:
                                continue
                    if has_today:
                        break
        except Exception as e:
            print(f"第二通道（中央銀行網頁）異常: {e}")

    # 第三通道：Yahoo Finance 免阻擋即時收盤 JSON 端點（最終防線）
    if not has_today:
        try:
            url_yf = "https://query1.finance.yahoo.com/v8/finance/chart/USDTWD=X?range=1d&interval=1d"
            res_yf = requests.get(url_yf, headers=headers, timeout=10)
            if res_yf.status_code == 200:
                yf_data = res_yf.json()
                meta = yf_data.get("chart", {}).get("result", [{}])[0].get("meta", {})
                current_rate = meta.get("regularMarketPrice")
                if current_rate and 25.0 <= float(current_rate) <= 38.0:
                    mid_rate = round(float(current_rate), 3)
                    valid_list.append((today_md, mid_rate))
                    print(f"第三通道成功：透過 Yahoo Finance 補齊今日匯率 {mid_rate}")
        except Exception as e:
            print(f"第三通道（Yahoo Finance）異常: {e}")

    # 計算近 5 日各日與前一日之漲跌
    try:
        if len(valid_list) >= 6:
            target_list = valid_list[-6:]
            for i in range(1, len(target_list)):
                curr_d, curr_r = target_list[i]
                prev_d, prev_r = target_list[i - 1]
                diff = round(curr_r - prev_r, 4)
                
                # 數值下降為新台幣升值(🟢)，上升為貶值(🔴)
                if diff < -0.0001:
                    chg_str = f"(升{abs(diff):.3f}) 🟢"
                elif diff > 0.0001:
                    chg_str = f"(貶{abs(diff):.3f}) 🔴"
                else:
                    chg_str = "(平盤) ⚪"
                
                fx_map[curr_d] = f"匯率:{curr_r:.3f} {chg_str}"
    except Exception as e:
        print(f"匯率漲跌計算異常: {e}")

    return fx_map

def get_histock_three_and_futures_5days():
    url = "https://histock.tw/stock/three.aspx?m=three"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }
    inst_map = {}
    futures_map = {}
    try:
        res = requests.get(url, headers=headers, timeout=12)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            tables = soup.find_all("table")
            
            # 1. 三大法人買賣超
            target_table_inst = None
            for tbl in tables:
                if "三大法人" in tbl.get_text():
                    target_table_inst = tbl
                    break
            if not target_table_inst and tables:
                target_table_inst = tables[0]

            if target_table_inst:
                for row in target_table_inst.find_all("tr")[1:]:
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
                        if len(inst_map) >= 10:
                            break

            # 2. 期貨未平倉
            target_table_fut = None
            for tbl in tables:
                if "台股期貨未平倉" in tbl.get_text():
                    target_table_fut = tbl
                    break
            if not target_table_fut and len(tables) > 1:
                target_table_fut = tables[1]

            if target_table_fut:
                for row in target_table_fut.find_all("tr")[1:]:
                    tds = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                    if len(tds) >= 5:
                        date_parts = tds[0].replace("-", "/").split("/")
                        if len(date_parts) >= 2:
                            m_d = f"{int(date_parts[-2]):02d}/{int(date_parts[-1]):02d}"
                            foreign_str = tds[1].replace(",", "").strip()
                            try:
                                foreign_val = int(foreign_str)
                                if foreign_val < 0:
                                    futures_map[m_d] = f"空單:{abs(foreign_val):,}口"
                                else:
                                    futures_map[m_d] = f"多單:{foreign_val:,}口"
                            except ValueError:
                                futures_map[m_d] = f"{foreign_str}口"
                        if len(futures_map) >= 10:
                            break
    except Exception as e:
        print(f"HiStock 現貨/期貨抓取異常: {e}")
    return inst_map, futures_map

def is_pure_stock(code, name):
    code = str(code).strip()
    name = str(name).strip()
    if len(code) != 4 or not code.isdigit():
        return False
    if code.startswith(('00', '01', '02')):
        return False
    exclude_keywords = ["ETF", "反1", "正2", "債", "購", "售", "牛", "熊"]
    if any(k in name for k in exclude_keywords):
        return False
    return True

def get_tse_market_rankings():
    today_str = datetime.date.today().strftime("%Y%m%d")
    url = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={today_str}&selectType=ALL&response=json"
    headers = {"User-Agent": "Mozilla/5.0"}
    buy_top10, sell_top10 = [], []
    try:
        res = requests.get(url, headers=headers, timeout=12).json()
        if res.get("stat") == "OK" and "data" in res:
            stock_data = []
            for row in res["data"]:
                code = str(row[0]).strip()
                name = str(row[1]).strip()
                if not is_pure_stock(code, name):
                    continue
                try:
                    shares = int(str(row[18]).replace(",", ""))
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
    """櫃買中心上櫃三大法人合計買超/賣超（含跨網域三層備援與全例外防禦）"""
    today = datetime.date.today()
    roc_year = today.year - 1911
    d_query = f"{roc_year}/{today.month:02d}/{today.day:02d}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Referer": "https://www.tpex.org.tw/"
    }

    buy_top10, sell_top10 = [], []

    # 通道 1：櫃買中心官方日報 JSON（timeout 放寬至 15 秒）
    try:
        url_tpex = f"https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&o=json&se=EW&t=D&d={d_query}"
        res = requests.get(url_tpex, headers=headers, timeout=15)
        if res.status_code == 200:
            data_json = res.json()
            rows = data_json.get("aaData", [])
            if not rows and "tables" in data_json and len(data_json["tables"]) > 0:
                rows = data_json["tables"][0].get("data", [])

            if rows:
                stock_data = []
                for r in rows:
                    if len(r) >= 11:
                        code = str(r[0]).strip()
                        name = str(r[1]).strip()
                        if not is_pure_stock(code, name):
                            continue
                        try:
                            val_str = str(r[-1]).replace(",", "").replace("+", "").strip()
                            shares = int(val_str)
                            sheets = round(shares / 1000)
                            stock_data.append((name, sheets))
                        except Exception:
                            continue
                
                if stock_data:
                    stock_data.sort(key=lambda x: x[1], reverse=True)
                    for idx, (name, sheets) in enumerate(stock_data[:10], 1):
                        buy_top10.append(f"{idx}. {name} ({sheets:+,}張)")
                    for idx, (name, sheets) in enumerate(stock_data[-10:][::-1], 1):
                        sell_top10.append(f"{idx}. {name} ({sheets:+,}張)")
                    return buy_top10, sell_top10
    except Exception as e:
        print(f"上櫃通道 1 異常: {e}")

    # 通道 2：櫃買中心 HTML 備援（timeout 放寬至 15 秒）
    try:
        alt_url = f"https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&o=htm&d={roc_year}/{today.month}/{today.day}&se=EW&t=D"
        alt_res = requests.get(alt_url, headers=headers, timeout=15)
        if alt_res.status_code == 200:
            soup = BeautifulSoup(alt_res.text, "html.parser")
            stock_data = []
            for r in soup.find_all("tr"):
                tds = [td.get_text(strip=True) for td in r.find_all("td")]
                if len(tds) >= 11 and tds[0].isdigit():
                    code = tds[0].strip()
                    name = tds[1].strip()
                    if not is_pure_stock(code, name):
                        continue
                    try:
                        val_str = tds[-1].replace(",", "").replace("+", "").strip()
                        sheets = round(int(val_str) / 1000)
                        stock_data.append((name, sheets))
                    except Exception:
                        continue
            if stock_data:
                stock_data.sort(key=lambda x: x[1], reverse=True)
                for idx, (name, sheets) in enumerate(stock_data[:10], 1):
                    buy_top10.append(f"{idx}. {name} ({sheets:+,}張)")
                for idx, (name, sheets) in enumerate(stock_data[-10:][::-1], 1):
                    sell_top10.append(f"{idx}. {name} ({sheets:+,}張)")
                return buy_top10, sell_top10
    except Exception as e:
        print(f"上櫃通道 2 備援異常: {e}")

    # 通道 3：跨網域第三方備援（HiStock 上櫃三大法人買賣超）
    try:
        histock_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        }
        url_hs = "https://histock.tw/stock/three.aspx?m=otc"
        res_hs = requests.get(url_hs, headers=histock_headers, timeout=15)
        if res_hs.status_code == 200:
            soup_hs = BeautifulSoup(res_hs.text, "html.parser")
            stock_data = []
            for r in soup_hs.find_all("tr"):
                tds = [td.get_text(strip=True) for td in r.find_all("td")]
                if len(tds) >= 5:
                    code = tds[0].strip()
                    name = tds[1].strip()
                    if not is_pure_stock(code, name):
                        continue
                    try:
                        val_str = tds[-1].replace(",", "").replace("+", "").strip()
                        sheets = int(float(val_str))
                        stock_data.append((name, sheets))
                    except Exception:
                        continue
            if stock_data:
                stock_data.sort(key=lambda x: x[1], reverse=True)
                for idx, (name, sheets) in enumerate(stock_data[:10], 1):
                    buy_top10.append(f"{idx}. {name} ({sheets:+,}張)")
                for idx, (name, sheets) in enumerate(stock_data[-10:][::-1], 1):
                    sell_top10.append(f"{idx}. {name} ({sheets:+,}張)")
                print("上櫃通道 3（HiStock 跨網域備援）成功取得資料")
    except Exception as e:
        print(f"上櫃通道 3 備援異常: {e}")

    return buy_top10, sell_top10

def build_message():
    today_str = datetime.date.today().strftime("%Y/%m/%d")
    twse_map, twse_days_list, bias_map = get_twse_market_data_and_bias()
    fx_map = get_taifex_fx_rate_5days()
    inst_map, futures_map = get_histock_three_and_futures_5days()
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

    # --- 近5日新台幣/美元匯率 ---
    msg += "💵【近5日新台幣/美元匯率】\n"
    msg += "─" * 22 + "\n"
    for d in twse_days_list:
        fx_label = fx_map.get(d, "匯率:計算中")
        msg += f"{d} {fx_label}\n"
    msg += "─" * 22 + "\n\n"

    # --- 近5日台指期外資未平倉淨空單 ---
    msg += "🎯【近5日台指期外資未平倉淨空單】\n"
    msg += "─" * 22 + "\n"
    for d in twse_days_list:
        foreign_label = futures_map.get(d, "已結算")
        msg += f"{d} {foreign_label}\n"
    msg += "─" * 22 + "\n\n"

    # --- 近5日大盤5MA乖離率 ---
    msg += "📐【近5日大盤5MA乖離率】\n"
    msg += "─" * 22 + "\n"
    for d in twse_days_list:
        bias_val = bias_map.get(d, "計算中")
        msg += f"{d} 乖離率:{bias_val}\n"
    msg += "─" * 22 + "\n\n"

    # --- 上市三大法人排行 ---
    msg += "📈【上市三大法人買超Top】\n"
    msg += ("\n".join(tse_buy) if tse_buy else "資料未取得或結算中") + "\n\n"

    msg += "📉【上市三大法人賣超Top】\n"
    msg += ("\n".join(tse_sell) if tse_sell else "資料未取得或結算中") + "\n\n"

    # --- 上櫃三大法人排行 ---
    msg += "📈【上櫃三大法人買超Top】\n"
    msg += ("\n".join(otc_buy) if otc_buy else "資料未取得或結算中") + "\n\n"

    msg += "📉【上櫃三大法人賣超Top】\n"
    msg += ("\n".join(otc_sell) if otc_sell else "資料未取得或結算中") + "\n\n"

    # --- 官股查詢直達連結 ---
    msg += "🏛️【八大官股即時動向查詢】\n"
    msg += "https://www.wantgoo.com/stock/public-bank/trend?market=-1&typeShow=amount\n\n"

    # --- 上市及上櫃融資維持率查詢連結 ---
    msg += "⚖️【上市及上櫃融資維持率】\n"
    msg += "https://reurl.cc/Xxzm8D"

    return msg

if __name__ == "__main__":
    try:
        if not is_trading_day():
            print("今日為休市日（國定假日或颱風假），取消推播。")
            sys.exit(0)

        content = build_message()
        send_line_message(content)
    except Exception as e:
        print(f"主執行程序異常: {e}")
