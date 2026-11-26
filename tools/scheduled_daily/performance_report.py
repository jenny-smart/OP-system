import os
import json
import calendar
import re
import smtplib
import tempfile
from concurrent.futures import ThreadPoolExecutor
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone
from pathlib import Path

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

from tools.scheduled_daily.reserve_deductions import resolve_deductions

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
EXTENDED_REPORT_FILE = Path(DASHBOARD_DIR) / "tool_report_extended" / "report.json"
EXTENDED_PERIOD_NAMES = ("次次月", "次次次月")


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


def get_report_month_ranges(start_month: Optional[str] = None, end_month: Optional[str] = None):
    """依畫面設定回傳起訖月份（含首尾）的標籤與完整月份起訖日。"""
    try:
        base = datetime.strptime(str(start_month), "%Y-%m") if start_month else now_dt().replace(day=1)
        if end_month:
            end_base = datetime.strptime(str(end_month), "%Y-%m")
        else:
            end_index = base.month
            end_base = base.replace(
                year=base.year + end_index // 12,
                month=end_index % 12 + 1,
            )
    except ValueError as exc:
        raise ValueError("起訖月份格式必須為 YYYY-MM") from exc

    month_count = (end_base.year - base.year) * 12 + end_base.month - base.month + 1
    if month_count < 1:
        raise ValueError("結束月份不可早於起始月份")
    if month_count > 24:
        raise ValueError("月份區間最多 24 個月")
    ranges = []
    for offset in range(month_count):
        month_index = base.month - 1 + offset
        year = base.year + month_index // 12
        month = month_index % 12 + 1
        label = f"{year}/{month:02d}"
        start = f"{year}-{month:02d}-01"
        end = f"{year}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"
        ranges.append((label, start, end))
    return ranges


def _extract_purchase_list_data(html: str):
    """讀取後台 purchaseList Vue JSON；若頁面版本無此資料就回傳空陣列。"""
    source = str(html or "")
    marker_pos = source.find("purchaseList:")
    if marker_pos < 0:
        return []
    start = source.find("{", marker_pos)
    if start < 0:
        return []

    depth = 0
    in_string = False
    escaped = False
    end = -1
    for idx in range(start, len(source)):
        char = source[idx]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = idx + 1
                break
    if end < 0:
        return []
    try:
        payload = json.loads(source[start:end])
    except Exception:
        return []
    data = payload.get("data", [])
    return data if isinstance(data, list) else []


def _fetch_purchase_items(session, **filters):
    """依條件讀取所有分頁的訂單 JSON，並以訂單編號去重。"""
    seen = {}
    for page in range(1, 81):
        params = {
            "keyword": "", "name": "", "phone": "", "orderNo": "",
            "date_s": "", "date_e": "", "clean_date_s": "", "clean_date_e": "",
            "paid_at_s": "", "paid_at_e": "", "refundDateS": "", "refundDateE": "",
            "buy": "", "area_id": "", "isCharge": "", "isRefund": "",
            "p_board": "on", "payway": "", "purchase_status": "",
            "progress_status": "", "invoiceStatus": "", "otherFee": "", "orderBy": "",
            "page": str(page),
        }
        params.update({k: v for k, v in filters.items() if v not in (None, "")})
        response = session.get(PURCHASE_URL, params=params, headers=HEADERS, allow_redirects=True)
        response.raise_for_status()
        items = _extract_purchase_list_data(response.text)
        if not items:
            break
        for item in items:
            order_no = str(item.get("order_no") or item.get("orderNo") or item.get("purchase_id") or "").strip()
            if order_no:
                seen[order_no] = item
        if len(items) < 20:
            break
    return list(seen.values())


def _purchase_created_date(item) -> str:
    for key in ("created_at", "create_at", "createdAt", "purchase_date", "date_created"):
        value = str(item.get(key) or "").strip()
        match = re.search(r"20\d{2}-\d{2}-\d{2}", value)
        if match:
            return match.group(0)
    return ""


def _purchase_uses_stored_value(item) -> bool:
    payment = " ".join(str(item.get(key) or "") for key in (
        "payway", "payment", "payment_method", "paymentMethod", "pay_type",
    )).lower()
    return "儲值金" in payment or "vvip" in payment or str(item.get("is_vvip") or "") == "1"


def _purchase_stored_weekend_amount(item) -> int:
    """相容後台不同版本的儲值金週末加價欄位（含巢狀資料）。"""
    generic_weekend_keys = {"weekendprice", "weekendfee", "holidayprice", "holidayfee"}

    def find(value):
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", str(key).lower())
                is_weekend = "weekend" in normalized or "holiday" in normalized or "週末" in normalized
                is_stored = "stored" in normalized or "vvip" in normalized or "儲值" in normalized
                if is_weekend and (is_stored or (normalized in generic_weekend_keys and _purchase_uses_stored_value(item))):
                    return safe_int(child)
            for child in value.values():
                found = find(child)
                if found:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = find(child)
                if found:
                    return found
        return 0

    return find(item)


def _purchase_amount(item) -> int:
    base = safe_int(item.get("total") or item.get("amount") or item.get("price") or 0)
    # 後台 purchaseList 的 total 不含儲值金週末加價，需另外加回。
    return base + _purchase_stored_weekend_amount(item)


def _purchase_service_date(item) -> str:
    for key in ("date_clean", "service_date", "clean_date", "dateClean"):
        value = str(item.get(key) or "").strip()
        match = re.search(r"20\d{2}-\d{2}-\d{2}", value)
        if match:
            return match.group(0)
    return ""


def _purchase_is_stored_value_topup(item) -> bool:
    buy = str(item.get("buy") or "").strip().lower()
    # 後台購買項目：1 是專業清潔，5 才是購買儲值金。
    if buy == "5":
        return True
    service = " ".join(str(item.get(key) or "") for key in (
        "service", "service_name", "item_name", "purchase_item", "title",
    ))
    return not _purchase_service_date(item) and "儲值金" in service


def _purchase_is_cancelled(item) -> bool:
    return bool(item.get("cancel_at") or item.get("cancel_log") or str(item.get("purchase_status")) in {"cancel", "cancelled"})


def _purchase_is_paid(item) -> bool:
    return bool(item.get("paid_at") or str(item.get("purchase_status") or "").strip() == "1")


def _purchase_is_reserve(item) -> bool:
    searchable = json.dumps(item, ensure_ascii=False, default=str)
    name = str(item.get("name") or item.get("customer_name") or "")
    return (
        "系統保留單" in searchable
        or "大掃除檸檬保留單" in searchable
        or "保留" in name
        or "檸檬" in name
    )


def _purchase_person_hours(item) -> float:
    """保留時數以人數 × 每人服務時數計算。"""
    try:
        people = float(item.get("person") or item.get("people") or item.get("cleaner_count") or 1)
    except Exception:
        people = 1
    try:
        hours = float(item.get("hour") or item.get("hours") or item.get("service_hours") or 0)
    except Exception:
        hours = 0
    if hours <= 0:
        start = str(item.get("period_s") or "")
        end = str(item.get("period_e") or "")
        try:
            start_dt = datetime.strptime(start[:5], "%H:%M")
            end_dt = datetime.strptime(end[:5], "%H:%M")
            hours = max(0, (end_dt - start_dt).total_seconds() / 3600)
        except Exception:
            hours = 0
    return people * hours


def get_order_service_month_ranges(base_date=None, month_count=4):
    """訂購日期報表固定拆成本月起四個服務月份。"""
    base = base_date or now_dt()
    if not isinstance(base, datetime):
        base = datetime.strptime(str(base)[:10], "%Y-%m-%d")
    ranges = []
    for offset in range(month_count):
        month_index = base.month - 1 + offset
        year = base.year + month_index // 12
        month = month_index % 12 + 1
        ranges.append((
            f"{year}/{month:02d}",
            f"{year}-{month:02d}-01",
            f"{year}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}",
        ))
    return ranges


def _build_order_payment_table(records, payment_label, month_ranges) -> pd.DataFrame:
    cols = ["地區", payment_label]
    cols.extend(f"{label}{payment_label}" for label, _, _ in month_ranges)
    cols.append(f"儲值金{payment_label}")
    rows = []
    for item in records:
        if _purchase_is_cancelled(item):
            continue
        city = str(item.get("__city") or "")
        if not city:
            continue
        amount = _purchase_amount(item)
        row = {col: 0 for col in cols}
        row["地區"] = city
        if _purchase_is_stored_value_topup(item):
            row[f"儲值金{payment_label}"] = amount
        else:
            row[payment_label] = amount
            service_date = _purchase_service_date(item)
            for label, start, end in month_ranges:
                if start <= service_date <= end:
                    row[f"{label}{payment_label}"] = amount
                    break
        rows.append(row)

    out = pd.DataFrame(rows, columns=cols).groupby("地區", as_index=False).sum() if rows else pd.DataFrame(columns=cols)
    out = out.set_index("地區").reindex(CITY_ORDER, fill_value=0).reset_index()
    total = {"地區": "加總", **{col: out[col].sum() for col in cols[1:]}}
    return pd.concat([out, pd.DataFrame([total])], ignore_index=True)[cols]


def build_order_date_summaries(records, month_ranges=None):
    month_ranges = month_ranges or get_order_service_month_ranges()
    unpaid = _build_order_payment_table(
        [x for x in records if not _purchase_is_paid(x)], "待付款", month_ranges,
    )
    paid = _build_order_payment_table(
        [x for x in records if _purchase_is_paid(x)], "已付款", month_ranges,
    )
    combined = unpaid.copy()
    combined.columns = ["地區", "待付款＋已付款"] + [
        col.replace("待付款", "待付款＋已付款") for col in unpaid.columns[2:]
    ]
    for index, col in enumerate(paid.columns[1:], start=1):
        combined.iloc[:, index] = unpaid.iloc[:, index] + paid[col]
    return {"待付款": unpaid, "已付款": paid, "待付款＋已付款": combined}


def build_order_date_summaries_from_report_rows(records, month_ranges):
    """用後台訂單統計表的實際金額建立訂購日期付款彙總。"""
    tables = {}
    for payment_label in ("待付款", "已付款"):
        cols = ["地區", payment_label]
        cols.extend(f"{label}{payment_label}" for label, _, _ in month_ranges)
        cols.append(f"儲值金{payment_label}")
        rows = []
        for city in CITY_ORDER:
            matched = next(
                (
                    row for row in records
                    if row.get("地區") == city and row.get("付款狀態") == payment_label
                ),
                {},
            )
            row = {col: 0 for col in cols}
            row["地區"] = city
            row[payment_label] = safe_int(matched.get("總金額", 0))
            for label, _, _ in month_ranges:
                row[f"{label}{payment_label}"] = safe_int(
                    (matched.get("月份金額") or {}).get(label, 0)
                )
            row[f"儲值金{payment_label}"] = safe_int(matched.get("儲值金金額", 0))
            rows.append(row)
        out = pd.DataFrame(rows, columns=cols)
        total = {"地區": "加總", **{col: out[col].sum() for col in cols[1:]}}
        tables[payment_label] = pd.concat(
            [out, pd.DataFrame([total])], ignore_index=True,
        )[cols]

    unpaid = tables["待付款"]
    paid = tables["已付款"]
    combined = unpaid.copy()
    combined.columns = ["地區", "待付款＋已付款"] + [
        col.replace("待付款", "待付款＋已付款") for col in unpaid.columns[2:]
    ]
    for index, col in enumerate(paid.columns[1:], start=1):
        combined.iloc[:, index] = unpaid.iloc[:, index] + paid[col]
    tables["待付款＋已付款"] = combined
    return tables


def build_order_date_summary(records) -> pd.DataFrame:
    """保留原介面供既有呼叫使用。"""
    cols = ["地區", "未付款", "已付款", "未付款＋已付款"]
    rows = []
    for item in records:
        if _purchase_is_cancelled(item) or not item.get("__city"):
            continue
        amount = _purchase_amount(item)
        rows.append({
            "地區": str(item["__city"]),
            "未付款": 0 if _purchase_is_paid(item) else amount,
            "已付款": amount if _purchase_is_paid(item) else 0,
        })
    if not rows:
        return pd.DataFrame(columns=cols)
    out = pd.DataFrame(rows).groupby("地區", as_index=False)[["未付款", "已付款"]].sum()
    out["未付款＋已付款"] = out["未付款"] + out["已付款"]
    out["地區"] = pd.Categorical(out["地區"], CITY_ORDER, ordered=True)
    out = out.sort_values("地區").reset_index(drop=True)
    out["地區"] = out["地區"].astype(str)
    total = {"地區": "加總", **{col: out[col].sum() for col in cols[1:]}}
    return pd.concat([out, pd.DataFrame([total])], ignore_index=True)[cols]


def build_month_performance_summary(raw_df: pd.DataFrame, month_ranges) -> pd.DataFrame:
    cols = ["地區"] + [f"{label}業績" for label, _, _ in month_ranges]
    work = raw_df.copy()
    work["類別"] = work.apply(lambda r: to_category(r["服務"], r["收入類型"]), axis=1)
    work = work[work["類別"] == "清潔"].copy()
    work["業績"] = pd.to_numeric(work["已付款"], errors="coerce").fillna(0) + pd.to_numeric(work["待付款"], errors="coerce").fillna(0)

    rows = []
    for city in CITY_ORDER:
        row = {"地區": city}
        for label, _, _ in month_ranges:
            row[f"{label}業績"] = work[(work["城市"] == city) & (work["月份"] == label)]["業績"].sum()
        rows.append(row)
    total = {"地區": "加總"}
    for col in cols[1:]:
        total[col] = sum(row[col] for row in rows)
    rows.append(total)
    return pd.DataFrame(rows, columns=cols)


def build_reserve_summary(records, month_ranges, amount_for_item=_purchase_amount) -> pd.DataFrame:
    cols = ["地區"]
    for label, _, _ in month_ranges:
        cols.extend([f"{label}保留單時數", f"{label}保留單業績"])
    rows = []
    for city in CITY_ORDER:
        row = {col: 0 for col in cols}
        row["地區"] = city
        city_records = [x for x in records if x.get("__city") == city and not _purchase_is_cancelled(x) and _purchase_is_reserve(x)]
        for display_label, start, end in month_ranges:
            selected = [x for x in city_records if start <= str(x.get("date_clean") or x.get("service_date") or "")[:10] <= end]
            row[f"{display_label}保留單時數"] = sum(_purchase_person_hours(x) for x in selected)
            row[f"{display_label}保留單業績"] = sum(amount_for_item(x) for x in selected)
        rows.append(row)
    total = {"地區": "加總"}
    for col in cols[1:]:
        total[col] = sum(float(row[col]) for row in rows)
    rows.append(total)
    return pd.DataFrame(rows, columns=cols)


def build_net_performance_summary(raw_df: pd.DataFrame, reserve_df: pd.DataFrame, month_ranges) -> pd.DataFrame:
    cols = ["地區"] + [f"{label}業績－保留單業績" for label, _, _ in month_ranges]
    performance_df = build_month_performance_summary(raw_df, month_ranges)

    rows = []
    for city in CITY_ORDER:
        row = {"地區": city}
        reserve_row = reserve_df[reserve_df["地區"].astype(str) == city]
        performance_row = performance_df[performance_df["地區"].astype(str) == city]
        for display_label, _, _ in month_ranges:
            performance_col = f"{display_label}業績"
            reserve_col = f"{display_label}保留單業績"
            gross = 0 if performance_row.empty else safe_int(performance_row.iloc[0].get(performance_col, 0))
            reserve_amount = 0 if reserve_row.empty else safe_int(reserve_row.iloc[0].get(reserve_col, 0))
            row[f"{display_label}業績－保留單業績"] = gross - reserve_amount
        rows.append(row)

    total_row = {"地區": "加總"}
    for display_label, _, _ in month_ranges:
        amount_col = f"{display_label}業績－保留單業績"
        total_row[amount_col] = sum(row[amount_col] for row in rows)
    rows.append(total_row)
    return pd.DataFrame(rows, columns=cols)


def build_report_url(
    status,
    keyword="",
    date_s="",
    date_e="",
    clean_date_s="",
    clean_date_e="",
):
    params = {
        "keyword": keyword,
        "name": "",
        "phone": "",
        "orderNo": "",
        "date_s": date_s,
        "date_e": date_e,
        "clean_date_s": clean_date_s,
        "clean_date_e": clean_date_e,
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


def build_url(start, end, status, keyword=""):
    return build_report_url(
        status,
        keyword=keyword,
        clean_date_s=start,
        clean_date_e=end,
    )


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
            # 儲值金表的週末加價未包含在付款金額內，依本次查詢狀態併回對應欄位。
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


def _payment_amount_from_report_rows(rows, payment_status):
    """拆分服務金額與購買儲值金；服務金額包含以儲值金付款的服務。"""
    amount_key = "已付款" if str(payment_status) == "1" else "待付款"
    service_amount = 0
    stored_value_topup = 0
    for row in rows:
        amount = safe_int(row.get(amount_key, 0))
        if row.get("收入類型") == "現金收入" and row.get("服務") == "儲值金":
            stored_value_topup += amount
        else:
            service_amount += amount
    return service_amount, stored_value_topup


def _fetch_order_date_report_row(
    session,
    city,
    order_start_date,
    order_end_date,
    month_ranges,
    payment_status,
):
    payment_label = "已付款" if str(payment_status) == "1" else "待付款"
    result = {
        "地區": city,
        "付款狀態": payment_label,
        "總金額": 0,
        "月份金額": {label: 0 for label, _, _ in month_ranges},
        "儲值金金額": 0,
    }
    for keyword in get_keywords(city):
        total_url = build_report_url(
            payment_status,
            keyword=keyword,
            date_s=order_start_date,
            date_e=order_end_date,
        )
        response = session.get(total_url, headers=HEADERS, allow_redirects=True)
        response.raise_for_status()
        rows = parse_html(response.text, payment_status=payment_status)
        service_amount, topup_amount = _payment_amount_from_report_rows(
            rows, payment_status,
        )
        result["總金額"] += service_amount
        result["儲值金金額"] += topup_amount

        for label, clean_start, clean_end in month_ranges:
            month_url = build_report_url(
                payment_status,
                keyword=keyword,
                date_s=order_start_date,
                date_e=order_end_date,
                clean_date_s=clean_start,
                clean_date_e=clean_end,
            )
            response = session.get(month_url, headers=HEADERS, allow_redirects=True)
            response.raise_for_status()
            month_rows = parse_html(response.text, payment_status=payment_status)
            month_amount, _ = _payment_amount_from_report_rows(
                month_rows, payment_status,
            )
            result["月份金額"][label] += month_amount
    return result


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
    historical_rows: Optional[pd.DataFrame] = None,
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

    period_names = ("本月", "次月", *EXTENDED_PERIOD_NAMES)
    offset = period_names.index(period_label)
    stat_month = get_order_service_month_ranges(now_obj, month_count=offset + 1)[offset][0]

    def get_val(city, col):
        try:
            row = df4[df4["城市"] == city]
            if row.empty or col not in row.columns:
                return 0
            return row.iloc[0][col]
        except Exception:
            return 0

    if historical_rows is not None:
        old_df = historical_rows.copy()
    elif os.path.exists(latest_path):
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

    log(f"✅ {period_label}統計報表完成，筆數 = {len(out)}")
    return out[cols]


def build_extended_performance(raw_df, month_ranges):
    summary = pd.DataFrame({"城市": [*CITY_ORDER, "加總"]})
    for period, (month, _, _) in zip(EXTENDED_PERIOD_NAMES, month_ranges[2:4]):
        # Reuse the same service classification and totals as the existing main report.
        if raw_df.empty:
            summary[f"{period}加總"] = 0
            summary[f"{period}佔比"] = 0.0
        else:
            monthly_raw = raw_df.assign(月份=raw_df["月份"].map({month: "本月"}))
            monthly = build_region4_df(build_region2_df(monthly_raw))
            monthly = monthly[["城市", "本月加總", "本月佔比"]].rename(
                columns={"本月加總": f"{period}加總", "本月佔比": f"{period}佔比"}
            )
            summary = summary.merge(monthly, on="城市", validate="one_to_one")
    return summary


def _reserve_amount(item):
    actual = item.get("__actual_reserve_deduction")
    if actual is not None:
        return actual
    return safe_int(item.get("total") or item.get("amount") or item.get("price") or 0)


def build_extended_reserve_details(records, month_ranges):
    start, end = month_ranges[0][1], month_ranges[-1][2]
    rows = []
    for item in records:
        date = _purchase_service_date(item)
        if not start <= date <= end or _purchase_is_cancelled(item) or not _purchase_is_reserve(item):
            continue
        rows.append({
            "地區": item["__city"],
            "服務日期": date,
            "訂單編號": str(item.get("order_no") or item.get("orderNo") or item.get("purchase_id") or ""),
            "保留單時數": _purchase_person_hours(item),
            "訂單金額": _reserve_amount(item),
            "金額來源": "儲值金實際扣款" if item.get("__actual_reserve_deduction") is not None else "訂單列總金額（歷程未對到）",
        })
    return sorted(rows, key=lambda row: (row["服務日期"], row["地區"], row["訂單編號"]))


def save_extended_report(raw_df, order_rows, reserve_records, month_ranges, order_start, order_end):
    import fcntl
    from uuid import uuid4

    summary = build_extended_performance(raw_df, month_ranges) if raw_df is not None else None
    run_dt = now_dt()
    path = EXTENDED_REPORT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        payload = dict(previous)
        timestamp = run_dt.strftime("%Y/%m/%d %H:%M:%S")
        if summary is not None:
            history = dict(previous.get("history", {}))
            for name in EXTENDED_PERIOD_NAMES:
                history_frame = _build_period_overview_df(
                    summary, "dashboard", f"{name}加總", f"{name}佔比", "",
                    name, run_dt=run_dt, historical_rows=pd.DataFrame(),
                )
                newest = history_frame.to_dict("records")[0]
                newest["id"] += f"-{uuid4().hex}"
                history[name] = [newest, *history.get(name, [])]
            payload.update(updated_at=timestamp, summary=summary.to_dict("records"), history=history)
        if order_rows is not None:
            orders = build_order_date_summaries_from_report_rows(order_rows, month_ranges)
            payload.update(
                order_updated_at=timestamp, order_start=order_start, order_end=order_end,
                orders={label: frame.to_dict("records") for label, frame in orders.items()},
            )
        if reserve_records is not None:
            reserve = build_reserve_summary(reserve_records, month_ranges, amount_for_item=_reserve_amount)
            payload.update(
                reserve_updated_at=timestamp, reserve=reserve.to_dict("records"),
                reserve_details=build_extended_reserve_details(reserve_records, month_ranges),
            )
        fd, temp_path = tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, allow_nan=False)
            os.replace(temp_path, path)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)


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
        ["NOTIFY_EMAIL"],
        env_name="NOTIFY_EMAIL",
        required=False,
        default="jenny@hers.com.tw",
    )
    password = get_secret(
        ["NOTIFY_PASSWORD"],
        env_name="NOTIFY_PASSWORD",
        required=False,
    )
    recipient = get_secret(
        ["NOTIFY_TO"],
        env_name="NOTIFY_TO",
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
        missing.append("NOTIFY_EMAIL")
    if not password:
        missing.append("NOTIFY_PASSWORD")
    if not recipients:
        missing.append("NOTIFY_TO")

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
            "Gmail 拒絕登入，請確認 NOTIFY_EMAIL 與 NOTIFY_PASSWORD "
            "是同一個 Gmail 帳號及其 App Password"
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
    order_date_df: Optional[pd.DataFrame] = None,
    order_date_paid_df: Optional[pd.DataFrame] = None,
    order_date_combined_df: Optional[pd.DataFrame] = None,
    month_performance_df: Optional[pd.DataFrame] = None,
    reserve_df: Optional[pd.DataFrame] = None,
    net_performance_df: Optional[pd.DataFrame] = None,
    report_start_month: Optional[str] = None,
    report_end_month: Optional[str] = None,
    order_start_date: Optional[str] = None,
    order_end_date: Optional[str] = None,
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
    latest_order_date = os.path.join(LATEST_DIR, "order_date_summary.csv")
    latest_order_date_paid = os.path.join(LATEST_DIR, "order_date_paid_summary.csv")
    latest_order_date_combined = os.path.join(LATEST_DIR, "order_date_combined_summary.csv")
    latest_month_performance = os.path.join(LATEST_DIR, "month_performance_summary.csv")
    latest_reserve = os.path.join(LATEST_DIR, "reserve_summary.csv")
    latest_net_performance = os.path.join(LATEST_DIR, "net_performance_summary.csv")

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

    for report_df, report_path in [
        (order_date_df, latest_order_date),
        (order_date_paid_df, latest_order_date_paid),
        (order_date_combined_df, latest_order_date_combined),
        (month_performance_df, latest_month_performance),
        (reserve_df, latest_reserve),
        (net_performance_df, latest_net_performance),
    ]:
        if report_df is not None:
            report_df.to_csv(report_path, index=False, encoding="utf-8-sig")
            append_output_file_log("業績報表", report_path, trigger)

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

    previous_meta = {}
    if os.path.exists(latest_meta):
        try:
            with open(latest_meta, encoding="utf-8") as f:
                previous_meta = json.load(f)
        except Exception:
            previous_meta = {}

    meta = {
        "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "df4_rows": int(len(df4)),
        "daily_rows": int(len(daily_df)),
        "next_month_daily_rows": int(len(next_month_daily_df)),
        "month_end_rows": int(len(month_end_df)),
        "order_date_rows": int(len(order_date_df)) if order_date_df is not None else previous_meta.get("order_date_rows", 0),
        "month_performance_rows": int(len(month_performance_df)) if month_performance_df is not None else previous_meta.get("month_performance_rows", 0),
        "reserve_rows": int(len(reserve_df)) if reserve_df is not None else previous_meta.get("reserve_rows", 0),
        "net_performance_rows": int(len(net_performance_df)) if net_performance_df is not None else previous_meta.get("net_performance_rows", 0),
        "report_start_month": report_start_month or previous_meta.get("report_start_month"),
        "report_end_month": report_end_month or previous_meta.get("report_end_month"),
        "order_start_date": order_start_date or previous_meta.get("order_start_date"),
        "order_end_date": order_end_date or previous_meta.get("order_end_date"),
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


def generate_order_date_report(
    order_start_date: str,
    order_end_date: str,
    trigger="dashboard",
    progress_callback=None,
):
    """只更新訂購日期付款彙總，不重抓目前總表、月份業績或保留單。"""
    if order_end_date < order_start_date:
        raise ValueError("訂購日期迄日不可早於起日")

    enabled_cities = [city for city in CITY_ORDER if city in ACCOUNTS]
    if not enabled_cities:
        raise RuntimeError("ACCOUNTS 沒有任何可用城市設定")

    month_start = get_ranges()[0][0]
    month_ranges = get_order_service_month_ranges(month_start, month_count=4)

    def fetch_city(city):
        session = requests.Session()
        try:
            account = ACCOUNTS[city]
            login(session, account["email"], account["password"])
            return [
                _fetch_order_date_report_row(
                    session,
                    city,
                    order_start_date,
                    order_end_date,
                    month_ranges,
                    payment_status,
                )
                for payment_status in (0, 1)
            ]
        finally:
            close = getattr(session, "close", None)
            if close:
                close()

    report_rows = []
    errors = []
    with ThreadPoolExecutor(max_workers=min(5, len(enabled_cities))) as executor:
        futures = {city: executor.submit(fetch_city, city) for city in enabled_cities}
        for completed, city in enumerate(enabled_cities, start=1):
            city_error = None
            try:
                report_rows.extend(futures[city].result())
            except Exception as exc:
                city_error = f"{city} 失敗：{exc}"
                errors.append(city_error)
                log(f"❌ {city_error}")
            if progress_callback:
                progress_callback(completed, len(enabled_cities), city, city_error)

    if not report_rows:
        raise RuntimeError("所有地區更新失敗：" + " / ".join(errors))

    summaries = build_order_date_summaries_from_report_rows(report_rows, month_ranges)
    ensure_dirs()
    output_files = {
        "待付款": os.path.join(LATEST_DIR, "order_date_summary.csv"),
        "已付款": os.path.join(LATEST_DIR, "order_date_paid_summary.csv"),
        "待付款＋已付款": os.path.join(LATEST_DIR, "order_date_combined_summary.csv"),
    }
    for label, path in output_files.items():
        summaries[label].to_csv(path, index=False, encoding="utf-8-sig")
        append_output_file_log("業績報表", path, trigger)

    meta_path = os.path.join(LATEST_DIR, "meta.json")
    meta = {}
    try:
        if os.path.exists(meta_path):
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
    except Exception:
        meta = {}
    meta.update({
        "updated_at": now_dt().strftime("%Y-%m-%d %H:%M:%S"),
        "order_date_updated_at": now_dt().strftime("%Y-%m-%d %H:%M:%S"),
        "order_date_rows": int(len(summaries["待付款"])),
        "order_start_date": order_start_date,
        "order_end_date": order_end_date,
        "order_date_error": " / ".join(errors) if errors else None,
        "trigger": trigger,
    })
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    append_output_file_log("業績報表", meta_path, trigger)

    return {**summaries, "error": " / ".join(errors) if errors else None}


def generate_sales_report(
    send_email=False,
    persist_dashboard=True,
    trigger="dashboard",
    strict_accounts=False,
    report_start_month: Optional[str] = None,
    report_end_month: Optional[str] = None,
    order_start_date: Optional[str] = None,
    order_end_date: Optional[str] = None,
    update_scope: str = "all",
):
    log("🔥 開始業績報表")

    if update_scope not in {"all", "current", "reserve"}:
        raise ValueError("不支援的業績報表更新範圍")
    reserve_only = update_scope == "reserve"
    web_report = trigger == "dashboard" and persist_dashboard

    ensure_dirs()
    (m_start, m_end), (n_start, n_end) = get_ranges()
    report_month_ranges = get_report_month_ranges(report_start_month, report_end_month)
    today_text = now_dt().strftime("%Y-%m-%d")
    order_start_date = order_start_date or today_text
    order_end_date = order_end_date or today_text
    if order_end_date < order_start_date:
        raise ValueError("訂購日期迄日不可早於起日")
    current_month_label = m_start[:7].replace("-", "/")
    next_month_label = n_start[:7].replace("-", "/")
    fetch_month_ranges = []
    seen_months = set()
    requested_month_ranges = [
        (current_month_label, m_start, m_end),
        (next_month_label, n_start, n_end),
    ]
    four_month_ranges = get_order_service_month_ranges(m_start, month_count=4)
    reserve_detail_start = (now_dt() + timedelta(days=1)).strftime("%Y-%m-%d")
    if update_scope == "all":
        requested_month_ranges.extend(report_month_ranges)
    extra_month_labels = {month[0] for month in four_month_ranges[2:]}
    if reserve_only:
        requested_month_ranges = []
    elif web_report:
        requested_month_ranges.extend(four_month_ranges[2:])
    for month_range in requested_month_ranges:
        if month_range[0] not in seen_months:
            seen_months.add(month_range[0])
            fetch_month_ranges.append(month_range)
    merged = {}
    order_month_ranges = four_month_ranges
    order_date_report_rows = []
    reserve_records = []
    city_errors = []

    def upcoming_reserves(items):
        return [
            item for item in items
            if "保留" in str(item.get("name") or item.get("customer_name") or "")
            and _purchase_is_paid(item)
            and not _purchase_is_cancelled(item)
            and reserve_detail_start <= _purchase_service_date(item) <= four_month_ranges[-1][2]
        ]

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
        city_order_date_rows = []
        city_reserve_records = []
        errors = []
        log(f"===== {city} =====")
        session = requests.Session()
        acc = ACCOUNTS[city]

        try:
            login(session, acc["email"], acc["password"])
            city_row_count = 0

            for label, s, e in fetch_month_ranges:
                for status in [1, 0]:
                    for kw in get_keywords(city):
                        url = build_url(s, e, status, kw)
                        log(f"抓取：city={city} month={label} status={status} kw={kw} url={url}")

                        res = session.get(url, headers=HEADERS, allow_redirects=True)
                        res.raise_for_status()

                        rows = parse_html(res.text, payment_status=status)
                        city_row_count += len(rows)

                        if not rows and (not web_report or label not in extra_month_labels):
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

            if update_scope == "all" or reserve_only:
                try:
                    for payment_status in (() if reserve_only else (0, 1)):
                        city_order_date_rows.append(
                            _fetch_order_date_report_row(
                                session,
                                city,
                                order_start_date,
                                order_end_date,
                                order_month_ranges,
                                payment_status,
                            )
                        )

                    reserve_ranges = four_month_ranges if reserve_only else report_month_ranges
                    if update_scope == "all" and web_report:
                        reserve_ranges = sorted([*report_month_ranges, *four_month_ranges])
                    reserve_filters = {"name": "保留", "purchase_status": "1"} if reserve_only else {}
                    reserve_items = _fetch_purchase_items(
                        session,
                        clean_date_s=reserve_detail_start if reserve_only else reserve_ranges[0][1],
                        clean_date_e=reserve_ranges[-1][2],
                        **reserve_filters,
                    )
                    for item in reserve_items:
                        item["__city"] = city
                    if web_report:
                        if reserve_only:
                            detail_items = [item for item in reserve_items if not _purchase_is_cancelled(item) and _purchase_is_reserve(item)]
                        else:
                            detail_items = upcoming_reserves(reserve_items)
                        deductions = resolve_deductions(session, detail_items, PURCHASE_URL, HEADERS)
                        for item in detail_items:
                            order_no = str(item.get("order_no") or item.get("orderNo") or item.get("purchase_id") or "").strip()
                            if order_no in deductions:
                                item["__actual_reserve_deduction"] = deductions[order_no]
                    city_reserve_records.extend(reserve_items)
                except Exception as extra_exc:
                    msg = f"{city} 新增報表資料抓取失敗：{extra_exc}"
                    errors.append(msg)
                    log(f"⚠️ {msg}")

            if city_row_count == 0 and not reserve_only:
                msg = f"{city}：登入成功，但沒有抓到任何表格資料"
                errors.append(msg)
                log(f"⚠️ {msg}")

        except Exception as e:
            msg = f"{city} 失敗：{e}"
            errors.append(msg)
            log(f"❌ {msg}")

        return city_merged, city_order_date_rows, city_reserve_records, errors

    with ThreadPoolExecutor(max_workers=min(5, len(enabled_cities))) as executor:
        futures = {city: executor.submit(fetch_city, city) for city in enabled_cities}
        for city in enabled_cities:
            city_merged, city_order_rows, city_reserve_rows, errors = futures[city].result()
            merged.update(city_merged)
            order_date_report_rows.extend(city_order_rows)
            reserve_records.extend(city_reserve_rows)
            city_errors.extend(errors)

    report_raw_df = pd.DataFrame(merged.values())
    if reserve_only:
        if missing_cities or city_errors:
            raise RuntimeError("保留單明細抓取不完整，已保留前次資料")
        save_extended_report(
            None, None, reserve_records, four_month_ranges, None, None,
        )
        return {"error": None}
    raw_df = report_raw_df.copy()
    if not raw_df.empty:
        raw_df.loc[raw_df["月份"] == current_month_label, "月份"] = "本月"
        raw_df.loc[raw_df["月份"] == next_month_label, "月份"] = "下月"

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
    if update_scope == "all":
        order_summaries = build_order_date_summaries_from_report_rows(
            order_date_report_rows, order_month_ranges,
        )
        order_date_df = order_summaries["待付款"]
        order_date_paid_df = order_summaries["已付款"]
        order_date_combined_df = order_summaries["待付款＋已付款"]
        month_performance_df = build_month_performance_summary(report_raw_df, report_month_ranges)
        reserve_df = build_reserve_summary(reserve_records, report_month_ranges)
        net_performance_df = build_net_performance_summary(report_raw_df, reserve_df, report_month_ranges)
    else:
        order_date_df = order_date_paid_df = order_date_combined_df = None
        month_performance_df = reserve_df = net_performance_df = None

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
    log(f"order_date_df rows = {len(order_date_df) if order_date_df is not None else '保留'}")
    log(f"month_performance_df rows = {len(month_performance_df) if month_performance_df is not None else '保留'}")
    log(f"reserve_df rows = {len(reserve_df) if reserve_df is not None else '保留'}")
    log(f"net_performance_df rows = {len(net_performance_df) if net_performance_df is not None else '保留'}")

    email_html = build_region4_email_html(df4)
    error_msg = None if not city_errors else " / ".join(city_errors)

    if persist_dashboard:
        if web_report and not missing_cities and not city_errors:
            upcoming_paid_reserves = upcoming_reserves(reserve_records) if update_scope == "all" else None
            save_extended_report(
                report_raw_df,
                order_date_report_rows if update_scope == "all" else None,
                upcoming_paid_reserves,
                four_month_ranges, order_start_date, order_end_date,
            )
        persist_dashboard_payload(
            df4, daily_df, next_month_daily_df, month_end_df, email_html,
            order_date_df=order_date_df,
            order_date_paid_df=order_date_paid_df,
            order_date_combined_df=order_date_combined_df,
            month_performance_df=month_performance_df,
            reserve_df=reserve_df,
            net_performance_df=net_performance_df,
            report_start_month=report_month_ranges[0][0] if update_scope == "all" else None,
            report_end_month=report_month_ranges[-1][0] if update_scope == "all" else None,
            order_start_date=order_start_date if update_scope == "all" else None,
            order_end_date=order_end_date if update_scope == "all" else None,
            error_msg=error_msg,
            trigger=trigger,
        )

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
        "order_date_df": order_date_df,
        "order_date_paid_df": order_date_paid_df,
        "order_date_combined_df": order_date_combined_df,
        "month_performance_df": month_performance_df,
        "reserve_df": reserve_df,
        "net_performance_df": net_performance_df,
        "report_start_month": report_month_ranges[0][0],
        "report_end_month": report_month_ranges[-1][0],
        "order_start_date": order_start_date,
        "order_end_date": order_end_date,
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
    report_start_month = None
    report_end_month = None
    order_start_date = None
    order_end_date = None
    update_scope = "all"

    # 若 toolapp 誤傳 --folder-id <id>，業績報表不需要雲端資料夾，直接忽略。
    cleaned_args = []
    raw_args = os.sys.argv[1:]
    index = 0
    while index < len(raw_args):
        arg = raw_args[index]
        if arg in {"--folder-id", "--start-month", "--end-month", "--order-start-date", "--order-end-date", "--scope"}:
            value = raw_args[index + 1] if index + 1 < len(raw_args) else ""
            if arg == "--start-month":
                report_start_month = value
            elif arg == "--end-month":
                report_end_month = value
            elif arg == "--order-start-date":
                order_start_date = value
            elif arg == "--order-end-date":
                order_end_date = value
            elif arg == "--scope":
                update_scope = value
            index += 2
            continue
        cleaned_args.append(arg)
        index += 1

    if len(cleaned_args) >= 1:
        trigger = cleaned_args[0]

    if len(cleaned_args) >= 2:
        send_email = cleaned_args[1].lower() in ("1", "true", "yes", "y")

    strict_accounts = os.getenv("PERFORMANCE_REPORT_STRICT_ACCOUNTS", "").lower() in ("1", "true", "yes", "y")

    if update_scope == "order":
        today_text = now_dt().strftime("%Y-%m-%d")
        result = generate_order_date_report(
            order_start_date or today_text,
            order_end_date or today_text,
            trigger=trigger,
        )
        if result.get("error"):
            log(f"⚠️ {result['error']}")
        return

    result = generate_sales_report(
        send_email=send_email,
        persist_dashboard=True,
        trigger=trigger,
        strict_accounts=strict_accounts,
        report_start_month=report_start_month,
        report_end_month=report_end_month,
        order_start_date=order_start_date,
        order_end_date=order_end_date,
        update_scope=update_scope,
    )

    if result.get("error"):
        raise RuntimeError(str(result["error"]))


if __name__ == "__main__":
    main()
