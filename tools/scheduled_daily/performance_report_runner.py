import os
import json
import calendar
import smtplib
from concurrent.futures import ThreadPoolExecutor
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone

TZ_TAIPEI = timezone(timedelta(hours=8))

def now_dt():
    return datetime.now(TZ_TAIPEI)
from typing import Optional

import pandas as pd
import requests
from bs4 import BeautifulSoup

try:
    from tools.common.lemon_backend_login import login as backend_login
except ModuleNotFoundError:  # 直接以腳本執行（cd 到此資料夾再 python xxx.py）時
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
    from tools.common.lemon_backend_login import login as backend_login

try:
    import streamlit as st
    HAS_STREAMLIT = True
except Exception:
    HAS_STREAMLIT = False


def get_secret(path_list, env_name=None, required=False, default=None, fallback_env_names=None):
    if env_name:
        value = os.getenv(env_name)
        if value not in (None, ""):
            return value

    for fallback_env_name in fallback_env_names or []:
        value = os.getenv(fallback_env_name)
        if value not in (None, ""):
            return value

    if not os.getenv("GITHUB_ACTIONS") and HAS_STREAMLIT:
        try:
            cur = st.secrets
            for key in path_list:
                cur = cur[key]
            if cur is not None and str(cur) != "":
                return cur
        except Exception:
            pass

    if required:
        raise RuntimeError(f"讀不到設定值：{'/'.join(path_list)}")

    return default


def load_accounts_dict():
    city_env_map = {
        "台北": ("taipei", "TAIPEI_EMAIL", "TAIPEI_PASSWORD"),
        "台中": ("taichung", "TAICHUNG_EMAIL", "TAICHUNG_PASSWORD"),
        "桃園": ("taoyuan", "TAOYUAN_EMAIL", "TAOYUAN_PASSWORD"),
        "新竹": ("hsinchu", "HSINCHU_EMAIL", "HSINCHU_PASSWORD"),
        "高雄": ("kaohsiung", "KAOHSIUNG_EMAIL", "KAOHSIUNG_PASSWORD"),
    }

    accounts = {}

    for city, (key, email_env, password_env) in city_env_map.items():
        email = get_secret(["accounts", key, "email"], env_name=email_env, required=False)
        password = get_secret(["accounts", key, "password"], env_name=password_env, required=False)

        if email and password:
            accounts[city] = {
                "email": email,
                "password": password,
            }

    return accounts


ACCOUNTS = load_accounts_dict()
PATH_REPORT = os.path.join(".", "dashboard_data", "latest")


LOGIN_URL = "https://backend.lemonclean.com.tw/login"
PURCHASE_URL = "https://backend.lemonclean.com.tw/purchase"
HEADERS = {"User-Agent": "Mozilla/5.0"}

CITY_ORDER = ["台北", "台中", "桃園", "新竹", "高雄"]
INCOME_ORDER = ["現金收入", "儲值金"]
CATEGORY_ORDER = ["清潔", "儲值金", "冷氣", "洗衣機", "水洗", "收納"]

REGION3_CATEGORY_ORDER = [
    "清潔",
    "冷氣",
    "洗衣機",
    "水洗",
    "收納",
    "儲值金",
    "清潔現金+儲值金",
    "家電現金+儲值金",
    "水洗/收納現金+儲值金",
    "清潔+水洗+收納現金+儲值金",
]

# Local Streamlit runtime output only. This file does NOT upload dashboard data to Google Drive.
DASHBOARD_DIR = os.path.join(".", "dashboard_data")
LATEST_DIR = os.path.join(DASHBOARD_DIR, "latest")
SNAPSHOT_DIR = os.path.join(DASHBOARD_DIR, "snapshots")
EXEC_LOG_DIR = os.path.join(DASHBOARD_DIR, "execution_logs")
DAILY_HISTORY_DIR = os.path.join(DASHBOARD_DIR, "daily_overview_history")
NEXT_MONTH_HISTORY_DIR = os.path.join(DASHBOARD_DIR, "next_month_overview_history")
MONTH_END_HISTORY_FILE = os.path.join(DAILY_HISTORY_DIR, "month_end_summary.csv")
OUTPUT_LOG_FILE = os.path.join(DASHBOARD_DIR, "output_file_log.csv")


def log(msg: str):
    print(f"[{now_dt().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def ensure_dirs():
    for p in [DASHBOARD_DIR, LATEST_DIR, SNAPSHOT_DIR, EXEC_LOG_DIR, DAILY_HISTORY_DIR, NEXT_MONTH_HISTORY_DIR]:
        if os.path.exists(p) and not os.path.isdir(p):
            raise RuntimeError(f"路徑存在但不是資料夾：{p}")
        os.makedirs(p, exist_ok=True)


def login(session, email, password):
    """登入檸檬後台（共用 tools/common/lemon_backend_login；程式結束時自動登出）。"""
    backend_login(session, email, password, login_url=LOGIN_URL, headers=HEADERS)
    log(f"✅ 登入成功：{email}")


def get_ranges():
    today = now_dt()
    y, m = today.year, today.month

    this_start = f"{y}-{m:02d}-01"
    this_end = f"{y}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}"

    if m == 12:
        ny, nm = y + 1, 1
    else:
        ny, nm = y, m + 1

    next_start = f"{ny}-{nm:02d}-01"
    next_end = f"{ny}-{nm:02d}-{calendar.monthrange(ny, nm)[1]:02d}"

    return (this_start, this_end), (next_start, next_end)


def build_url(start, end, status, keyword=""):
    params = {
        "keyword": keyword,
        "name": "",
        "phone": "",
        "orderNo": "",
        "date_s": "",
        "date_e": "",
        "clean_date_s": start,
        "clean_date_e": end,
        "paid_at_s": "",
        "paid_at_e": "",
        "refundDateS": "",
        "refundDateE": "",
        "buy": "",
        "area_id": "",
        "isCharge": "",
        "isRefund": "",
        "p_board": "on",
        "payway": "",
        "purchase_status": str(status),
        "progress_status": "",
        "invoiceStatus": "",
        "otherFee": "",
        "orderBy": "",
    }
    return requests.Request("GET", PURCHASE_URL, params=params).prepare().url


def get_keywords(city):
    if city == "新竹":
        return ["新竹"]
    if city == "高雄":
        return ["高雄", "台南"]
    return [""]


def safe_int(v):
    try:
        s = str(v).replace(",", "").strip()
        if s in ("", "-", "None", "nan"):
            return 0
        return int(float(s))
    except Exception:
        return 0


def normalize_service(name):
    name = str(name or "").strip().replace("螨", "蟎")

    mapping = {
        "VIP": "儲值金",
        "冷氣機清潔": "冷氣清潔",
        "冷氣機清潔服務": "冷氣清潔",
        "洗衣機": "洗衣機清潔",
        "洗衣機清潔": "洗衣機清潔",
        "沙發床墊水洗除蟎": "水洗",
        "沙發床墊水洗除螨": "水洗",
        "沙發清洗": "水洗",
        "床墊清洗": "水洗",
        "整理收納": "收納",
    }
    return mapping.get(name, name)


def detect_income_type(first_header):
    first_header = str(first_header or "").strip()
    if first_header in ("VIP", "儲值金"):
        return "儲值金"
    return "現金收入"


def normalize_date_text(text: str) -> Optional[str]:
    txt = str(text or "").strip()
    if not txt:
        return None

    txt = txt.replace("年", "-").replace("月", "-").replace("日", "")
    txt = txt.replace("/", "-").replace(".", "-")
    txt = " ".join(txt.split())

    import re

    patterns = [
        r"(20\d{2}-\d{1,2}-\d{1,2})",
        r"(20\d{6})",
        r"(\d{4}/\d{1,2}/\d{1,2})",
        r"(\d{4}\.\d{1,2}\.\d{1,2})",
        r"(\d{1,2}-\d{1,2})",
        r"(\d{1,2}/\d{1,2})",
    ]

    for p in patterns:
        m = re.search(p, txt)
        if not m:
            continue

        raw = m.group(1)
        try:
            if re.fullmatch(r"20\d{2}-\d{1,2}-\d{1,2}", raw):
                dt = datetime.strptime(raw, "%Y-%m-%d")
                return dt.strftime("%Y-%m-%d")
            if re.fullmatch(r"20\d{6}", raw):
                dt = datetime.strptime(raw, "%Y%m%d")
                return dt.strftime("%Y-%m-%d")
            if re.fullmatch(r"\d{4}/\d{1,2}/\d{1,2}", raw):
                dt = datetime.strptime(raw, "%Y/%m/%d")
                return dt.strftime("%Y-%m-%d")
            if re.fullmatch(r"\d{4}\.\d{1,2}\.\d{1,2}", raw):
                dt = datetime.strptime(raw, "%Y.%m.%d")
                return dt.strftime("%Y-%m-%d")
            if re.fullmatch(r"\d{1,2}-\d{1,2}", raw):
                today = now_dt()
                dt = datetime.strptime(f"{today.year}-{raw}", "%Y-%m-%d")
                return dt.strftime("%Y-%m-%d")
            if re.fullmatch(r"\d{1,2}/\d{1,2}", raw):
                today = now_dt()
                dt = datetime.strptime(f"{today.year}/{raw}", "%Y/%m/%d")
                return dt.strftime("%Y-%m-%d")
        except Exception:
            pass

    return None


def parse_html(html, payment_status=None):
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    results = []

    date_candidates = ["服務日期", "清潔日期", "日期", "預約日期", "服務日", "clean_date"]

    for table in tables:
        trs = table.find_all("tr")
        rows = []

        for tr in trs:
            cells = tr.find_all(["th", "td"])
            row = [c.get_text(" ", strip=True) for c in cells]
            if any(str(x).strip() for x in row):
                rows.append(row)

        if not rows:
            continue

        header = [str(x).strip() for x in rows[0]]

        if "已付款金額" not in header and "待付款金額" not in header:
            continue

        paid_idx = header.index("已付款金額") if "已付款金額" in header else None
        unpaid_idx = header.index("待付款金額") if "待付款金額" in header else None
        weekend_idx = header.index("週末加價") if "週末加價" in header else None

        date_idx = None
        for name in date_candidates:
            if name in header:
                date_idx = header.index(name)
                break

        income_type = detect_income_type(header[0] if header else "")
        source = "儲值金表" if income_type == "儲值金" else "主表"

        for row in rows[1:]:
            if not row:
                continue

            service = normalize_service(row[0] if len(row) > 0 else "")
            if not service or service == "加總" or service.startswith("LC"):
                continue

            paid = safe_int(row[paid_idx]) if paid_idx is not None and len(row) > paid_idx else 0
            unpaid = safe_int(row[unpaid_idx]) if unpaid_idx is not None and len(row) > unpaid_idx else 0
            if income_type == "儲值金" and weekend_idx is not None and len(row) > weekend_idx:
                weekend_amount = safe_int(row[weekend_idx])
                if str(payment_status) == "0":
                    unpaid += weekend_amount
                else:
                    paid += weekend_amount

            service_date = None
            if date_idx is not None and len(row) > date_idx:
                service_date = normalize_date_text(row[date_idx])

            results.append({
                "收入類型": income_type,
                "資料來源": source,
                "服務": service,
                "子項目": "",
                "日期": service_date,
                "已付款": paid,
                "待付款": unpaid,
            })

    log(f"✅ parse_html rows = {len(results)}")
    return results


def to_category(service, income) -> Optional[str]:
    if service == "儲值金" and income == "現金收入":
        return "儲值金"
    if service in ["居家清潔", "辦公室清潔", "裝修細清", "搬入清潔", "搬出清潔", "大掃除"]:
        return "清潔"
    if service == "冷氣清潔":
        return "冷氣"
    if service == "洗衣機清潔":
        return "洗衣機"
    if service == "水洗":
        return "水洗"
    if service == "收納":
        return "收納"
    return None


def build_region1_df(raw_df: pd.DataFrame) -> pd.DataFrame:
    work = raw_df.copy()

    work["本月已付款"] = 0
    work["本月待付款"] = 0
    work["下月已付款"] = 0
    work["下月待付款"] = 0

    this_mask = work["月份"] == "本月"
    next_mask = work["月份"] == "下月"

    work.loc[this_mask, "本月已付款"] = work.loc[this_mask, "已付款"]
    work.loc[this_mask, "本月待付款"] = work.loc[this_mask, "待付款"]
    work.loc[next_mask, "下月已付款"] = work.loc[next_mask, "已付款"]
    work.loc[next_mask, "下月待付款"] = work.loc[next_mask, "待付款"]

    region1 = (
        work.groupby(["城市", "收入類型", "資料來源", "服務", "子項目"], as_index=False)[
            ["本月已付款", "本月待付款", "下月已付款", "下月待付款"]
        ]
        .sum()
    )

    region1["城市"] = pd.Categorical(region1["城市"], categories=CITY_ORDER, ordered=True)
    region1["收入類型"] = pd.Categorical(region1["收入類型"], categories=INCOME_ORDER, ordered=True)
    region1 = region1.sort_values(["城市", "收入類型", "服務"]).reset_index(drop=True)

    region1["城市"] = region1["城市"].astype(str)
    region1["收入類型"] = region1["收入類型"].astype(str)
    return region1


def build_region2_df(raw_df: pd.DataFrame) -> pd.DataFrame:
    work = raw_df.copy()
    work["類別"] = work.apply(lambda r: to_category(r["服務"], r["收入類型"]), axis=1)
    work = work[work["類別"].notna()].copy()

    rows = []
    for city in CITY_ORDER:
        for income in INCOME_ORDER:
            for category in CATEGORY_ORDER:
                sub = work[
                    (work["城市"] == city) &
                    (work["收入類型"] == income) &
                    (work["類別"] == category)
                ]

                bm = sub[sub["月份"] == "本月"]
                nm = sub[sub["月份"] == "下月"]

                rows.append({
                    "城市": city,
                    "收入類型": income,
                    "類別": category,
                    "本月待付": bm["待付款"].sum(),
                    "本月已付": bm["已付款"].sum(),
                    "本月加總": bm["已付款"].sum() + bm["待付款"].sum(),
                    "次月待付": nm["待付款"].sum(),
                    "次月已付": nm["已付款"].sum(),
                    "次月加總": nm["已付款"].sum() + nm["待付款"].sum(),
                })

    return pd.DataFrame(rows)


def validate_region2_totals(raw_df: pd.DataFrame, region2_df: pd.DataFrame) -> None:
    work = raw_df.copy()
    work["類別"] = work.apply(lambda r: to_category(r["服務"], r["收入類型"]), axis=1)
    work = work[work["類別"].notna()].copy()

    expected = (
        work.groupby(["城市", "收入類型", "類別", "月份"], as_index=False)[["已付款", "待付款"]]
        .sum()
    )

    expected_rows = []
    for city in CITY_ORDER:
        for income in INCOME_ORDER:
            for category in CATEGORY_ORDER:
                sub = expected[
                    (expected["城市"] == city) &
                    (expected["收入類型"] == income) &
                    (expected["類別"] == category)
                ]
                bm = sub[sub["月份"] == "本月"]
                nm = sub[sub["月份"] == "下月"]
                expected_rows.append({
                    "城市": city,
                    "收入類型": income,
                    "類別": category,
                    "本月待付": int(bm["待付款"].sum()),
                    "本月已付": int(bm["已付款"].sum()),
                    "本月加總": int(bm["已付款"].sum() + bm["待付款"].sum()),
                    "次月待付": int(nm["待付款"].sum()),
                    "次月已付": int(nm["已付款"].sum()),
                    "次月加總": int(nm["已付款"].sum() + nm["待付款"].sum()),
                })

    expected_df = pd.DataFrame(expected_rows)
    check_cols = ["本月待付", "本月已付", "本月加總", "次月待付", "次月已付", "次月加總"]
    merged_check = expected_df.merge(
        region2_df,
        on=["城市", "收入類型", "類別"],
        how="outer",
        suffixes=("_raw", "_df2"),
    )

    mismatches = []
    for _, row in merged_check.iterrows():
        for col in check_cols:
            raw_val = safe_int(row.get(f"{col}_raw", 0))
            df2_val = safe_int(row.get(f"{col}_df2", 0))
            if raw_val != df2_val:
                mismatches.append(
                    f"{row.get('城市')} / {row.get('收入類型')} / {row.get('類別')} / {col}: raw={raw_val}, df2={df2_val}"
                )

    if mismatches:
        raise RuntimeError("df2 分類後彙總與 raw_df 不一致：" + "；".join(mismatches[:20]))

    stored_clean = region2_df[
        (region2_df["收入類型"] == "儲值金") &
        (region2_df["類別"] == "清潔")
    ][["城市", "本月已付", "本月加總", "次月已付", "次月加總"]]
    log("✅ df2 對帳完成；儲值金/清潔 = " + stored_clean.to_dict("records").__repr__())


def build_region3_df(region2_df: pd.DataFrame) -> pd.DataFrame:
    rows = []

    for city in CITY_ORDER:
        city_df = region2_df[region2_df["城市"] == city].copy()

        level1 = city_df[["城市", "類別", "收入類型", "本月加總", "次月加總"]].copy()
        level1["加總類型"] = "加總1"
        rows.extend(level1.to_dict("records"))

        mapping_level2 = {
            "清潔現金+儲值金": ["清潔"],
            "家電現金+儲值金": ["冷氣", "洗衣機"],
            "水洗/收納現金+儲值金": ["水洗", "收納"],
        }

        for new_cat, old_cats in mapping_level2.items():
            tmp = city_df[city_df["類別"].isin(old_cats)]
            rows.append({
                "城市": city,
                "類別": new_cat,
                "收入類型": "現金+儲值金",
                "本月加總": tmp["本月加總"].sum(),
                "次月加總": tmp["次月加總"].sum(),
                "加總類型": "加總2",
            })

        mapping_level3 = {
            "清潔+水洗+收納現金+儲值金": ["清潔", "水洗", "收納"],
            "家電現金+儲值金": ["冷氣", "洗衣機"],
        }

        for new_cat, old_cats in mapping_level3.items():
            tmp = city_df[city_df["類別"].isin(old_cats)]
            rows.append({
                "城市": city,
                "類別": new_cat,
                "收入類型": "現金+儲值金",
                "本月加總": tmp["本月加總"].sum(),
                "次月加總": tmp["次月加總"].sum(),
                "加總類型": "加總3",
            })

    region3 = pd.DataFrame(rows)
    type_order = ["加總1", "加總2", "加總3"]

    region3["城市"] = pd.Categorical(region3["城市"], categories=CITY_ORDER, ordered=True)
    region3["加總類型"] = pd.Categorical(region3["加總類型"], categories=type_order, ordered=True)
    region3["類別"] = pd.Categorical(region3["類別"], categories=REGION3_CATEGORY_ORDER, ordered=True)

    region3 = region3.sort_values(["城市", "加總類型", "類別", "收入類型"]).reset_index(drop=True)
    region3["城市"] = region3["城市"].astype(str)
    region3["類別"] = region3["類別"].astype(str)
    region3["收入類型"] = region3["收入類型"].astype(str)
    region3["加總類型"] = region3["加總類型"].astype(str)
    return region3


def build_region4_df(region2_df: pd.DataFrame) -> pd.DataFrame:
    rows = []

    for city in CITY_ORDER:
        city_df = region2_df[region2_df["城市"] == city].copy()

        appliance_df = city_df[city_df["類別"].isin(["冷氣", "洗衣機"])]
        bm_appliance = appliance_df["本月加總"].sum()
        nm_appliance = appliance_df["次月加總"].sum()

        cash_stored_df = city_df[
            (city_df["收入類型"] == "現金收入") &
            (city_df["類別"] == "儲值金")
        ]
        bm_cash_stored = cash_stored_df["本月加總"].sum()
        nm_cash_stored = cash_stored_df["次月加總"].sum()

        # 本月/次月加總：清潔服務業績。
        # 規則：
        # 1) 不論收入類型是「現金收入」或「儲值金」，都必須先依服務分類。
        # 2) 只有「清潔」類別納入本月/次月加總。
        # 3) 儲值金中的冷氣、洗衣機屬於家電，只放在「家電加總」，不放進本月/次月加總。
        # 4) 儲值金中的水洗屬於水洗，不放進本月/次月加總。
        # 5) 現金收入的「儲值金購買」是預收款，不屬於服務業績，不放進本月/次月加總。
        total_df = city_df[city_df["類別"] == "清潔"]

        rows.append({
            "城市": city,
            "本月加總": total_df["本月加總"].sum(),
            "次月加總": total_df["次月加總"].sum(),
            "本月家電加總": bm_appliance,
            "次月家電加總": nm_appliance,
            "儲值金": bm_cash_stored + nm_cash_stored,
        })

    region4 = pd.DataFrame(rows)
    bm_sum = region4["本月加總"].sum()
    nm_sum = region4["次月加總"].sum()

    region4["本月佔比"] = 0 if bm_sum == 0 else region4["本月加總"] / bm_sum
    region4["次月佔比"] = 0 if nm_sum == 0 else region4["次月加總"] / nm_sum

    total_row = pd.DataFrame([{
        "城市": "加總",
        "本月加總": bm_sum,
        "本月佔比": 1,
        "次月加總": nm_sum,
        "次月佔比": 1,
        "本月家電加總": region4["本月家電加總"].sum(),
        "次月家電加總": region4["次月家電加總"].sum(),
        "儲值金": region4["儲值金"].sum(),
    }])

    region4 = pd.concat([region4, total_row], ignore_index=True)

    return region4[[
        "城市",
        "本月加總",
        "本月佔比",
        "次月加總",
        "次月佔比",
        "本月家電加總",
        "次月家電加總",
        "儲值金",
    ]]


def _build_period_overview_df(
    df4: pd.DataFrame,
    source: str,
    amount_col: str,
    ratio_col: str,
    latest_filename: str,
    period_label: str,
    run_dt: Optional[datetime] = None,
) -> pd.DataFrame:
    cols = [
        "id",
        "來源",
        "統計月份",
        "日期",
        "台北業績", "台北佔比",
        "台中業績", "台中佔比",
        "桃園業績", "桃園佔比",
        "新竹業績", "新竹佔比",
        "高雄業績", "高雄佔比",
        "全區合計",
    ]

    if df4 is None or df4.empty:
        log(f"⚠️ _build_period_overview_df：df4 為空，period={period_label}")
        return pd.DataFrame(columns=cols)

    latest_path = os.path.join(LATEST_DIR, latest_filename)
    now_obj = run_dt or now_dt()
    row_id = f"{now_obj.strftime('%Y%m%d%H%M%S')}_{period_label}"
    date_text = now_obj.strftime("%Y/%m/%d %H:%M:%S")

    if period_label == "次月":
        y, m = now_obj.year, now_obj.month
        if m == 12:
            stat_month = f"{y + 1}/01"
        else:
            stat_month = f"{y}/{m + 1:02d}"
    else:
        stat_month = now_obj.strftime("%Y/%m")

    def get_val(city, col):
        try:
            row = df4[df4["城市"] == city]
            if row.empty or col not in row.columns:
                return 0
            return row.iloc[0][col]
        except Exception:
            return 0

    if os.path.exists(latest_path):
        try:
            old_df = pd.read_csv(latest_path, encoding="utf-8-sig")
        except Exception:
            old_df = pd.DataFrame(columns=cols)
    else:
        old_df = pd.DataFrame(columns=cols)

    for c in cols:
        if c not in old_df.columns:
            old_df[c] = ""

    new_row = {
        "id": row_id,
        "來源": source,
        "統計月份": stat_month,
        "日期": date_text,
        "台北業績": get_val("台北", amount_col),
        "台北佔比": get_val("台北", ratio_col),
        "台中業績": get_val("台中", amount_col),
        "台中佔比": get_val("台中", ratio_col),
        "桃園業績": get_val("桃園", amount_col),
        "桃園佔比": get_val("桃園", ratio_col),
        "新竹業績": get_val("新竹", amount_col),
        "新竹佔比": get_val("新竹", ratio_col),
        "高雄業績": get_val("高雄", amount_col),
        "高雄佔比": get_val("高雄", ratio_col),
        "全區合計": get_val("加總", amount_col),
    }

    # 當月/次月追蹤採「一直保留」策略：
    # 每次更新會把上方各區月度摘要 df4 的本月/次月數字各新增一筆；
    # 舊紀錄不會因月初換月被清空，除非在 OP app 畫面勾選刪除。
    out = pd.concat([old_df[cols], pd.DataFrame([new_row])], ignore_index=True)

    # 保留既有歷史資料，不因舊資料缺少「統計月份」欄位而被清掉。
    # 若要刪除舊紀錄，請在 OP app 介面勾選刪除。
    out["_sort_dt"] = pd.to_datetime(out["日期"], errors="coerce")
    out = out.sort_values(["_sort_dt", "id"], ascending=[False, False]).drop(columns=["_sort_dt"])
    out = out.reset_index(drop=True)

    # 2026-11-24 Jenny：行事曆要看結果——印各區業績金額與合計（原本印歷史筆數，看不出內容）
    log(f"✅ {period_label}（{stat_month}）業績：" + "／".join(
        f"{city} {_money(new_row[city + '業績'])}" for city in ("台北", "台中", "桃園", "新竹", "高雄"))
        + f"；合計 {_money(new_row['全區合計'])}")
    return out[cols]


def _money(value) -> str:
    try:
        return f"{int(round(float(value))):,}"
    except (TypeError, ValueError):
        return str(value or 0)


def build_daily_overview_df(df4: pd.DataFrame, source: str = "dashboard", run_dt: Optional[datetime] = None) -> pd.DataFrame:
    return _build_period_overview_df(
        df4=df4,
        source=source,
        amount_col="本月加總",
        ratio_col="本月佔比",
        latest_filename="daily_df.csv",
        period_label="本月",
        run_dt=run_dt,
    )


def build_next_month_overview_df(df4: pd.DataFrame, source: str = "dashboard", run_dt: Optional[datetime] = None) -> pd.DataFrame:
    return _build_period_overview_df(
        df4=df4,
        source=source,
        amount_col="次月加總",
        ratio_col="次月佔比",
        latest_filename="next_month_daily_df.csv",
        period_label="次月",
        run_dt=run_dt,
    )


def build_month_end_summary_df(df4: pd.DataFrame, source: str = "dashboard") -> pd.DataFrame:
    cols = ["id", "來源", "快照日期", "城市", "當月業績", "當月佔比", "次月業績", "次月佔比", "當月總業績", "次月總業績"]

    if df4 is None or df4.empty:
        return pd.DataFrame(columns=cols)

    now_obj = now_dt()
    month_last_day = calendar.monthrange(now_obj.year, now_obj.month)[1]
    # 月底快照：只在每月最後一天「執行更新資料/排程更新」時產生。
    # 若當天更新多次，會以快照日期覆蓋同一天的舊快照，避免月底快照重複。
    if now_obj.day != month_last_day:
        return pd.DataFrame(columns=cols)

    snapshot_date = now_obj.strftime("%Y/%m/%d")
    row_id_prefix = now_obj.strftime("%Y%m%d")

    def get_val(city, col):
        try:
            row = df4[df4["城市"] == city]
            if row.empty or col not in row.columns:
                return 0
            return row.iloc[0][col]
        except Exception:
            return 0

    current_total = get_val("加總", "本月加總")
    next_total = get_val("加總", "次月加總")

    rows = []
    for city in CITY_ORDER + ["加總"]:
        rows.append({
            "id": f"{row_id_prefix}_{city}",
            "來源": source,
            "快照日期": snapshot_date,
            "城市": city,
            "當月業績": get_val(city, "本月加總"),
            "當月佔比": get_val(city, "本月佔比"),
            "次月業績": get_val(city, "次月加總"),
            "次月佔比": get_val(city, "次月佔比"),
            "當月總業績": current_total,
            "次月總業績": next_total,
        })

    out = pd.DataFrame(rows, columns=cols)

    if os.path.exists(MONTH_END_HISTORY_FILE):
        try:
            old_df = pd.read_csv(MONTH_END_HISTORY_FILE, encoding="utf-8-sig")
        except Exception:
            old_df = pd.DataFrame(columns=cols)
    else:
        old_df = pd.DataFrame(columns=cols)

    for c in cols:
        if c not in old_df.columns:
            old_df[c] = ""

    old_df = old_df[old_df["快照日期"].astype(str) != snapshot_date].copy()
    history_df = pd.concat([old_df[cols], out], ignore_index=True)
    history_df.to_csv(MONTH_END_HISTORY_FILE, index=False, encoding="utf-8-sig")
    append_output_file_log("月底快照", MONTH_END_HISTORY_FILE, source)

    month_folder = os.path.join(SNAPSHOT_DIR, now_obj.strftime("%Y%m"))
    os.makedirs(month_folder, exist_ok=True)
    snap_path = os.path.join(month_folder, f"{now_obj.strftime('%Y%m%d')}_month_end_summary.csv")
    out.to_csv(snap_path, index=False, encoding="utf-8-sig")
    append_output_file_log("月底快照", snap_path, source)

    log(f"✅ 月底快照已記錄：{snapshot_date}")
    return out


def load_month_end_history() -> pd.DataFrame:
    ensure_dirs()
    cols = ["id", "來源", "快照日期", "城市", "當月業績", "當月佔比", "次月業績", "次月佔比", "當月總業績", "次月總業績"]
    if not os.path.exists(MONTH_END_HISTORY_FILE):
        return pd.DataFrame(columns=cols)
    return pd.read_csv(MONTH_END_HISTORY_FILE, encoding="utf-8-sig")

def format_region4_for_display(df4: pd.DataFrame) -> pd.DataFrame:
    out = df4.copy()
    for col in ["本月加總", "次月加總", "本月家電加總", "次月家電加總", "儲值金"]:
        if col in out.columns:
            out[col] = out[col].apply(lambda x: int(x) if pd.notna(x) else 0)
    return out


def build_region4_email_html(df4):
    mail_df = df4.copy()

    for col in ["本月加總", "次月加總", "本月家電加總", "次月家電加總", "儲值金"]:
        if col in mail_df.columns:
            mail_df[col] = mail_df[col].apply(lambda x: f"{int(x):,}" if pd.notna(x) else "")

    if "本月佔比" in mail_df.columns:
        mail_df["本月佔比"] = mail_df["本月佔比"].apply(lambda x: f"{x:.2%}" if pd.notna(x) else "")

    if "次月佔比" in mail_df.columns:
        mail_df["次月佔比"] = mail_df["次月佔比"].apply(lambda x: f"{x:.2%}" if pd.notna(x) else "")

    html_table = mail_df.to_html(index=False, border=0)

    return f"""
    <html>
      <head>
        <style>
          table {{
            border-collapse: collapse;
            font-family: Arial, sans-serif;
            font-size: 14px;
          }}
          th, td {{
            border: 1px solid #999;
            padding: 6px 10px;
          }}
          th {{
            background-color: #f2f2f2;
            text-align: center;
          }}
          td {{
            text-align: right;
          }}
          td:first-child {{
            text-align: left;
          }}
        </style>
      </head>
      <body>
        <p>您好，以下為業績報表：</p>
        {html_table}
      </body>
    </html>
    """


def split_email_recipients(raw: str) -> list[str]:
    return [
        email.strip()
        for email in str(raw or "").replace(";", ",").split(",")
        if email.strip()
    ]


def get_email_settings(default_recipient="jenny@hers.com.tw"):
    sender = get_secret(
        ["email", "sender"],
        env_name="NOTIFY_EMAIL",
        fallback_env_names=["REPORT_EMAIL_SENDER", "EMAIL_SENDER"],
        required=False,
    )
    password = get_secret(
        ["email", "app_password"],
        env_name="NOTIFY_PASSWORD",
        fallback_env_names=["REPORT_EMAIL_APP_PASSWORD", "EMAIL_APP_PASSWORD"],
        required=False,
    )
    recipient = get_secret(
        ["email", "recipient"],
        env_name="NOTIFY_TO",
        fallback_env_names=["REPORT_EMAIL_RECIPIENT", "EMAIL_RECIPIENT"],
        required=False,
        default=default_recipient,
    )

    return sender, password, split_email_recipients(recipient)


def email_settings_ready() -> bool:
    sender, password, recipients = get_email_settings()
    return bool(sender and password and recipients)


def send_region4_email(df4, recipient="jenny@hers.com.tw", required=False) -> bool:
    sender, password, recipients = get_email_settings(recipient)

    missing = []
    if not sender:
        missing.append("REPORT_EMAIL_SENDER 或 NOTIFY_EMAIL")
    if not password:
        missing.append("REPORT_EMAIL_APP_PASSWORD 或 NOTIFY_PASSWORD")
    if not recipients:
        missing.append("REPORT_EMAIL_RECIPIENT 或 NOTIFY_TO")

    if missing:
        message = "缺少寄信設定：" + " / ".join(missing)
        if required:
            raise RuntimeError(message)
        log(f"⚠️ {message}，略過寄信")
        return False

    today_str = now_dt().strftime("%Y%m%d")
    subject = f"業績報表{today_str}"
    html = build_region4_email_html(df4)

    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender, password)
            server.sendmail(sender, recipients, msg.as_string())
    except smtplib.SMTPAuthenticationError as exc:
        message = (
            "Gmail 拒絕登入，請確認 email.sender / REPORT_EMAIL_SENDER 與 "
            "email.app_password / REPORT_EMAIL_APP_PASSWORD 是同一個 Gmail 帳號的 app password"
        )
        if required:
            raise RuntimeError(message) from exc
        log(f"⚠️ {message}，本次略過寄信")
        return False
    except smtplib.SMTPException as exc:
        message = f"SMTP 寄信失敗：{exc}"
        if required:
            raise RuntimeError(message) from exc
        log(f"⚠️ {message}，本次略過寄信")
        return False

    log(f"✅ 已寄出：{', '.join(recipients)}")
    return True


def load_execution_log_for_current_month() -> pd.DataFrame:
    return pd.DataFrame()


def delete_execution_log_rows(ids):
    return 0


def append_daily_overview_history(daily_df: pd.DataFrame, trigger: str):
    return None


def load_daily_history_for_current_month() -> pd.DataFrame:
    return pd.DataFrame()


def delete_daily_history_rows(ids):
    return 0


def append_output_file_log(category: str, file_path: str, trigger: str):
    ensure_dirs()

    row = {
        "id": now_dt().strftime("%Y%m%d%H%M%S%f"),
        "時間": now_dt().strftime("%Y-%m-%d %H:%M:%S"),
        "分類": category,
        "檔名": os.path.basename(file_path),
        "完整路徑": file_path,
        "trigger": trigger,
    }

    new_df = pd.DataFrame([row])

    if os.path.exists(OUTPUT_LOG_FILE):
        old_df = pd.read_csv(OUTPUT_LOG_FILE, encoding="utf-8-sig")
        out_df = pd.concat([old_df, new_df], ignore_index=True)
    else:
        out_df = new_df

    out_df.to_csv(OUTPUT_LOG_FILE, index=False, encoding="utf-8-sig")


def load_output_file_log() -> pd.DataFrame:
    ensure_dirs()
    if not os.path.exists(OUTPUT_LOG_FILE):
        return pd.DataFrame(columns=["id", "時間", "分類", "檔名", "完整路徑", "trigger"])
    return pd.read_csv(OUTPUT_LOG_FILE, encoding="utf-8-sig")


def persist_dashboard_payload(
    df4: pd.DataFrame,
    daily_df: pd.DataFrame,
    next_month_daily_df: pd.DataFrame,
    month_end_df: pd.DataFrame,
    email_html: str,
    error_msg: Optional[str] = None,
    trigger: str = "dashboard",
):
    ensure_dirs()

    now = now_dt()
    stamp = now.strftime("%Y%m%d_%H%M%S")
    month_folder = os.path.join(SNAPSHOT_DIR, now.strftime("%Y%m"))
    os.makedirs(month_folder, exist_ok=True)

    latest_df4 = os.path.join(LATEST_DIR, "df4.csv")
    latest_daily = os.path.join(LATEST_DIR, "daily_df.csv")
    latest_next_daily = os.path.join(LATEST_DIR, "next_month_daily_df.csv")
    latest_month_end = os.path.join(LATEST_DIR, "month_end_summary.csv")
    latest_html = os.path.join(LATEST_DIR, "email_preview.html")
    latest_meta = os.path.join(LATEST_DIR, "meta.json")

    log("===== 寫入 dashboard 檔案 =====")
    log(f"LATEST_DIR = {LATEST_DIR}")
    log(f"latest_df4 = {latest_df4}")
    log(f"latest_daily = {latest_daily}")
    log(f"latest_next_daily = {latest_next_daily}")
    log(f"latest_month_end = {latest_month_end}")
    log(f"latest_html = {latest_html}")
    log(f"latest_meta = {latest_meta}")
    log(f"df4 rows = {len(df4)}")

    # IMPORTANT: 每次 generate_sales_report() 被執行，都必須 append 本月與次月各一列。
    # 這裡使用 generate_sales_report() 已經依照上方摘要 df4 建好的 daily_df / next_month_daily_df，
    # 不再第二次重算，避免同一次更新出現兩種時間戳或畫面端再補寫造成不同步。

    log(f"daily_df rows = {len(daily_df)}")
    log(f"next_month_daily_df rows = {len(next_month_daily_df)}")
    log(f"month_end_df rows = {len(month_end_df)}")

    df4.to_csv(latest_df4, index=False, encoding="utf-8-sig")
    append_output_file_log("業績報表", latest_df4, trigger)

    daily_df.to_csv(latest_daily, index=False, encoding="utf-8-sig")
    append_output_file_log("業績報表", latest_daily, trigger)

    next_month_daily_df.to_csv(latest_next_daily, index=False, encoding="utf-8-sig")
    append_output_file_log("次月統計報表", latest_next_daily, trigger)

    if month_end_df is not None and not month_end_df.empty:
        month_end_df.to_csv(latest_month_end, index=False, encoding="utf-8-sig")
        append_output_file_log("月底快照", latest_month_end, trigger)

    with open(latest_html, "w", encoding="utf-8") as f:
        f.write(email_html or "")
    append_output_file_log("業績報表", latest_html, trigger)

    meta = {
        "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "df4_rows": int(len(df4)),
        "daily_rows": int(len(daily_df)),
        "next_month_daily_rows": int(len(next_month_daily_df)),
        "month_end_rows": int(len(month_end_df)),
        "error": error_msg,
        "trigger": trigger,
    }
    with open(latest_meta, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    append_output_file_log("業績報表", latest_meta, trigger)

    snapshot_prefix = os.path.join(month_folder, stamp)

    snap_df4 = f"{snapshot_prefix}_df4.csv"
    snap_daily = f"{snapshot_prefix}_daily_df.csv"
    snap_next_daily = f"{snapshot_prefix}_next_month_daily_df.csv"
    snap_month_end = f"{snapshot_prefix}_month_end_summary.csv"
    snap_meta = f"{snapshot_prefix}_meta.json"
    snap_html = f"{snapshot_prefix}_email_preview.html"

    df4.to_csv(snap_df4, index=False, encoding="utf-8-sig")
    append_output_file_log("業績報表", snap_df4, trigger)

    daily_df.to_csv(snap_daily, index=False, encoding="utf-8-sig")
    append_output_file_log("業績報表", snap_daily, trigger)

    next_month_daily_df.to_csv(snap_next_daily, index=False, encoding="utf-8-sig")
    append_output_file_log("次月統計報表", snap_next_daily, trigger)

    if month_end_df is not None and not month_end_df.empty:
        month_end_df.to_csv(snap_month_end, index=False, encoding="utf-8-sig")
        append_output_file_log("月底快照", snap_month_end, trigger)

    with open(snap_meta, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    append_output_file_log("業績報表", snap_meta, trigger)

    with open(snap_html, "w", encoding="utf-8") as f:
        f.write(email_html or "")
    append_output_file_log("業績報表", snap_html, trigger)


def generate_sales_report(send_email=False, persist_dashboard=True, trigger="dashboard", strict_accounts=False):
    log("🔥 開始業績報表")

    ensure_dirs()
    (m_start, m_end), (n_start, n_end) = get_ranges()
    merged = {}
    city_errors = []

    enabled_cities = [city for city in CITY_ORDER if city in ACCOUNTS]
    missing_cities = [city for city in CITY_ORDER if city not in ACCOUNTS]

    if missing_cities:
        log(f"⚠️ ACCOUNTS 缺少城市設定，已略過：{', '.join(missing_cities)}")
        if strict_accounts:
            raise RuntimeError("缺少城市帳號設定：" + "、".join(missing_cities))

    if not enabled_cities:
        error_msg = "ACCOUNTS 沒有任何可用城市設定"
        log(f"❌ {error_msg}")
        log("⚠️ 本次不覆蓋 latest，保留舊資料")

        return {
            "raw_df": pd.DataFrame(),
            "df1": pd.DataFrame(),
            "df2": pd.DataFrame(),
            "df3": pd.DataFrame(),
            "df4": pd.DataFrame(),
            "daily_df": pd.DataFrame(),
            "next_month_daily_df": pd.DataFrame(),
            "month_end_df": pd.DataFrame(),
            "month_end_history_df": load_month_end_history(),
            "email_html": "",
            "updated_at": now_dt().strftime("%Y-%m-%d %H:%M:%S"),
            "execution_log_df": pd.DataFrame(),
            "daily_history_df": pd.DataFrame(),
            "output_file_log_df": load_output_file_log(),
             "error": error_msg,
        }


    def fetch_city(city):
        city_merged = {}
        errors = []
        log(f"===== {city} =====")
        session = requests.Session()
        acc = ACCOUNTS[city]

        try:
            login(session, acc["email"], acc["password"])
            city_row_count = 0

            for label, (s, e) in {
                "本月": (m_start, m_end),
                "下月": (n_start, n_end),
            }.items():
                for status in [1, 0]:
                    for kw in get_keywords(city):
                        url = build_url(s, e, status, kw)
                        log(f"抓取：city={city} month={label} status={status} kw={kw} url={url}")

                        res = session.get(url, headers=HEADERS, allow_redirects=True)
                        res.raise_for_status()

                        rows = parse_html(res.text, payment_status=status)
                        city_row_count += len(rows)

                        if not rows:
                            log(f"⚠️ {city} / {label} / status={status} / kw={kw} 沒抓到資料，HTML 長度={len(res.text)}")
                            try:
                                debug_dir = os.path.join(DASHBOARD_DIR, "_debug_html")
                                os.makedirs(debug_dir, exist_ok=True)
                                debug_name = f"{city}_{label}_status{status}_{(kw or 'ALL')}.html"
                                debug_path = os.path.join(debug_dir, debug_name)
                                with open(debug_path, "w", encoding="utf-8") as f:
                                    f.write(res.text)
                                log(f"📝 已輸出 debug html：{debug_path}")
                                append_output_file_log("業績報表", debug_path, trigger)
                            except Exception as dbg_e:
                                log(f"⚠️ debug html 寫出失敗：{dbg_e}")

                        for row in rows:
                            key = (
                                city,
                                label,
                                row["日期"],
                                row["收入類型"],
                                row["資料來源"],
                                row["服務"],
                                row["子項目"],
                            )

                            if key not in city_merged:
                                city_merged[key] = {
                                    "城市": city,
                                    "月份": label,
                                    "日期": row["日期"],
                                    "收入類型": row["收入類型"],
                                    "資料來源": row["資料來源"],
                                    "服務": row["服務"],
                                    "子項目": row["子項目"],
                                    "已付款": 0,
                                    "待付款": 0,
                                }

                            city_merged[key]["已付款"] += row["已付款"]
                            city_merged[key]["待付款"] += row["待付款"]

            if city_row_count == 0:
                msg = f"{city}：登入成功，但沒有抓到任何表格資料"
                errors.append(msg)
                log(f"⚠️ {msg}")

        except Exception as e:
            msg = f"{city} 失敗：{e}"
            errors.append(msg)
            log(f"❌ {msg}")

        return city_merged, errors

    with ThreadPoolExecutor(max_workers=min(5, len(enabled_cities))) as executor:
        futures = {city: executor.submit(fetch_city, city) for city in enabled_cities}
        for city in enabled_cities:
            city_merged, errors = futures[city].result()
            merged.update(city_merged)
            city_errors.extend(errors)

    raw_df = pd.DataFrame(merged.values())

    if raw_df.empty:
        error_msg = "沒有任何資料可輸出"
        if city_errors:
            error_msg += "；" + " / ".join(city_errors)

        log(f"⚠️ {error_msg}")
        log("⚠️ 本次不覆蓋 latest，保留舊資料")

        return {
            "raw_df": pd.DataFrame(),
            "df1": pd.DataFrame(),
            "df2": pd.DataFrame(),
            "df3": pd.DataFrame(),
            "df4": pd.DataFrame(),
            "daily_df": pd.DataFrame(),
            "next_month_daily_df": pd.DataFrame(),
            "month_end_df": pd.DataFrame(),
            "month_end_history_df": load_month_end_history(),
            "email_html": "",
            "updated_at": now_dt().strftime("%Y-%m-%d %H:%M:%S"),
            "execution_log_df": pd.DataFrame(),
            "daily_history_df": pd.DataFrame(),
            "output_file_log_df": load_output_file_log(),
            "error": error_msg,
        }


    df1 = build_region1_df(raw_df)
    df2 = build_region2_df(raw_df)
    validate_region2_totals(raw_df, df2)
    df3 = build_region3_df(df2)
    df4 = build_region4_df(df2)

    raw_df.to_csv(os.path.join(LATEST_DIR, "raw_df.csv"), index=False, encoding="utf-8-sig")
    df1.to_csv(os.path.join(LATEST_DIR, "df1.csv"), index=False, encoding="utf-8-sig")
    df2.to_csv(os.path.join(LATEST_DIR, "df2.csv"), index=False, encoding="utf-8-sig")
    df3.to_csv(os.path.join(LATEST_DIR, "df3.csv"), index=False, encoding="utf-8-sig")

    hour = now_dt().hour

    if trigger == "schedule":
        if hour == 8:
            source = "schedule-08"
        elif hour == 18:
            source = "schedule-18"
        elif hour == 0:
            source = "schedule-00"
        else:
            source = "schedule"
    else:
        source = "dashboard"

    # 單次「更新資料」必須同時新增本月與次月各一列，且使用同一個時間戳。
    # 不用舊 daily_df/df4 的 id 判斷是否新增；舊資料一律保留，只有使用者勾選刪除才會移除。
    run_dt = now_dt()
    daily_df = build_daily_overview_df(df4, source=source, run_dt=run_dt)
    next_month_daily_df = build_next_month_overview_df(df4, source=source, run_dt=run_dt)
    month_end_df = build_month_end_summary_df(df4, source=source)

    log(f"raw_df columns = {list(raw_df.columns)}")
    log(f"raw_df 前5筆 = {raw_df.head().to_dict('records')}")
    log(f"df1 rows = {len(df1)}")
    log(f"df2 rows = {len(df2)}")
    log(f"df3 rows = {len(df3)}")
    log(f"df4 rows = {len(df4)}")
    log(f"daily_df rows = {len(daily_df)}")
    log(f"next_month_daily_df rows = {len(next_month_daily_df)}")
    log(f"month_end_df rows = {len(month_end_df)}")

    email_html = build_region4_email_html(df4)
    error_msg = None if not city_errors else " / ".join(city_errors)

    if persist_dashboard:
        persist_dashboard_payload(df4, daily_df, next_month_daily_df, month_end_df, email_html, error_msg, trigger=trigger)

    if send_email:
        email_required = os.getenv("PERFORMANCE_REPORT_EMAIL_REQUIRED", "").lower() in ("1", "true", "yes", "y")
        send_region4_email(df4, required=email_required)

    return {
        "raw_df": raw_df,
        "df1": df1,
        "df2": df2,
        "df3": df3,
        "df4": format_region4_for_display(df4),
        "daily_df": daily_df,
        "next_month_daily_df": next_month_daily_df,
        "month_end_df": month_end_df,
        "month_end_history_df": load_month_end_history(),
        "email_html": email_html,
        "updated_at": now_dt().strftime("%Y-%m-%d %H:%M:%S"),
        "execution_log_df": pd.DataFrame(),
        "daily_history_df": pd.DataFrame(),
        "output_file_log_df": load_output_file_log(),
        "error": error_msg,
    }


def main():
    trigger = "schedule"
    send_email = False

    args = [arg for arg in os.sys.argv[1:] if arg != "--folder-id"]
    # 若 toolapp 誤傳 --folder-id <id>，業績報表不需要雲端資料夾，直接忽略。
    cleaned_args = []
    skip_next = False
    for arg in os.sys.argv[1:]:
        if skip_next:
            skip_next = False
            continue
        if arg == "--folder-id":
            skip_next = True
            continue
        cleaned_args.append(arg)

    if len(cleaned_args) >= 1:
        trigger = cleaned_args[0]

    if len(cleaned_args) >= 2:
        send_email = cleaned_args[1].lower() in ("1", "true", "yes", "y")

    strict_accounts = os.getenv("PERFORMANCE_REPORT_STRICT_ACCOUNTS", "").lower() in ("1", "true", "yes", "y")

    result = generate_sales_report(
        send_email=send_email,
        persist_dashboard=True,
        trigger=trigger,
        strict_accounts=strict_accounts,
    )

    if result.get("error"):
        raise RuntimeError(str(result["error"]))


if __name__ == "__main__":
    main()
