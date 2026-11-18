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
import matplotlib.gridspec as gridspec
import numpy as np

LINE_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "")
CACHE_FILE = "charts/payload_cache.json"

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
    """
    抓取近 5 日新台幣/美元匯率及漲跌（央行優先、期交所次之、Yahoo Finance 殿後）
    """
    today = datetime.date.today()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    fx_map = {}
    fx_raw_map = {}
    rate_records = {}

    # 第一通道：中央銀行官方每日收盤匯率網頁
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

    # 第二通道：期交所官方 OpenAPI
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

    # 第三通道：Yahoo Finance 1個月端點
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

def get_gov_bank_5days():
    """抓取近 5 日八大官股買賣超金額（單位：億元）"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    gov_map = {}
    try:
        url = "https://histock.tw/stock/public.aspx"
        res = requests.get(url, headers=headers, timeout=12)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            for tbl in soup.find_all("table"):
                if "八大" in tbl.get_text() or "官股" in tbl.get_text() or "合計" in tbl.get_text():
                    for row in tbl.find_all("tr")[1:]:
                        tds = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                        if len(tds) >= 2:
                            m_date = re.search(r'(\d{1,2})[/-](\d{1,2})', tds[0])
                            if m_date:
                                m_d = f"{int(m_date.group(1)):02d}/{int(m_date.group(2)):02d}"
                                for cell in reversed(tds[1:]):
                                    clean_val = cell.replace(",", "").replace("+", "").replace("億", "").strip()
                                    try:
                                        val_f = float(clean_val)
                                        if abs(val_f) > 5000:
                                            val_f = round(val_f / 10000.0, 2)
                                        gov_map[m_d] = round(val_f, 2)
                                        break
                                    except ValueError:
                                        continue
                        if len(gov_map) >= 10:
                            break
                if gov_map:
                    break
    except Exception as e:
        print(f"八大官股抓取異常: {e}")
    return gov_map

def get_margin_balance_5days():
    """抓取近 5 日上市及上櫃融資餘額（前一日基準，單位：億元）"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    tse_margin = {}
    otc_margin = {}
    days_order = []

    try:
        url_tse = "https://histock.tw/stock/three.aspx?m=mg"
        res_tse = requests.get(url_tse, headers=headers, timeout=12)
        if res_tse.status_code == 200:
            soup = BeautifulSoup(res_tse.text, "html.parser")
            for tbl in soup.find_all("table"):
                if "融資餘額" in tbl.get_text():
                    for row in tbl.find_all("tr")[1:]:
                        tds = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                        if len(tds) >= 4:
                            m_date = re.search(r'(\d{1,2})[/-](\d{1,2})', tds[0])
                            if m_date:
                                m_d = f"{int(m_date.group(1)):02d}/{int(m_date.group(2)):02d}"
                                for cell in tds[1:4]:
                                    clean_v = cell.replace(",", "").replace("億", "").strip()
                                    try:
                                        v_f = float(clean_v)
                                        if 1000 <= v_f <= 8000:
                                            tse_margin[m_d] = round(v_f, 1)
                                            if m_d not in days_order:
                                                days_order.append(m_d)
                                            break
                                    except ValueError:
                                        continue
                        if len(days_order) >= 6:
                            break
                    if tse_margin:
                        break
    except Exception as e:
        print(f"上市融資餘額抓取異常: {e}")

    try:
        url_otc = "https://histock.tw/stock/three.aspx?m=otcmg"
        res_otc = requests.get(url_otc, headers=headers, timeout=12)
        if res_otc.status_code == 200:
            soup = BeautifulSoup(res_otc.text, "html.parser")
            for tbl in soup.find_all("table"):
                if "融資餘額" in tbl.get_text():
                    for row in tbl.find_all("tr")[1:]:
                        tds = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                        if len(tds) >= 4:
                            m_date = re.search(r'(\d{1,2})[/-](\d{1,2})', tds[0])
                            if m_date:
                                m_d = f"{int(m_date.group(1)):02d}/{int(m_date.group(2)):02d}"
                                for cell in tds[1:4]:
                                    clean_v = cell.replace(",", "").replace("億", "").strip()
                                    try:
                                        v_f = float(clean_v)
                                        if 200 <= v_f <= 3000:
                                            otc_margin[m_d] = round(v_f, 1)
                                            break
                                    except ValueError:
                                        continue
                        if len(otc_margin) >= 6:
                            break
                    if otc_margin:
                        break
    except Exception as e:
        print(f"上櫃融資餘額抓取異常: {e}")

    today_md = f"{datetime.date.today().month:02d}/{datetime.date.today().day:02d}"
    filtered_days = [d for d in days_order if d != today_md][:5]
    return filtered_days, tse_margin, otc_margin

def generate_dashboard_chart(twse_days_list, turnover_map, inst_num_map, fx_raw_map, futures_num_map, bias_num_map, gov_map, margin_days, tse_margin, otc_margin):
    """繪製 Dark Mode 專業看盤風格直式戰情圖表"""
    os.makedirs("charts", exist_ok=True)
    output_path = "charts/daily_chart.png"

    plt.rcParams['font.sans-serif'] = ['Noto Sans CJK JP', 'Noto Sans CJK TC', 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False

    BG_COLOR = "#0F172A"
    CARD_BG = "#1E293B"
    TEXT_MAIN = "#F8FAFC"
    TEXT_SUB = "#94A3B8"
    GRID_COLOR = "#334155"
    RED_UP = "#EF4444"
    GREEN_DOWN = "#10B981"
    BLUE_BAR = "#38BDF8"
    AMBER_BAR = "#F59E0B"
    PURPLE_BAR = "#A855F7"

    fig = plt.figure(figsize=(10, 21), dpi=130, facecolor=BG_COLOR)
    gs = gridspec.GridSpec(6, 2, figure=fig, height_ratios=[1.1, 1.0, 1.0, 1.0, 1.0, 1.25], hspace=0.45, wspace=0.28)

    today_str = datetime.date.today().strftime("%Y/%m/%d")
    fig.suptitle(f"台股盤後籌碼戰情總覽  |  {today_str}", fontsize=20, fontweight="bold", color=TEXT_MAIN, y=0.965)

    days_rev = list(reversed(twse_days_list))

    def style_ax(ax, title):
        ax.set_facecolor(CARD_BG)
        ax.set_title(title, fontsize=13.5, fontweight="bold", color=TEXT_MAIN, loc="left", pad=10)
        ax.tick_params(colors=TEXT_MAIN, labelsize=10.5)
        ax.xaxis.grid(True, linestyle="--", alpha=0.35, color=GRID_COLOR)
        ax.set_axisbelow(True)
        for spine in ax.spines.values():
            spine.set_edgecolor(GRID_COLOR)
            spine.set_linewidth(0.8)

    # 1. 左上：近5日大盤成交量
    ax1 = fig.add_subplot(gs[0, 0])
    style_ax(ax1, "① 近5日大盤成交量 (億)")
    vals1 = [turnover_map.get(d, 0) for d in days_rev]
    bars1 = ax1.barh(days_rev, vals1, color=BLUE_BAR, height=0.55)
    max_v1 = max(vals1) if vals1 else 1000
    ax1.set_xlim(0, max_v1 * 1.28)
    for bar, v in zip(bars1, vals1):
        ax1.text(v + max_v1 * 0.02, bar.get_y() + bar.get_height()/2, f"{v:,.1f}", va="center", ha="left", color=TEXT_MAIN, fontsize=9.5, fontweight="bold")

    # 2. 右上：近5日三大法人買賣超
    ax2 = fig.add_subplot(gs[0, 1])
    style_ax(ax2, "② 近5日三大法人買賣超 (億)")
    vals2 = [inst_num_map.get(d, 0) for d in days_rev]
    colors2 = [RED_UP if v >= 0 else GREEN_DOWN for v in vals2]
    bars2 = ax2.barh(days_rev, vals2, color=colors2, height=0.55)
    ax2.axvline(0, color=TEXT_SUB, linewidth=1)
    max_abs2 = max([abs(v) for v in vals2]) if vals2 else 500
    max_abs2 = max(max_abs2, 100)
    ax2.set_xlim(-max_abs2 * 1.38, max_abs2 * 1.38)
    for bar, v in zip(bars2, vals2):
        offset = max_abs2 * 0.04 if v >= 0 else -max_abs2 * 0.04
        ha_align = "left" if v >= 0 else "right"
        ax2.text(v + offset, bar.get_y() + bar.get_height()/2, f"{v:+.1f}", va="center", ha=ha_align, color=TEXT_MAIN, fontsize=9.5, fontweight="bold")

    # 3. 第二層：近5日新台幣/美元匯率
    ax3 = fig.add_subplot(gs[1, :])
    style_ax(ax3, "③ 近5日新台幣/美元匯率 (紅:貶值 / 綠:升值)")
    fx_rates = [fx_raw_map.get(d, (0, 0))[0] for d in days_rev]
    fx_diffs = [fx_raw_map.get(d, (0, 0))[1] for d in days_rev]
    valid_fx = [r for r in fx_rates if r > 0]
    min_fx = min(valid_fx) - 0.15 if valid_fx else 32.0
    max_fx = max(valid_fx) + 0.25 if valid_fx else 33.5
    colors3 = [RED_UP if df > 0.0001 else (GREEN_DOWN if df < -0.0001 else TEXT_SUB) for df in fx_diffs]
    bars3 = ax3.barh(days_rev, [max(0, r - min_fx) for r in fx_rates], left=min_fx, color=colors3, height=0.52)
    ax3.set_xlim(min_fx, max_fx)
    for bar, r, df in zip(bars3, fx_rates, fx_diffs):
        if r > 0:
            tag = f"貶{abs(df):.3f}" if df > 0.0001 else (f"升{abs(df):.3f}" if df < -0.0001 else "平盤")
            ax3.text(r + (max_fx - min_fx)*0.015, bar.get_y() + bar.get_height()/2, f"{r:.3f} ({tag})", va="center", ha="left", color=TEXT_MAIN, fontsize=10.5, fontweight="bold")

    # 4. 第三層：近5日台指期外資未平倉淨空單
    ax4 = fig.add_subplot(gs[2, :])
    style_ax(ax4, "④ 近5日台指期外資未平倉淨空單 (口)")
    vals4 = [abs(futures_num_map.get(d, 0)) for d in days_rev]
    bars4 = ax4.barh(days_rev, vals4, color=PURPLE_BAR, height=0.52)
    max_v4 = max(vals4) if vals4 else 100000
    ax4.set_xlim(0, max_v4 * 1.22)
    for bar, d in zip(bars4, days_rev):
        raw_f = futures_num_map.get(d, 0)
        label_f = f"空單 {abs(raw_f):,} 口" if raw_f <= 0 else f"多單 {raw_f:,} 口"
        ax4.text(abs(raw_f) + max_v4 * 0.015, bar.get_y() + bar.get_height()/2, label_f, va="center", ha="left", color=TEXT_MAIN, fontsize=10.5, fontweight="bold")

    # 5. 第四層：近5日大盤5MA乖離率
    ax5 = fig.add_subplot(gs[3, :])
    style_ax(ax5, "⑤ 近5日大盤5MA乖離率 (%)")
    vals5 = [bias_num_map.get(d, 0) for d in days_rev]
    colors5 = [RED_UP if v >= 0 else GREEN_DOWN for v in vals5]
    bars5 = ax5.barh(days_rev, vals5, color=colors5, height=0.52)
    ax5.axvline(0, color=TEXT_SUB, linewidth=1)
    max_abs5 = max([abs(v) for v in vals5]) if vals5 else 5
    max_abs5 = max(max_abs5, 1.5)
    ax5.set_xlim(-max_abs5 * 1.28, max_abs5 * 1.28)
    for bar, v in zip(bars5, vals5):
        offset = max_abs5 * 0.025 if v >= 0 else -max_abs5 * 0.025
        ha_align = "left" if v >= 0 else "right"
        ax5.text(v + offset, bar.get_y() + bar.get_height()/2, f"{v:+.2f}%", va="center", ha=ha_align, color=TEXT_MAIN, fontsize=10.5, fontweight="bold")

    # 6. 第五層：近5日八大官股買賣超動向
    ax6 = fig.add_subplot(gs[4, :])
    style_ax(ax6, "⑥ 近5日八大官股買賣超動向 (億)")
    vals6 = [gov_map.get(d, 0) for d in days_rev]
    colors6 = [RED_UP if v >= 0 else GREEN_DOWN for v in vals6]
    bars6 = ax6.barh(days_rev, vals6, color=colors6, height=0.52)
    ax6.axvline(0, color=TEXT_SUB, linewidth=1)
    max_abs6 = max([abs(v) for v in vals6]) if vals6 else 100
    max_abs6 = max(max_abs6, 20)
    ax6.set_xlim(-max_abs6 * 1.3, max_abs6 * 1.3)
    for bar, v in zip(bars6, vals6):
        offset = max_abs6 * 0.025 if v >= 0 else -max_abs6 * 0.025
        ha_align = "left" if v >= 0 else "right"
        txt6 = f"{v:+.2f}億" if v != 0 else "結算中"
        ax6.text(v + offset, bar.get_y() + bar.get_height()/2, txt6, va="center", ha=ha_align, color=TEXT_MAIN, fontsize=10.5, fontweight="bold")

    # 7. 第六層：近5日上市及上櫃融資餘額（雙色並列橫向柱狀圖）
    ax7 = fig.add_subplot(gs[5, :])
    m_days_rev = list(reversed(margin_days)) if margin_days else days_rev
    style_ax(ax7, "⑦ 近5日上市及上櫃融資餘額 [前一日基準] (億)")
    y_pos = np.arange(len(m_days_rev))
    bar_h = 0.36
    tse_vals = [tse_margin.get(d, 0) for d in m_days_rev]
    otc_vals = [otc_margin.get(d, 0) for d in m_days_rev]

    rects_tse = ax7.barh(y_pos + bar_h/2, tse_vals, height=bar_h, label="上市融資餘額", color=BLUE_BAR)
    rects_otc = ax7.barh(y_pos - bar_h/2, otc_vals, height=bar_h, label="上櫃融資餘額", color=AMBER_BAR)
    ax7.set_yticks(y_pos)
    ax7.set_yticklabels(m_days_rev)
    max_m = max(tse_vals + otc_vals) if (tse_vals + otc_vals) else 3500
    max_m = max(max_m, 500)
    ax7.set_xlim(0, max_m * 1.25)
    ax7.legend(loc="lower right", facecolor=BG_COLOR, edgecolor=GRID_COLOR, labelcolor=TEXT_MAIN, fontsize=10)

    for rect, v in zip(rects_tse, tse_vals):
        if v > 0:
            ax7.text(v + max_m * 0.015, rect.get_y() + rect.get_height()/2, f"上市 {v:,.1f}億", va="center", ha="left", color=BLUE_BAR, fontsize=9.5, fontweight="bold")
    for rect, v in zip(rects_otc, otc_vals):
        if v > 0:
            ax7.text(v + max_m * 0.015, rect.get_y() + rect.get_height()/2, f"上櫃 {v:,.1f}億", va="center", ha="left", color=AMBER_BAR, fontsize=9.5, fontweight="bold")

    plt.subplots_adjust(left=0.11, right=0.95, top=0.93, bottom=0.03)
    plt.savefig(output_path, facecolor=BG_COLOR)
    plt.close(fig)
    print(f"成功產出戰情圖表: {output_path}")
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
    """Step 1：抓取全市場數據、繪製戰情圖表，並將文字內容暫存至本地快取檔"""
    today_str = datetime.date.today().strftime("%Y/%m/%d")
    twse_map, twse_days_list, bias_map, bias_num_map = get_twse_market_data_and_bias()
    fx_map, fx_raw_map = get_taifex_fx_rate_5days()
    inst_map, inst_num_map, futures_map, futures_num_map = get_histock_three_and_futures_5days()
    gov_map = get_gov_bank_5days()
    margin_days, tse_margin, otc_margin = get_margin_balance_5days()
    tse_buy, tse_sell = get_tse_market_rankings()
    otc_buy, otc_sell = get_otc_market_rankings()

    image_url = None
    try:
        generate_dashboard_chart(
            twse_days_list, twse_map, inst_num_map, fx_raw_map,
            futures_num_map, bias_num_map, gov_map,
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
    msg += "https://reurl.cc/mkV8Bl\n\n"

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

    # 緩衝 3 秒確保 GitHub raw CDN 同步更新完成
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
