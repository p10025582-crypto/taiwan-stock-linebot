import os
import re
import sys
import json
import time
import requests
import datetime
from bs4 import BeautifulSoup
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Circle
import numpy as np

LINE_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "")
CACHE_FILE = "charts/payload_cache.json"
OTC_HISTORY_FILE = "charts/otc_margin_history.json"

def is_trading_day():
    """向證交所官方 FMTQIK 報表確認今天是否有實際開盤交易"""
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

def send_line_broadcast(text, image_url=None):
    """透過單次 LINE Broadcast API 呼叫同時發送圖片與文字（僅計為 1 次推播）"""
    if not LINE_ACCESS_TOKEN:
        print("未設定 LINE 金鑰 (LINE_CHANNEL_ACCESS_TOKEN)")
        return
    url = "https://api.line.me/v2/bot/message/broadcast"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"
    }
    messages = []
    if image_url:
        messages.append({
            "type": "image",
            "originalContentUrl": image_url,
            "previewImageUrl": image_url
        })
    messages.append({"type": "text", "text": text})

    payload = {"messages": messages}
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        print(f"LINE 廣播推播狀態: {resp.status_code}")
    except Exception as e:
        print(f"LINE 發送異常: {e}")

def get_twse_market_data_and_bias():
    """抓取大盤成交量與近 5 日 5MA 乖離率（具備跨月份自動拼接功能）"""
    today = datetime.date.today()
    date_str = today.strftime("%Y%m%d")
    headers = {"User-Agent": "Mozilla/5.0"}
    turnover_map = {}
    twse_days_list = []
    bias_map = {}
    bias_num_map = {}

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
                bias_num_map[m_d] = round(bias, 2)
    except Exception as e:
        print(f"大盤資料與乖離率解析異常: {e}")

    return turnover_map, twse_days_list, bias_map, bias_num_map

def get_taifex_fx_rate_5days():
    """抓取近 5 日新台幣/美元匯率及漲跌（央行優先、期交所次之、Yahoo Finance 殿後）"""
    today = datetime.date.today()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    fx_map = {}
    fx_raw_map = {}
    rate_records = {}

    try:
        url_cbc = "https://www.cbc.gov.tw/tw/lp-645-1.html"
        res_cbc = requests.get(url_cbc, headers=headers, timeout=10)
        if res_cbc.status_code == 200:
            soup = BeautifulSoup(res_cbc.text, "html.parser")
            for tr in soup.find_all("tr"):
                tds = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                if len(tds) >= 2:
                    d_obj = None
                    rate_val = None
                    for cell in tds:
                        m_date = re.search(r'(\d{2,4})[/-](\d{1,2})[/-](\d{1,2})', cell)
                        if m_date and not d_obj:
                            y, m, d = int(m_date.group(1)), int(m_date.group(2)), int(m_date.group(3))
                            if y < 1911:
                                y += 1911
                            try:
                                d_obj = datetime.date(y, m, d)
                            except ValueError:
                                pass
                        try:
                            val = float(cell.replace(",", ""))
                            if 25.0 <= val <= 38.0:
                                rate_val = round(val, 3)
                        except ValueError:
                            continue
                    if d_obj and rate_val:
                        rate_records[d_obj] = rate_val
    except Exception as e:
        print(f"第一通道（中央銀行網頁）異常: {e}")

    if today not in rate_records or len(rate_records) < 6:
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
                            d_obj = datetime.datetime.strptime(date_str, "%Y%m%d").date()
                            rate_val = float(usd_ntd)
                            if d_obj not in rate_records:
                                rate_records[d_obj] = rate_val
                        except ValueError:
                            continue
        except Exception as e:
            print(f"第二通道（期交所外匯 API）異常: {e}")

    if today not in rate_records or len(rate_records) < 6:
        try:
            url_yf = "https://query1.finance.yahoo.com/v8/finance/chart/USDTWD=X?range=1mo&interval=1d"
            res_yf = requests.get(url_yf, headers=headers, timeout=10)
            if res_yf.status_code == 200:
                yf_data = res_yf.json()
                result = yf_data.get("chart", {}).get("result", [{}])[0]
                timestamps = result.get("timestamp", [])
                closes = result.get("indicators", {}).get("quote", [{}])[0].get("close", [])
                tz_tw = datetime.timezone(datetime.timedelta(hours=8))

                for ts, c_val in zip(timestamps, closes):
                    if c_val is not None and 25.0 <= float(c_val) <= 38.0:
                        d_obj = datetime.datetime.fromtimestamp(ts, tz_tw).date()
                        if d_obj not in rate_records:
                            rate_records[d_obj] = round(float(c_val), 3)

                if today not in rate_records:
                    meta = result.get("meta", {})
                    current_rate = meta.get("regularMarketPrice")
                    if current_rate and 25.0 <= float(current_rate) <= 38.0:
                        rate_records[today] = round(float(current_rate), 3)
        except Exception as e:
            print(f"第三通道（Yahoo Finance）異常: {e}")

    try:
        sorted_dates = sorted(rate_records.keys())
        if len(sorted_dates) >= 2:
            target_dates = sorted_dates[-6:]
            for i in range(1, len(target_dates)):
                curr_d_obj = target_dates[i]
                prev_d_obj = target_dates[i - 1]
                curr_r = rate_records[curr_d_obj]
                prev_r = rate_records[prev_d_obj]
                diff = round(curr_r - prev_r, 4)

                if diff < -0.0001:
                    chg_str = f"(升{abs(diff):.3f}) 🟢"
                elif diff > 0.0001:
                    chg_str = f"(貶{abs(diff):.3f}) 🔴"
                else:
                    chg_str = "(平盤) ⚪"

                m_d = f"{curr_d_obj.month:02d}/{curr_d_obj.day:02d}"
                fx_map[m_d] = f"匯率:{curr_r:.3f} {chg_str}"
                fx_raw_map[m_d] = (curr_r, diff)
    except Exception as e:
        print(f"匯率漲跌計算異常: {e}")

    return fx_map, fx_raw_map

def get_histock_three_and_futures_5days():
    url = "https://histock.tw/stock/three.aspx?m=three"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }
    inst_map = {}
    inst_num_map = {}
    futures_map = {}
    futures_num_map = {}
    try:
        res = requests.get(url, headers=headers, timeout=12)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            tables = soup.find_all("table")
            
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
                                inst_num_map[m_d] = net_float
                            except ValueError:
                                inst_map[m_d] = f"{net_str}億"
                        if len(inst_map) >= 10:
                            break

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
                                futures_num_map[m_d] = foreign_val
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
    return inst_map, inst_num_map, futures_map, futures_num_map

def _extract_tpex_otc_margin_from_openapi(headers):
    """
    動態解析櫃買中心官方 OpenAPI (tpex_mainboard_margin_balance) 的最新上櫃融資餘額總計（億元）。
    自動偵測欄位名稱並印出首筆結構供 Log 確認。
    """
    result_map = {}
    url_openapi = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_margin_balance"
    try:
        res_api = requests.get(url_openapi, headers=headers, timeout=15)
        if res_api.status_code == 200:
            items = res_api.json()
            if isinstance(items, list) and len(items) > 0:
                print(f"TPEx OpenAPI 首筆欄位範例: {list(items[0].keys())}")
                sum_by_date = {}
                for it in items:
                    # 1. 動態尋找日期欄位
                    d_str = ""
                    for k, v in it.items():
                        if "date" in k.lower() or "日期" in k:
                            d_str = str(v).strip()
                            break
                    if not d_str or len(d_str) < 6:
                        continue

                    # 2. 動態尋找今日融資餘額（張數或金額）與收盤價
                    bal_val = None
                    close_val = None
                    for k, v in it.items():
                        kl = k.lower()
                        v_clean = str(v).replace(",", "").strip()
                        try:
                            num = float(v_clean)
                        except ValueError:
                            continue

                        # 排除「前日餘額 (Previous/Pre)」，鎖定「今日融資餘額 (MarginPurchaseBalance / Balance)」
                        if ("margin" in kl and "balance" in kl and "prev" not in kl and "pre" not in kl) or ("融資餘額" in k and "前" not in k):
                            bal_val = num
                        elif ("close" in kl and "prev" not in kl) or ("收盤" in k):
                            close_val = num

                    if bal_val is not None and bal_val > 0:
                        m_d = f"{int(d_str[-4:-2]):02d}/{int(d_str[-2:]):02d}"
                        # 若有收盤價且餘額為張數（通常單檔 < 50 萬張），以 張數 * 1000股 * 收盤價 換算億元
                        if close_val is not None and close_val > 0 and bal_val < 500000:
                            sum_by_date[m_d] = sum_by_date.get(m_d, 0.0) + (bal_val * 1000.0 * close_val) / 100000000.0
                        else:
                            # 若已是仟元直接除以 100000 換算億元
                            sum_by_date[m_d] = sum_by_date.get(m_d, 0.0) + (bal_val / 100000.0)

                for m_d, tot_b in sum_by_date.items():
                    if 300 <= tot_b <= 5000:
                        result_map[m_d] = round(tot_b, 1)
                        print(f"TPEx OpenAPI 成功計算上櫃融資餘額 [{m_d}]: {result_map[m_d]} 億")
    except Exception as e:
        print(f"TPEx OpenAPI 解析異常: {e}")
    return result_map

def _fetch_tpex_daily_margin_web(target_date, headers):
    """
    備援通道：直接向櫃買中心官方新版網頁報表端點查询指定日期的全市場上櫃融資餘額總計（億元）
    """
    m_d = f"{target_date.month:02d}/{target_date.day:02d}"
    d_west = target_date.strftime("%Y/%m/%d")
    roc_y = target_date.year - 1911
    d_roc = f"{roc_y}/{target_date.month:02d}/{target_date.day:02d}"

    candidate_urls = [
        f"https://www.tpex.org.tw/www/zh-tw/margin/balance?date={d_west}&id=&response=json",
        f"https://www.tpex.org.tw/web/stock/margin_trading/margin_balance/margin_bal_result.php?l=zh-tw&o=json&d={d_roc}"
    ]
    req_h = dict(headers)
    req_h["Referer"] = "https://www.tpex.org.tw/zh-tw/mainboard/trading/margin-trading/transactions.html"

    for u in candidate_urls:
        try:
            res = requests.get(u, headers=req_h, timeout=10)
            if res.status_code == 200 and res.text.strip().startswith("{"):
                j = res.json()
                tables = j.get("tables", [])
                for tbl in tables:
                    # 尋找合計列（通常在 summary 或 tfoot 或 data 最後幾列）
                    rows_to_check = tbl.get("summary", []) + tbl.get("tfoot", []) + tbl.get("data", [])[-3:]
                    for row in rows_to_check:
                        row_str = "".join([str(c) for c in row])
                        if "合計" in row_str or "總計" in row_str or len(rows_to_check) <= 3:
                            big_nums = []
                            for c in row:
                                c_clean = str(c).replace(",", "").strip()
                                if c_clean.isdigit() and int(c_clean) > 10000000:
                                    big_nums.append(int(c_clean))
                            if big_nums:
                                # 取最大的金額欄位（融資餘額仟元）換算億元
                                otc_b = round(max(big_nums) / 100000.0, 1)
                                if 300 <= otc_b <= 5000:
                                    print(f"櫃買中心官方報表補齊上櫃融資 [{m_d}]: {otc_b} 億")
                                    return otc_b
        except Exception:
            continue
    return None

def get_margin_balance_5days():
    """
    抓取近 5 日上市及上櫃融資餘額（前一日基準，單位：億元）：
    - 上市：HiStock (m=mg) 精準讀取融資餘額(億)
    - 上櫃：TPEx OpenAPI 動態計算全市場總額 + 櫃買中心官方報表補齊前 4 日 + 本地歷史累積快取
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    tse_margin = {}
    otc_margin = {}
    days_order = []

    os.makedirs("charts", exist_ok=True)
    if os.path.exists(OTC_HISTORY_FILE):
        try:
            with open(OTC_HISTORY_FILE, "r", encoding="utf-8") as f:
                saved_hist = json.load(f)
                if isinstance(saved_hist, dict):
                    otc_margin.update(saved_hist)
        except Exception as e:
            print(f"讀取上櫃歷史快取異常: {e}")

    # 1. 上市融資餘額 (HiStock m=mg)
    try:
        url_tse = "https://histock.tw/stock/three.aspx?m=mg"
        res_tse = requests.get(url_tse, headers=headers, timeout=12)
        if res_tse.status_code == 200:
            soup = BeautifulSoup(res_tse.text, "html.parser")
            for tbl in soup.find_all("table"):
                if "融資餘額" in tbl.get_text():
                    for row in tbl.find_all("tr")[1:]:
                        tds = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                        if len(tds) >= 2:
                            m_date = re.search(r'(\d{1,2})[/-](\d{1,2})', tds[0])
                            if m_date:
                                m_d = f"{int(m_date.group(1)):02d}/{int(m_date.group(2)):02d}"
                                clean_v = tds[1].replace(",", "").replace("億", "").strip()
                                try:
                                    v_f = float(clean_v)
                                    if 1000 <= v_f <= 9500:
                                        tse_margin[m_d] = round(v_f, 1)
                                        if m_d not in days_order:
                                            days_order.append(m_d)
                                except ValueError:
                                    pass
                        if len(days_order) >= 7:
                            break
                    if tse_margin:
                        break
    except Exception as e:
        print(f"上市融資餘額抓取異常: {e}")

    today_md = f"{datetime.date.today().month:02d}/{datetime.date.today().day:02d}"
    filtered_days = [d for d in days_order if d != today_md][:5]

    # 2. 上櫃融資餘額通道 1：櫃買中心官方 OpenAPI 動態解析最新交易日
    openapi_res = _extract_tpex_otc_margin_from_openapi(headers)
    otc_margin.update(openapi_res)

    # 3. 上櫃融資餘額通道 2：針對尚未累積到的歷史日期，向櫃買中心官方報表逐日查詢補齊
    today = datetime.date.today()
    for d_str in filtered_days:
        if d_str not in otc_margin:
            try:
                m_int, d_int = map(int, d_str.split("/"))
                y_int = today.year if m_int <= today.month else today.year - 1
                t_date = datetime.date(y_int, m_int, d_int)
                fetched_val = _fetch_tpex_daily_margin_web(t_date, headers)
                if fetched_val is not None:
                    otc_margin[d_str] = fetched_val
            except Exception as e:
                print(f"日期 {d_str} 補齊上櫃融資異常: {e}")

    # 將最新累積的上櫃融資餘額存回本地 JSON（保留最近 15 筆）
    if otc_margin:
        try:
            trimmed = dict(list(otc_margin.items())[-15:])
            with open(OTC_HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(trimmed, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"寫入上櫃歷史快取異常: {e}")

    print(f"融資餘額最終結果 -> 日期:{filtered_days}, 上市:{tse_margin}, 上櫃:{otc_margin}")
    return filtered_days, tse_margin, otc_margin

def draw_card_box(fig, x, y, w, h, title, bg_color="#FFFFFF", header_color="#007A99"):
    """繪製冰藍資訊圖卡風格的圓角白色卡片與深藍綠標題橫幅（無 Emoji，改用幾何裝飾圓點）"""
    card = FancyBboxPatch(
        (x, y), w, h,
        boxstyle="round,pad=0,rounding_size=0.015",
        facecolor=bg_color, edgecolor="#CBE3EE", linewidth=1.2,
        transform=fig.transFigure, zorder=1
    )
    fig.patches.append(card)

    header_h = 0.038
    header = FancyBboxPatch(
        (x, y + h - header_h), w, header_h,
        boxstyle="round,pad=0,rounding_size=0.015",
        facecolor=header_color, edgecolor="none",
        transform=fig.transFigure, zorder=2
    )
    fig.patches.append(header)

    dot = Circle(
        (x + 0.018, y + h - header_h / 2), radius=0.0045,
        facecolor="#7DD3FC", edgecolor="none",
        transform=fig.transFigure, zorder=3
    )
    fig.patches.append(dot)

    fig.text(
        x + 0.030, y + h - header_h / 2, title,
        fontsize=12.5, fontweight="bold", color="#FFFFFF",
        va="center", ha="left", zorder=3
    )

def generate_dashboard_chart(twse_days_list, turnover_map, inst_num_map, fx_raw_map, futures_num_map, bias_num_map, margin_days, tse_margin, otc_margin):
    """
    繪製清亮冰藍資訊圖卡風格（緊湊比例 10 x 12.6，6 大核心指標）：
    - 第 1、2 層雙欄（成交量、三大法人、匯率、外資期貨淨空單）
    - 第 3 層全寬橫幅（5MA乖離率）
    - 第 4 層全寬堆疊橫條圖（上市＋上櫃融資餘額）
    """
    os.makedirs("charts", exist_ok=True)
    output_path = "charts/daily_chart.png"

    plt.rcParams['font.sans-serif'] = ['Noto Sans CJK JP', 'Noto Sans CJK TC', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False

    BG_ICE = "#E6F4FA"
    TEXT_DARK = "#0F2942"
    TEXT_MUTED = "#475569"
    GRID_LIGHT = "#E2E8F0"
    RED_BAR = "#E11D48"
    GREEN_BAR = "#059669"
    BLUE_MAIN = "#0284C7"
    AMBER_OTC = "#F59E0B"
    PURPLE_FUT = "#7C3AED"
    HEADER_TEAL = "#007A99"

    fig = plt.figure(figsize=(10, 12.6), dpi=130, facecolor=BG_ICE)

    today_str = datetime.date.today().strftime("%Y/%m/%d")
    title_badge = FancyBboxPatch(
        (0.04, 0.938), 0.012, 0.030,
        boxstyle="round,pad=0,rounding_size=0.004",
        facecolor=HEADER_TEAL, edgecolor="none",
        transform=fig.transFigure, zorder=2
    )
    fig.patches.append(title_badge)
    fig.text(0.062, 0.953, "台股盤後籌碼戰情總覽", fontsize=23, fontweight="bold", color=TEXT_DARK, va="center", ha="left")
    
    date_pill = FancyBboxPatch(
        (0.74, 0.933), 0.22, 0.040,
        boxstyle="round,pad=0,rounding_size=0.018",
        facecolor=HEADER_TEAL, edgecolor="none",
        transform=fig.transFigure, zorder=2
    )
    fig.patches.append(date_pill)
    fig.text(0.85, 0.953, today_str, fontsize=15, fontweight="bold", color="#FFFFFF", va="center", ha="center", zorder=3)

    col_w = 0.445
    left_x = 0.04
    right_x = 0.515
    row_h = 0.208
    y_row1 = 0.700
    y_row2 = 0.474
    y_row3 = 0.264
    row3_h = 0.192
    y_row4 = 0.054
    row4_h = 0.192

    cards_meta = [
        (left_x,  y_row1, col_w, row_h,  "1. 近5日大盤成交量 (億)"),
        (right_x, y_row1, col_w, row_h,  "2. 近5日三大法人買賣超 (億)"),
        (left_x,  y_row2, col_w, row_h,  "3. 近5日新台幣/美元匯率 (紅:貶 / 綠:升)"),
        (right_x, y_row2, col_w, row_h,  "4. 近5日台指期外資未平倉淨空單 (口)"),
        (left_x,  y_row3, 0.92,  row3_h, "5. 近5日大盤5MA乖離率 (%)"),
        (left_x,  y_row4, 0.92,  row4_h, "6. 近5日上市及上櫃融資餘額 [前一日基準 · 堆疊總覽] (億)")
    ]

    for cx, cy, cw, ch, ctitle in cards_meta:
        draw_card_box(fig, cx, cy, cw, ch, ctitle, header_color=HEADER_TEAL)

    days_rev = list(reversed(twse_days_list))

    def create_inner_ax(cx, cy, cw, ch, left_pad=0.065, right_pad=0.025):
        ax = fig.add_axes([cx + left_pad, cy + 0.024, cw - left_pad - right_pad, ch - 0.074], zorder=4)
        ax.set_facecolor("#FFFFFF")
        ax.tick_params(axis='y', colors=TEXT_DARK, labelsize=10.5, length=0)
        ax.tick_params(axis='x', colors=TEXT_MUTED, labelsize=8.5)
        for label in ax.get_yticklabels():
            label.set_fontweight('bold')
        ax.xaxis.grid(True, linestyle="-", alpha=0.6, color=GRID_LIGHT)
        ax.set_axisbelow(True)
        for spine in ['top', 'right']:
            ax.spines[spine].set_visible(False)
        ax.spines['left'].set_color("#CBD5E1")
        ax.spines['bottom'].set_color("#CBD5E1")
        return ax

    # --- 1. 左上：近5日大盤成交量 ---
    ax1 = create_inner_ax(left_x, y_row1, col_w, row_h)
    vals1 = [turnover_map.get(d, 0) for d in days_rev]
    bars1 = ax1.barh(days_rev, vals1, color=BLUE_MAIN, height=0.56)
    max_v1 = max(vals1) if vals1 else 1000
    ax1.set_xlim(0, max_v1 * 1.32)
    for bar, v in zip(bars1, vals1):
        ax1.text(v + max_v1 * 0.025, bar.get_y() + bar.get_height()/2, f"{v:,.1f}", va="center", ha="left", color=TEXT_DARK, fontsize=10, fontweight="bold")

    # --- 2. 右上：近5日三大法人買賣超 ---
    ax2 = create_inner_ax(right_x, y_row1, col_w, row_h)
    vals2 = [inst_num_map.get(d, 0) for d in days_rev]
    colors2 = [RED_BAR if v >= 0 else GREEN_BAR for v in vals2]
    bars2 = ax2.barh(days_rev, vals2, color=colors2, height=0.56)
    ax2.axvline(0, color="#64748B", linewidth=1)
    max_abs2 = max([abs(v) for v in vals2]) if vals2 else 500
    max_abs2 = max(max_abs2, 100)
    ax2.set_xlim(-max_abs2 * 1.55, max_abs2 * 1.55)
    for bar, v in zip(bars2, vals2):
        offset = max_abs2 * 0.05 if v >= 0 else -max_abs2 * 0.05
        ha_align = "left" if v >= 0 else "right"
        ax2.text(v + offset, bar.get_y() + bar.get_height()/2, f"{v:+.1f}", va="center", ha=ha_align, color=TEXT_DARK, fontsize=10, fontweight="bold")

    # --- 3. 第二層左：近5日新台幣/美元匯率 ---
    ax3 = create_inner_ax(left_x, y_row2, col_w, row_h)
    fx_rates = [fx_raw_map.get(d, (0, 0))[0] for d in days_rev]
    fx_diffs = [fx_raw_map.get(d, (0, 0))[1] for d in days_rev]
    valid_fx = [r for r in fx_rates if r > 0]
    min_fx = min(valid_fx) - 0.12 if valid_fx else 32.0
    max_fx = max(valid_fx) + 0.35 if valid_fx else 33.5
    colors3 = [RED_BAR if df > 0.0001 else (GREEN_BAR if df < -0.0001 else "#64748B") for df in fx_diffs]
    bars3 = ax3.barh(days_rev, [max(0, r - min_fx) for r in fx_rates], left=min_fx, color=colors3, height=0.56)
    ax3.set_xlim(min_fx, max_fx)
    for bar, r, df in zip(bars3, fx_rates, fx_diffs):
        if r > 0:
            tag = f"貶{abs(df):.3f}" if df > 0.0001 else (f"升{abs(df):.3f}" if df < -0.0001 else "平")
            ax3.text(r + (max_fx - min_fx)*0.02, bar.get_y() + bar.get_height()/2, f"{r:.3f}({tag})", va="center", ha="left", color=TEXT_DARK, fontsize=9.5, fontweight="bold")

    # --- 4. 第二層右：近5日台指期外資未平倉淨空單 ---
    ax4 = create_inner_ax(right_x, y_row2, col_w, row_h)
    vals4 = [abs(futures_num_map.get(d, 0)) for d in days_rev]
    bars4 = ax4.barh(days_rev, vals4, color=PURPLE_FUT, height=0.56)
    max_v4 = max(vals4) if vals4 else 100000
    ax4.set_xlim(0, max_v4 * 1.42)
    for bar, d in zip(bars4, days_rev):
        raw_f = futures_num_map.get(d, 0)
        label_f = f"空 {abs(raw_f):,}口" if raw_f <= 0 else f"多 {raw_f:,}口"
        ax4.text(abs(raw_f) + max_v4 * 0.02, bar.get_y() + bar.get_height()/2, label_f, va="center", ha="left", color=TEXT_DARK, fontsize=9.5, fontweight="bold")

    # --- 5. 第三層全寬：近5日大盤5MA乖離率 ---
    ax5 = create_inner_ax(left_x, y_row3, 0.92, row3_h, left_pad=0.065, right_pad=0.03)
    vals5 = [bias_num_map.get(d, 0) for d in days_rev]
    colors5 = [RED_BAR if v >= 0 else GREEN_BAR for v in vals5]
    bars5 = ax5.barh(days_rev, vals5, color=colors5, height=0.56)
    ax5.axvline(0, color="#64748B", linewidth=1)
    max_abs5 = max([abs(v) for v in vals5]) if vals5 else 5
    max_abs5 = max(max_abs5, 1.5)
    ax5.set_xlim(-max_abs5 * 1.35, max_abs5 * 1.35)
    for bar, v in zip(bars5, vals5):
        offset = max_abs5 * 0.025 if v >= 0 else -max_abs5 * 0.025
        ha_align = "left" if v >= 0 else "right"
        ax5.text(v + offset, bar.get_y() + bar.get_height()/2, f"{v:+.2f}%", va="center", ha=ha_align, color=TEXT_DARK, fontsize=10, fontweight="bold")

    # --- 6. 第四層全寬：近5日上市及上櫃融資餘額（平面堆疊橫條圖） ---
    ax6 = create_inner_ax(left_x, y_row4, 0.92, row4_h, left_pad=0.065, right_pad=0.03)
    m_days_rev = list(reversed(margin_days)) if margin_days else days_rev
    tse_vals = np.array([tse_margin.get(d, 0) for d in m_days_rev], dtype=float)
    otc_vals = np.array([otc_margin.get(d, 0) for d in m_days_rev], dtype=float)
    totals = tse_vals + otc_vals

    ax6.barh(m_days_rev, tse_vals, color=BLUE_MAIN, height=0.58, label="上市融資餘額")
    ax6.barh(m_days_rev, otc_vals, left=tse_vals, color=AMBER_OTC, height=0.58, label="上櫃融資餘額")

    max_total = max(totals) if len(totals) > 0 and max(totals) > 0 else 6800
    ax6.set_xlim(0, max_total * 1.24)

    ax6.legend(
        loc="upper right", bbox_to_anchor=(1.0, 1.24), ncol=2,
        frameon=True, facecolor="#FFFFFF", edgecolor="#CBD5E1",
        fontsize=9.5
    )

    for i, (t_v, o_v, tot_v) in enumerate(zip(tse_vals, otc_vals, totals)):
        if t_v > 0:
            ax6.text(t_v / 2, i, f"上市 {t_v:,.1f}", va="center", ha="center", color="#FFFFFF", fontsize=9.0, fontweight="bold")
        if o_v > 0:
            if o_v > max_total * 0.11:
                ax6.text(t_v + o_v / 2, i, f"上櫃 {o_v:,.1f}", va="center", ha="center", color="#FFFFFF", fontsize=9.0, fontweight="bold")
                ax6.text(tot_v + max_total * 0.012, i, f"總計 {tot_v:,.1f}", va="center", ha="left", color=TEXT_DARK, fontsize=9.5, fontweight="bold")
            else:
                ax6.text(tot_v + max_total * 0.012, i, f"上櫃 {o_v:,.1f} (總計 {tot_v:,.1f})", va="center", ha="left", color=TEXT_DARK, fontsize=9.5, fontweight="bold")
        elif tot_v > 0:
            ax6.text(tot_v + max_total * 0.012, i, f"總計 {tot_v:,.1f}", va="center", ha="left", color=TEXT_DARK, fontsize=9.5, fontweight="bold")

    fig.text(0.5, 0.022, "───────   掌握市場動態 · 從容應對變化   ───────", fontsize=12, fontweight="bold", color=HEADER_TEAL, va="center", ha="center")

    plt.savefig(output_path, facecolor=BG_ICE)
    plt.close(fig)
    print(f"成功產出冰藍戰情圖表: {output_path}")
    return output_path

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
    except Exception as e:
        print(f"上櫃通道 3 備援異常: {e}")

    return buy_top10, sell_top10

def prepare_data_and_chart():
    """Step 1：抓取全市場數據、繪製冰藍戰情圖表，並將文字內容暫存至本地快取檔"""
    today_str = datetime.date.today().strftime("%Y/%m/%d")
    twse_map, twse_days_list, bias_map, bias_num_map = get_twse_market_data_and_bias()
    fx_map, fx_raw_map = get_taifex_fx_rate_5days()
    inst_map, inst_num_map, futures_map, futures_num_map = get_histock_three_and_futures_5days()
    margin_days, tse_margin, otc_margin = get_margin_balance_5days()
    tse_buy, tse_sell = get_tse_market_rankings()
    otc_buy, otc_sell = get_otc_market_rankings()

    image_url = None
    try:
        generate_dashboard_chart(
            twse_days_list, twse_map, inst_num_map, fx_raw_map,
            futures_num_map, bias_num_map,
            margin_days, tse_margin, otc_margin
        )
        if GITHUB_REPOSITORY:
            ts = int(datetime.datetime.now().timestamp())
            image_url = f"https://raw.githubusercontent.com/{GITHUB_REPOSITORY}/main/charts/daily_chart.png?v={ts}"
    except Exception as e:
        print(f"圖表產出異常（不影響文字推播）: {e}")

    msg = f"📊【台股盤後籌碼總覽】\n"
    msg += f"📅 日期：{today_str}\n\n"

    msg += "🏛️【近5日大盤量與三大法人動向】\n"
    msg += "─" * 22 + "\n"
    for d in twse_days_list:
        turnover = twse_map.get(d, 0.0)
        inst_net = inst_map.get(d, "已結算")
        msg += f"{d} 大盤:{turnover}億 | 法人:{inst_net}\n"
    msg += "─" * 22 + "\n\n"

    msg += "💵【近5日新台幣/美元匯率】\n"
    msg += "─" * 22 + "\n"
    for d in twse_days_list:
        fx_label = fx_map.get(d, "匯率:計算中")
        msg += f"{d} {fx_label}\n"
    msg += "─" * 22 + "\n\n"

    msg += "🎯【近5日台指期外資未平倉淨空單】\n"
    msg += "─" * 22 + "\n"
    for d in twse_days_list:
        foreign_label = futures_map.get(d, "已結算")
        msg += f"{d} {foreign_label}\n"
    msg += "─" * 22 + "\n\n"

    msg += "📐【近5日大盤5MA乖離率】\n"
    msg += "─" * 22 + "\n"
    for d in twse_days_list:
        bias_val = bias_map.get(d, "計算中")
        msg += f"{d} 乖離率:{bias_val}\n"
    msg += "─" * 22 + "\n\n"

    msg += "📈【上市三大法人買超Top】\n"
    msg += ("\n".join(tse_buy) if tse_buy else "資料未取得或結算中") + "\n\n"

    msg += "📉【上市三大法人賣超Top】\n"
    msg += ("\n".join(tse_sell) if tse_sell else "資料未取得或結算中") + "\n\n"

    msg += "📈【上櫃三大法人買超Top】\n"
    msg += ("\n".join(otc_buy) if otc_buy else "資料未取得或結算中") + "\n\n"

    msg += "📉【上櫃三大法人賣超Top】\n"
    msg += ("\n".join(otc_sell) if otc_sell else "資料未取得或結算中") + "\n\n"

    msg += "🏛️【八大官股即時動向】\n"
    msg += "https://pse.is/9vymbz\n\n"

    msg += "⚖️【上市及上櫃融資維持率】\n"
    msg += "https://reurl.cc/Xxzm8D"

    os.makedirs("charts", exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({"text": msg, "image_url": image_url}, f, ensure_ascii=False)
    print("數據與圖表準備完成，已寫入本地快取。")

def send_cached_broadcast():
    """Step 3：確認圖片已 Push 上 GitHub CDN 後，讀取快取並單次發送 LINE 圖文推播"""
    if not os.path.exists(CACHE_FILE):
        print("找不到推播快取檔（可能為休市日已略過），結束執行。")
        return

    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        payload = json.load(f)

    time.sleep(3)
    send_line_broadcast(payload["text"], payload.get("image_url"))

if __name__ == "__main__":
    try:
        mode = sys.argv[1] if len(sys.argv) > 1 else "--all"

        if mode == "--prepare":
            if not is_trading_day():
                print("今日為休市日（國定假日或颱風假），取消推播。")
                sys.exit(0)
            prepare_data_and_chart()

        elif mode == "--send":
            send_cached_broadcast()

        else:
            if not is_trading_day():
                print("今日為休市日（國定假日或颱風假），取消推播。")
                sys.exit(0)
            prepare_data_and_chart()
            send_cached_broadcast()

    except Exception as e:
        print(f"主執行程序異常: {e}")
