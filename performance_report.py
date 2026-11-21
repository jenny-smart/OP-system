import os
import json
import calendar
import re
import smtplib
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone

TZ_TAIPEI = timezone(timedelta(hours=8))

def now_dt():
    return datetime.now(TZ_TAIPEI)
from typing import Optional

import pandas as pd
import requests
from bs4 import BeautifulSoup

from accounts import ACCOUNTS
from paths import PATH_REPORT


LOGIN_URL = "https://backend.lemonclean.com.tw/login"
PURCHASE_URL = "https://backend.lemonclean.com.tw/purchase"
HEADERS = {"User-Agent": "Mozilla/5.0"}

CITY_ORDER = ["台北", "台中", "桃園", "新竹", "高雄"]
INCOME_ORDER = ["現金收入", "儲值金"]
CATEGORY_ORDER = ["清潔", "儲值金", "冷氣", "洗衣機", "水洗", "收納"]
ORDER_DATE_SUMMARY_EXCLUDED_CATEGORIES = {
    "儲值金",
    "冷氣",
    "洗衣機",
    "水洗",
    "收納",
}

# 業績報表（各區月度摘要）的月份：本月起連續 5 個月，跟「訂購日期付款彙總」的 5 個月份一致。
# REPORT_MONTH_KEYS 是 raw_df「月份」欄的內部標籤（次月沿用舊標籤「下月」，不動 df1 與既有 CSV），
# REPORT_MONTH_NAMES 是畫面／CSV 欄位名稱的前綴。
REPORT_MONTH_KEYS = ["本月", "下月", "次次月", "次次次月", "次次次次月"]
REPORT_MONTH_NAMES = ["本月", "次月", "次次月", "次次次月", "次次次次月"]
REGION4_AMOUNT_COLS = [f"{n}加總" for n in REPORT_MONTH_NAMES]
REGION4_RATIO_COLS = [f"{n}佔比" for n in REPORT_MONTH_NAMES]
REGION4_APPLIANCE_COLS = [f"{n}家電加總" for n in REPORT_MONTH_NAMES]
REGION4_INT_COLS = REGION4_AMOUNT_COLS + REGION4_APPLIANCE_COLS + ["儲值金"]

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
    res = session.get(LOGIN_URL, headers=HEADERS, allow_redirects=True)
    res.raise_for_status()

    soup = BeautifulSoup(res.text, "html.parser")
    token_input = soup.find("input", {"name": "_token"})
    if not token_input:
        raise RuntimeError("找不到 _token，無法登入")

    payload = {
        "_token": token_input.get("value"),
        "email": email,
        "password": password,
    }

    login_res = session.post(LOGIN_URL, data=payload, headers=HEADERS, allow_redirects=True)
    login_res.raise_for_status()

    if "login" in login_res.url.lower():
        raise RuntimeError(f"{email} 登入失敗")

    log(f"✅ 登入成功：{email}")


def get_ranges():
    today = datetime.today()
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
            end_base = base
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


def _extract_purchase_row_info(html: str) -> dict:
    """從 /purchase 頁面表格列讀出 JSON 沒有的欄位：專員姓名、付款狀態、總金額、服務時段。

    專員姓名在「服務日期」欄的 /schedule/edit 連結裡（結尾帶評分 (n)，要去掉），
    解析方式與 orders-system 的 fetch_recent_service_records 一致。回傳 {訂單編號: {...}}。
    """
    info = {}
    try:
        try:
            soup = BeautifulSoup(str(html or ""), "lxml")  # 比 html.parser 快很多
        except Exception:
            soup = BeautifulSoup(str(html or ""), "html.parser")
        rows = soup.find_all("tr")
    except Exception:
        return info
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 3:
            continue
        order_match = re.search(r"\b[A-Z]{2}\d{5,}\b", cells[0].get_text(" ", strip=True))
        if not order_match:
            continue
        names = []
        for link in cells[2].find_all("a", href=re.compile(r"/schedule/edit")):
            name = re.sub(r"\(\d+\)\s*$", "", link.get_text(strip=True)).strip()
            if name:
                names.append(name)
        row_text = row.get_text("\n", strip=True)
        status_match = re.search(r"付款狀態[：:]\s*(\S+)", row_text)
        amount_match = re.search(r"總金額[：:]\s*(-?[\d,]+)", row_text)
        period_match = re.search(r"(\d{2}:\d{2})\s*[-~～]\s*(\d{2}:\d{2})", cells[2].get_text(" ", strip=True))
        member_id = ""
        for link in row.find_all("a", href=re.compile(r"/member/(?:edit/)?\d+")):
            member_id = re.search(r"/member/(?:edit/)?(\d+)", link.get("href", "")).group(1)
            break
        address = ""
        for line in cells[1].get_text("\n", strip=True).splitlines():
            if _ADDRESS_LINE.match(line.strip()):
                address = line.strip()
                break
        info[order_match.group(0)] = {
            "__address": address,
            "__member_id": member_id,
            "__staff": names,
            "__pay_status": status_match.group(1) if status_match else "",
            "__amount": int(amount_match.group(1).replace(",", "")) if amount_match else None,
            "__period": f"{period_match.group(1)}-{period_match.group(2)}" if period_match else "",
        }
    return info


_ADDRESS_LINE = re.compile(
    r"^(?:台|臺|新北|桃園|台中|臺中|台南|臺南|高雄|基隆|新竹|嘉義|苗栗|彰化|南投|雲林|屏東|宜蘭|花蓮|台東|臺東)"
    r".{0,3}[市縣].*[區鄉鎮市路街]"
)


def _city_from_address(address) -> str:
    """依服務地址的縣市判斷地區；判斷不出來回傳空字串（呼叫端改用抓到訂單的帳號地區）。"""
    text = str(address or "").strip().replace("臺", "台")
    if text.startswith("新竹"):
        return "新竹"
    if text.startswith(("高雄", "台南")):
        return "高雄"
    if text.startswith("桃園"):
        return "桃園"
    if text.startswith("台中"):
        return "台中"
    if text.startswith(("台北", "新北")):
        return "台北"
    return ""


def _purchase_member_id(item) -> str:
    """訂單所屬會員編號：優先用訂單 JSON 的 member_id，否則用訂單列上的會員連結。"""
    value = item.get("member_id") or item.get("memberId") or item.get("__member_id") or ""
    return str(value).strip()


def _member_id_from_edit_page(session, order_no: str) -> str:
    """打開訂單修改頁，從頁面資料（member_id）或會員連結找出訂單所屬會員編號。"""
    digits = re.sub(r"\D", "", str(order_no or ""))
    if not digits:
        return ""
    response = session.get(f"{PURCHASE_URL}/edit/{int(digits)}", headers=HEADERS, allow_redirects=True)
    if response.status_code != 200:
        return ""
    text = response.text
    for pattern in (
        r"[\"']member_id[\"']\s*:\s*[\"']?(\d+)",
        r"member_id[\"']?\s*[=:]\s*[\"']?(\d+)",
        r"/member/(?:edit/)?(\d+)",
    ):
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return ""


def _discover_member_ids(session, items):
    """找出這個地區保留單所屬的會員編號。回傳 (會員編號集合, 找不到時的說明)。

    先看訂單資料／訂單列連結；某位顧客名下的訂單都沒有時，各抽一張打開修改頁找。
    """
    reserve = [i for i in items if not _purchase_is_cancelled(i) and _purchase_is_reserve(i)]
    ids, messages = set(), []
    known_names, missing = set(), {}
    for item in reserve:
        name = str(item.get("name") or item.get("customer_name") or "")
        member_id = _purchase_member_id(item)
        if member_id:
            ids.add(member_id)
            known_names.add(name)
        else:
            missing.setdefault(name, item)
    for name, item in missing.items():
        if name in known_names:
            continue
        order_no = str(item.get("order_no") or item.get("orderNo") or "")
        try:
            member_id = _member_id_from_edit_page(session, order_no)
        except Exception as exc:
            member_id, order_no = "", f"{order_no}（{exc}）"
        if member_id:
            ids.add(member_id)
        else:
            messages.append(f"找不到會員編號（顧客「{name}」，訂單 {order_no}）")
    if reserve and not ids:
        messages.append("訂單資料欄位：" + ",".join(sorted(reserve[0].keys())))
    return ids, messages


def _parse_stored_value_rows(html: str) -> list:
    """儲值金歷程表格：預約日期／服務日期／訂單編號／項目／入帳金額／扣款金額／餘額／備註。

    回傳 [(訂單編號, 入帳金額, 扣款金額)]，入帳為正數、扣款為負數（沒有填就是 0）。
    """
    rows = []
    try:
        soup = BeautifulSoup(str(html or ""), "lxml")
    except Exception:
        soup = BeautifulSoup(str(html or ""), "html.parser")
    for tr in soup.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 6:
            continue
        order_match = re.search(r"\b[A-Z]{2}\d{5,}\b", cells[2].get_text(" ", strip=True))
        if not order_match:
            continue

        def money(cell):
            digits = re.sub(r"[^\d\-]", "", cell.get_text(strip=True))
            return int(digits) if digits not in ("", "-") else 0

        rows.append((order_match.group(0), money(cells[4]), money(cells[5])))
    return rows


def _fetch_stored_value_deductions(session, member_id, page_workers=6, max_pages=150, stats=None) -> dict:
    """彙總儲值金歷程：回傳 {訂單編號: 實際扣儲值金金額}（扣款合計 − 返還合計）。

    stats（選填的 dict）會填入 pages（讀到資料的頁數）、rows（歷程筆數），供診斷。
    """
    if stats is None:
        stats = {}
    stats.update({"pages": 0, "rows": 0})
    url = f"https://backend.lemonclean.com.tw/member/{member_id}/stored_value_histories"
    spent, returned = {}, {}
    seen_signatures = set()

    def fetch_page(page):
        response = session.get(url, params={"page": str(page)}, headers=HEADERS, allow_redirects=True)
        response.raise_for_status()
        return response.text

    page = 1
    while page <= max_pages:
        pages = list(range(page, min(page + page_workers, max_pages + 1)))
        with ThreadPoolExecutor(max_workers=len(pages)) as pool:
            texts = list(pool.map(fetch_page, pages))
        finished = False
        for text in texts:
            rows = _parse_stored_value_rows(text)
            signature = tuple(rows[:3])
            if not rows or signature in seen_signatures:
                finished = True  # 空頁，或頁碼不被支援而回到重複的頁面
                break
            seen_signatures.add(signature)
            stats["pages"] += 1
            stats["rows"] += len(rows)
            for order_no, income, deduct in rows:
                if deduct:
                    spent[order_no] = spent.get(order_no, 0) + abs(deduct)
                if income:
                    returned[order_no] = returned.get(order_no, 0) + income
        if finished:
            break
        page += len(pages)
    return {no: spent.get(no, 0) - returned.get(no, 0) for no in set(spent) | set(returned)}


def _fetch_purchase_items(session, page_workers=1, **filters):
    """依條件讀取所有分頁訂單並以訂單編號去重。

    page_workers > 1 時一次同時抓多頁（一批 page_workers 頁），再依頁碼順序處理；
    遇到空頁或不滿 20 筆的頁就停止。預設 1 = 原本逐頁抓取的行為。
    """
    seen = {}
    max_pages = 80
    wave = max(1, int(page_workers or 1))

    def fetch_page(page):
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
        return response.text

    def handle_page(text):
        """回傳 True 表示還有下一頁。"""
        items = _extract_purchase_list_data(text)
        if not items:
            return False
        row_info = _extract_purchase_row_info(text)
        for item in items:
            order_no = str(item.get("order_no") or item.get("orderNo") or item.get("purchase_id") or "").strip()
            if order_no:
                item.update(row_info.get(order_no, {}))
                seen[order_no] = item
        return len(items) >= 20

    page = 1
    while page <= max_pages:
        pages = list(range(page, min(page + wave, max_pages + 1)))
        if len(pages) == 1:
            texts = [fetch_page(pages[0])]
        else:
            with ThreadPoolExecutor(max_workers=len(pages)) as pool:
                texts = list(pool.map(fetch_page, pages))
        more = True
        for text in texts:
            if not handle_page(text):
                more = False
                break
        if not more:
            break
        page += len(pages)
    return list(seen.values())


def _purchase_amount(item) -> int:
    return safe_int(item.get("total") or item.get("amount") or item.get("price") or 0)


def _purchase_is_cancelled(item) -> bool:
    return bool(item.get("cancel_at") or item.get("cancel_log") or str(item.get("purchase_status")) in {"cancel", "cancelled"})


def _purchase_is_reserve(item) -> bool:
    searchable = json.dumps(item, ensure_ascii=False, default=str)
    name = str(item.get("name") or item.get("customer_name") or "")
    return "系統保留單" in searchable or "大掃除檸檬保留單" in searchable or "保留" in name or "檸檬" in name


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
        try:
            start_dt = datetime.strptime(str(item.get("period_s") or "")[:5], "%H:%M")
            end_dt = datetime.strptime(str(item.get("period_e") or "")[:5], "%H:%M")
            hours = max(0, (end_dt - start_dt).total_seconds() / 3600)
        except Exception:
            hours = 0
    return people * hours


def _purchase_people(item) -> float:
    """保留單的專員人數。"""
    try:
        return float(item.get("person") or item.get("people") or item.get("cleaner_count") or 1)
    except Exception:
        return 1


# 「月份業績統整」頁籤：當月、次月、次次月、次次次月的保留單明細（日期／星期／時數／專員）。
RESERVE_ORDER_MONTH_NAMES = ["當月", "次月", "次次月", "次次次月"]
RESERVE_ORDERS_FILE = "reserve_orders.csv"
RESERVE_ORDER_COLS = [
    "月份別", "月份", "地區", "訂單編號", "日期", "星期", "服務時段",
    "總人時", "專員人數", "專員姓名", "訂單金額", "付款狀態",
]
_WEEKDAY_NAMES = ["一", "二", "三", "四", "五", "六", "日"]


def _reserve_order_month_ranges(base: Optional[datetime] = None):
    base = (base or now_dt()).replace(day=1)
    ranges = []
    for offset, name in enumerate(RESERVE_ORDER_MONTH_NAMES):
        month_index = base.month - 1 + offset
        year = base.year + month_index // 12
        month = month_index % 12 + 1
        ranges.append((
            name,
            f"{year}/{month:02d}",
            f"{year}-{month:02d}-01",
            f"{year}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}",
        ))
    return ranges


def build_reserve_orders_df(records, month_ranges, deductions=None) -> pd.DataFrame:
    """把保留單逐筆列出：日期、星期、時數（人數 × 每人服務時數）、專員（人數）。

    deductions：{訂單編號: 實際扣儲值金金額}；有的話訂單金額用它，沒有的訂單退回用訂單列的「總金額」。
    """
    deductions = deductions or {}
    rows = []
    # 各地區帳號看到的訂單會重疊（例如新竹單在新竹、高雄後台都查得到），所以依訂單編號去重。
    # 地區 = 「查得到這張單的後台帳號」；只有同一張單被多個帳號查到時，才用服務地址挑其中一個。
    # （不能單看服務地址：例如新北地址的單可能屬於桃園後台，取消與金額都得用那個後台的帳號。）
    city_rank = {city: i for i, city in enumerate(CITY_ORDER)}
    grouped, order_keys = {}, []
    for item in records:
        key = str(item.get("order_no") or item.get("orderNo") or item.get("purchase_id") or "")
        if key not in grouped:
            grouped[key] = []
            order_keys.append(key)
        grouped[key].append(item)
    unique_records = []
    for key in order_keys:
        group = sorted(grouped[key], key=lambda r: city_rank.get(r.get("__city"), len(city_rank)))
        address_city = next(
            (c for c in (_city_from_address(r.get("__address") or r.get("address")) for r in group) if c), ""
        )
        chosen = next((r for r in group if r.get("__city") == address_city), group[0])
        unique_records.append(chosen)
    records = unique_records
    for month_name, label, start, end in month_ranges:
        for item in records:
            if _purchase_is_cancelled(item) or not _purchase_is_reserve(item):
                continue
            pay_status = str(item.get("__pay_status") or "")
            if pay_status and pay_status != "已付款":
                continue  # 只列付款狀態為「已付款」的保留單；讀不到狀態時不排除
            date_text = str(item.get("date_clean") or item.get("service_date") or "")[:10]
            if not (start <= date_text <= end):
                continue
            try:
                weekday = "週" + _WEEKDAY_NAMES[datetime.strptime(date_text, "%Y-%m-%d").weekday()]
            except ValueError:
                weekday = ""
            people = _purchase_people(item)
            staff_names = [str(n) for n in (item.get("__staff") or []) if str(n).strip()]
            amount = item.get("__amount")
            order_no = str(item.get("order_no") or item.get("orderNo") or item.get("purchase_id") or "")
            if order_no in deductions:
                amount = deductions[order_no]
            rows.append({
                "月份別": month_name,
                "月份": label,
                "地區": item.get("__city", ""),
                "訂單編號": order_no,
                "日期": date_text,
                "星期": weekday,
                "服務時段": str(item.get("__period") or ""),
                "總人時": _purchase_person_hours(item),
                "專員人數": int(people) if float(people).is_integer() else people,
                "專員姓名": " X ".join(staff_names),
                "訂單金額": int(amount) if amount is not None else _purchase_amount(item),
                "付款狀態": pay_status,
            })
    df = pd.DataFrame(rows, columns=RESERVE_ORDER_COLS)
    if df.empty:
        return df
    city_rank = {city: i for i, city in enumerate(CITY_ORDER)}
    df["__c"] = df["地區"].map(lambda c: city_rank.get(c, len(city_rank)))
    df["__m"] = df["月份別"].map(RESERVE_ORDER_MONTH_NAMES.index)
    return df.sort_values(["__m", "日期", "__c"]).drop(columns=["__c", "__m"]).reset_index(drop=True)


def generate_reserve_orders_report(trigger="dashboard"):
    """只更新「月份業績統整」的保留單明細（當月～次次次月），並行處理各地區。"""
    month_ranges = _reserve_order_month_ranges()

    def worker(city):
        session = requests.Session()
        account = ACCOUNTS[city]
        login(session, account["email"], account["password"])

        # 4 個月各自查詢並同時進行（原本是一個長區間逐頁抓，最慢）。
        def fetch_month(month_range):
            return _fetch_purchase_items(
                session,
                page_workers=3,  # 每個月一次同時抓 3 頁
                clean_date_s=month_range[2],
                clean_date_e=month_range[3],
            )

        items = []
        with ThreadPoolExecutor(max_workers=len(month_ranges)) as month_pool:
            for month_items in month_pool.map(fetch_month, month_ranges):
                items.extend(month_items)
        for item in items:
            item["__city"] = city

        # 訂單金額 = 保留單會員的儲值金歷程扣款。每個地區的保留單會員編號不同，
        # 從該地區保留單本身取得，並用該地區帳號讀取；失敗只影響金額（退回訂單列總金額）。
        found_ids, discover_messages = _discover_member_ids(session, items)
        member_ids = sorted(found_ids)
        city_deductions, city_errors = {}, [f"{city}：{m}" for m in discover_messages]
        for member_id in member_ids:
            try:
                stats = {}
                found = _fetch_stored_value_deductions(session, member_id, stats=stats)
                for order_no, amount in found.items():
                    city_deductions[order_no] = city_deductions.get(order_no, 0) + amount
                reserve_nos = {
                    str(i.get("order_no") or i.get("orderNo") or "") for i in items
                    if not _purchase_is_cancelled(i) and _purchase_is_reserve(i)
                }
                hit = len(reserve_nos & set(found))
                city_errors.append(
                    f"{city}（診斷）會員 {member_id}：歷程讀到 {stats['pages']} 頁／{stats['rows']} 筆，"
                    f"涵蓋 {len(found)} 張訂單，對上保留單 {hit}／{len(reserve_nos)} 張"
                )
            except Exception as exc:
                city_errors.append(f"{city} 會員 {member_id} 儲值金歷程抓取失敗，金額改用訂單列總金額：{exc}")
        return items, city_deductions, city_errors

    cities = [city for city in CITY_ORDER if city in ACCOUNTS]
    records, errors = [], []
    deductions = {}
    with ThreadPoolExecutor(max_workers=max(1, len(cities))) as pool:
        futures = {pool.submit(worker, city): city for city in cities}
        for future in as_completed(futures):
            city = futures[future]
            try:
                city_items, city_deductions, city_errors = future.result()
                records.extend(city_items)
                deductions.update(city_deductions)
                errors.extend(city_errors)
            except Exception as exc:
                errors.append(f"{city} 保留單抓取失敗：{exc}")
                log(f"⚠️ {errors[-1]}")
    error_text = " / ".join(errors) if errors else None

    df = build_reserve_orders_df(records, month_ranges, deductions)

    ensure_dirs()
    path = os.path.join(LATEST_DIR, RESERVE_ORDERS_FILE)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    append_output_file_log("保留單明細", path, trigger)

    # 只更新 meta.json 裡保留單相關的欄位，其他欄位原樣保留。
    meta_path = os.path.join(LATEST_DIR, "meta.json")
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except Exception:
        meta = {}
    meta.update({
        "reserve_orders_rows": int(len(df)),
        "reserve_orders_updated_at": now_dt().strftime("%Y-%m-%d %H:%M:%S"),
        "reserve_orders_error": error_text,
    })
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    return {"reserve_orders_df": df, "error": error_text}


def build_order_date_summary(raw_df: pd.DataFrame, month_rows=None) -> pd.DataFrame:
    """依地區統計待付款／已付款／合計，待付款/已付款底下再依「服務日期」拆出固定的
    月份欄位（本月＋4個月），並把「儲值金」拆成同一列裡的獨立欄位（只分待付款/
    已付款，不拆月份）。

    raw_df 跟 build_month_performance_summary() 吃的是同一種資料形狀（城市/收入類型/
    服務/已付款/待付款，來自同一個報表頁面裡「財務彙總表」的 parse_html() 結果），用
    to_category()／detect_income_type() 分類，才會跟「目前總表」的儲值金判斷邏輯一致，
    是「待付款/已付款/儲值金待付款/儲值金已付款」這幾個總額欄位唯一的資料來源。

    財務彙總表本身沒有服務日期（依服務分類彙總，不是逐筆訂單），月份拆分改吃
    month_rows：list of {"城市","月份","已付款","待付款"}，每個月份都是額外用
    「訂購日期＋服務日期」兩個條件一起查一次財務彙總表算出來的（見
    generate_order_date_report()／_order_date_month_ranges()），不是自己逐筆訂單
    加總——這樣金額口徑才會跟地區/加總欄位完全一致，不會有稅前/稅後金額混淆、
    分頁漏單、儲值金訂單總金額顯示 0 之類的問題。上方三張表只統計清潔類服務，
    家電（冷氣、洗衣機）、水洗與收納都不列入。月份欄位只影響「待付款/已付款」
    底下的拆分，不影響地區/加總/儲值金這幾個主要欄位的數字（那些永遠以 raw_df
    為準）；month_rows 缺漏時就不會有月份欄位，不會出錯。
    """
    base_cols = ["地區", "待付款", "已付款", "待付款＋已付款"]
    stored_value_cols = ["儲值金待付款", "儲值金已付款", "儲值金待付款＋已付款"]
    if raw_df.empty:
        return pd.DataFrame(columns=base_cols + stored_value_cols)

    work = raw_df.copy()
    work["類別"] = work.apply(lambda r: to_category(r["服務"], r["收入類型"]), axis=1)

    # 上方待付款、已付款與兩者合計只統計清潔類；家電、水洗、收納另有明細表，
    # 不在這裡重複計算。未分類的新服務名稱仍保留，避免新服務無預警漏帳。
    service_df = work[
        ~work["類別"].isin(ORDER_DATE_SUMMARY_EXCLUDED_CATEGORIES)
    ].copy()
    stored_value_df = work[(work["收入類型"] == "現金收入") & (work["類別"] == "儲值金")]

    month_df = pd.DataFrame(month_rows) if month_rows else pd.DataFrame()
    months = sorted(month_df["月份"].unique().tolist()) if not month_df.empty else []
    month_cols = [f"{m}{kind}" for m in months for kind in ("待付款", "已付款", "待付款＋已付款")]

    cols = base_cols + month_cols + stored_value_cols

    rows = []
    for city in CITY_ORDER:
        svc_sub = service_df[service_df["城市"] == city]
        sv_sub = stored_value_df[stored_value_df["城市"] == city]
        unpaid = svc_sub["待付款"].sum()
        paid = svc_sub["已付款"].sum()
        row = {
            "地區": city,
            "待付款": unpaid,
            "已付款": paid,
            "待付款＋已付款": unpaid + paid,
        }
        for m in months:
            m_sub = month_df[(month_df["城市"] == city) & (month_df["月份"] == m)]
            m_unpaid = m_sub["待付款"].sum() if not m_sub.empty else 0
            m_paid = m_sub["已付款"].sum() if not m_sub.empty else 0
            row[f"{m}待付款"] = m_unpaid
            row[f"{m}已付款"] = m_paid
            row[f"{m}待付款＋已付款"] = m_unpaid + m_paid
        sv_unpaid = sv_sub["待付款"].sum()
        sv_paid = sv_sub["已付款"].sum()
        row["儲值金待付款"] = sv_unpaid
        row["儲值金已付款"] = sv_paid
        row["儲值金待付款＋已付款"] = sv_unpaid + sv_paid
        rows.append(row)

    out = pd.DataFrame(rows, columns=cols)
    out = pd.concat([out, pd.DataFrame([{
        "地區": "加總",
        **{c: out[c].sum() for c in cols[1:]},
    }])], ignore_index=True)
    return out[cols]


def build_month_performance_summary(raw_df: pd.DataFrame, month_ranges) -> pd.DataFrame:
    cols = ["地區"] + [f"{label}業績" for label, _, _ in month_ranges]
    if raw_df.empty:
        work = pd.DataFrame(columns=["城市", "月份", "業績"])
    else:
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
    rows.append({"地區": "加總", **{col: sum(row[col] for row in rows) for col in cols[1:]}})
    return pd.DataFrame(rows, columns=cols)


def build_reserve_summary(records, month_ranges) -> pd.DataFrame:
    cols = ["地區"]
    for label, _, _ in month_ranges:
        cols.extend([f"{label}保留單時數", f"{label}保留單業績"])
    rows = []
    for city in CITY_ORDER:
        row = {col: 0 for col in cols}
        row["地區"] = city
        city_records = [x for x in records if x.get("__city") == city and not _purchase_is_cancelled(x) and _purchase_is_reserve(x)]
        for label, start, end in month_ranges:
            selected = [x for x in city_records if start <= str(x.get("date_clean") or x.get("service_date") or "")[:10] <= end]
            row[f"{label}保留單時數"] = sum(_purchase_person_hours(x) for x in selected)
            row[f"{label}保留單業績"] = sum(_purchase_amount(x) for x in selected)
        rows.append(row)
    rows.append({"地區": "加總", **{col: sum(float(row[col]) for row in rows) for col in cols[1:]}})
    return pd.DataFrame(rows, columns=cols)


def build_net_performance_summary(raw_df: pd.DataFrame, reserve_df: pd.DataFrame, month_ranges) -> pd.DataFrame:
    cols = ["地區"] + [f"{label}業績－保留單業績" for label, _, _ in month_ranges]
    performance_df = build_month_performance_summary(raw_df, month_ranges)
    rows = []
    for city in CITY_ORDER:
        row = {"地區": city}
        reserve_row = reserve_df[reserve_df["地區"].astype(str) == city]
        performance_row = performance_df[performance_df["地區"].astype(str) == city]
        for label, _, _ in month_ranges:
            gross = 0 if performance_row.empty else safe_int(performance_row.iloc[0].get(f"{label}業績", 0))
            reserve = 0 if reserve_row.empty else safe_int(reserve_row.iloc[0].get(f"{label}保留單業績", 0))
            row[f"{label}業績－保留單業績"] = gross - reserve
        rows.append(row)
    rows.append({"地區": "加總", **{col: sum(row[col] for row in rows) for col in cols[1:]}})
    return pd.DataFrame(rows, columns=cols)


def _update_latest_meta(**changes):
    """只更新本次報表相關欄位，保留其他頁籤的最近篩選與筆數。"""
    ensure_dirs()
    path = os.path.join(LATEST_DIR, "meta.json")
    meta = {}
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                meta = json.load(f)
    except Exception:
        meta = {}
    meta.update(changes)
    meta["updated_at"] = now_dt().strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def _parallel_city_results(worker):
    enabled = [city for city in CITY_ORDER if city in ACCOUNTS]
    if not enabled:
        raise RuntimeError("ACCOUNTS 沒有任何可用城市設定")
    results = []
    errors = []
    with ThreadPoolExecutor(max_workers=min(5, len(enabled))) as executor:
        futures = {executor.submit(worker, city): city for city in enabled}
        for future in as_completed(futures):
            city = futures[future]
            try:
                results.append((city, future.result()))
            except Exception as exc:
                errors.append(f"{city}：{exc}")
                log(f"❌ {city}：{exc}")
    if not results and errors:
        raise RuntimeError("所有地區更新失敗：" + " / ".join(errors))
    return results, errors


def _order_date_month_ranges():
    """訂購日期付款彙總拆月份用的固定區間：本月＋4個月，總共 5 個月份。"""
    base = now_dt().replace(day=1)
    month_index = base.month - 1 + 4
    year = base.year + month_index // 12
    month = month_index % 12 + 1
    end_month = f"{year}-{month:02d}"
    return get_report_month_ranges(base.strftime("%Y-%m"), end_month)


def generate_order_date_report(order_start_date: str, order_end_date: str, trigger="dashboard"):
    """只更新付款彙總，不重抓目前總表與月份保留單。

    跟「月份業績統整」共用同一個報表頁面／parse_html() 解析邏輯，只是查詢用「訂購
    日期」（date_s/date_e）而不是「清潔／服務日期」，這樣「儲值金」判斷才會跟「目前
    總表」一致（見 build_order_date_summary()）。

    月份拆分不是逐筆訂單自己加總（會遇到未稅金額、分頁、儲值金訂單總金額顯示 0
    等各種問題），而是每個月份都用「訂購日期＋服務日期」兩個條件一起查詢一次
    財務彙總表，直接拿後端算好的已付款/待付款金額——跟地區/加總欄位用同一套
    parse_html()/to_category() 邏輯，金額口徑保證一致。固定查「本月＋4個月」
    （見 _order_date_month_ranges()），涵蓋不到的月份就不會有資料。

    本月這個月份用服務日期「不限起日、只限本月底」查（逾期未結的訂單服務日期
    可能落在本月以前，這樣才不會漏掉），之後 4 個月才各自用整月起訖日查、彼此
    不重疊。儲值金儲值單本身沒有服務日期，若落進「不限起日」這種查詢會被算進
    去，但每一輪都還是用 to_category() 排除儲值金、家電、水洗與收納，所以不會
    滲進月份欄位裡（跟地區/加總欄位使用完全相同的排除規則）。
    """
    if order_end_date < order_start_date:
        raise ValueError("訂購日期迄日不可早於起日")

    month_ranges = _order_date_month_ranges()

    def worker(city):
        session = requests.Session()
        account = ACCOUNTS[city]
        login(session, account["email"], account["password"])
        merged = {}
        month_totals = {}
        for status in [1, 0]:
            for keyword in get_keywords(city):
                response = session.get(
                    build_url(order_start_date, order_end_date, status, keyword, use_order_date=True),
                    headers=HEADERS, allow_redirects=True,
                )
                response.raise_for_status()
                for row in parse_html(response.text):
                    key = (row["日期"], row["收入類型"], row["資料來源"], row["服務"], row["子項目"])
                    if key not in merged:
                        merged[key] = {
                            "城市": city, "日期": row["日期"],
                            "收入類型": row["收入類型"], "資料來源": row["資料來源"],
                            "服務": row["服務"], "子項目": row["子項目"],
                            "已付款": 0, "待付款": 0,
                        }
                    merged[key]["已付款"] += row["已付款"]
                    merged[key]["待付款"] += row["待付款"]

                for idx, (label, m_start, m_end) in enumerate(month_ranges):
                    # 第一個月份（本月）用服務日期「不限起日、迄本月底」查，才會把服務
                    # 日期落在本月以前（逾期未結）的訂單也收進來，不會因為劃了本月起日
                    # 而漏掉；之後每個月才用整月的起訖日各自查一次，彼此不重疊。
                    clean_start = None if idx == 0 else m_start
                    month_response = session.get(
                        build_url(order_start_date, order_end_date, status, keyword,
                                  use_order_date=True, clean_start=clean_start, clean_end=m_end),
                        headers=HEADERS, allow_redirects=True,
                    )
                    month_response.raise_for_status()
                    for row in parse_html(month_response.text):
                        category = to_category(row["服務"], row["收入類型"])
                        if category in ORDER_DATE_SUMMARY_EXCLUDED_CATEGORIES:
                            continue
                        month_key = (city, label)
                        if month_key not in month_totals:
                            month_totals[month_key] = {"城市": city, "月份": label, "已付款": 0, "待付款": 0}
                        month_totals[month_key]["已付款"] += row["已付款"]
                        month_totals[month_key]["待付款"] += row["待付款"]
        return list(merged.values()), list(month_totals.values())

    city_results, errors = _parallel_city_results(worker)
    raw_rows = [row for _, (rows, _) in city_results for row in rows]
    month_rows = [row for _, (_, rows) in city_results for row in rows]
    raw_df = pd.DataFrame(raw_rows)
    out = build_order_date_summary(raw_df, month_rows=month_rows)
    ensure_dirs()
    path = os.path.join(LATEST_DIR, "order_date_summary.csv")
    out.to_csv(path, index=False, encoding="utf-8-sig")
    append_output_file_log("訂購日期付款彙總", path, trigger)
    _update_latest_meta(
        order_start_date=order_start_date,
        order_end_date=order_end_date,
        order_date_rows=int(len(out)),
        order_date_updated_at=now_dt().strftime("%Y-%m-%d %H:%M:%S"),
        order_date_error=" / ".join(errors) if errors else None,
    )
    return out


def generate_month_range_reports(report_start_month: str, report_end_month: str, trigger="dashboard"):
    """只更新月份業績、保留單及扣除後業績，並行處理各地區。"""
    month_ranges = get_report_month_ranges(report_start_month, report_end_month)

    def worker(city):
        session = requests.Session()
        account = ACCOUNTS[city]
        login(session, account["email"], account["password"])
        merged = {}
        for label, start, end in month_ranges:
            for status in [1, 0]:
                for keyword in get_keywords(city):
                    response = session.get(build_url(start, end, status, keyword), headers=HEADERS, allow_redirects=True)
                    response.raise_for_status()
                    for row in parse_html(response.text):
                        key = (label, row["日期"], row["收入類型"], row["資料來源"], row["服務"], row["子項目"])
                        if key not in merged:
                            merged[key] = {
                                "城市": city, "月份": label, "日期": row["日期"],
                                "收入類型": row["收入類型"], "資料來源": row["資料來源"],
                                "服務": row["服務"], "子項目": row["子項目"],
                                "已付款": 0, "待付款": 0,
                            }
                        merged[key]["已付款"] += row["已付款"]
                        merged[key]["待付款"] += row["待付款"]
        reserve_items = _fetch_purchase_items(
            session,
            clean_date_s=month_ranges[0][1],
            clean_date_e=month_ranges[-1][2],
        )
        for item in reserve_items:
            item["__city"] = city
        return list(merged.values()), reserve_items

    city_results, errors = _parallel_city_results(worker)
    raw_rows = [row for _, (rows, _) in city_results for row in rows]
    reserve_records = [item for _, (_, items) in city_results for item in items]
    raw_df = pd.DataFrame(raw_rows)
    performance_df = build_month_performance_summary(raw_df, month_ranges)
    reserve_df = build_reserve_summary(reserve_records, month_ranges)
    net_df = build_net_performance_summary(raw_df, reserve_df, month_ranges)

    ensure_dirs()
    outputs = [
        (performance_df, os.path.join(LATEST_DIR, "month_performance_summary.csv")),
        (reserve_df, os.path.join(LATEST_DIR, "reserve_summary.csv")),
        (net_df, os.path.join(LATEST_DIR, "net_performance_summary.csv")),
    ]
    for frame, path in outputs:
        frame.to_csv(path, index=False, encoding="utf-8-sig")
        append_output_file_log("月份業績統整", path, trigger)
    _update_latest_meta(
        report_start_month=month_ranges[0][0],
        report_end_month=month_ranges[-1][0],
        month_performance_rows=int(len(performance_df)),
        reserve_rows=int(len(reserve_df)),
        net_performance_rows=int(len(net_df)),
        month_report_updated_at=now_dt().strftime("%Y-%m-%d %H:%M:%S"),
        month_report_error=" / ".join(errors) if errors else None,
    )
    return {
        "month_performance_df": performance_df,
        "reserve_df": reserve_df,
        "net_performance_df": net_df,
        "error": " / ".join(errors) if errors else None,
    }


def build_url(start, end, status, keyword="", use_order_date=False, clean_start=None, clean_end=None):
    """組出報表頁查詢網址。

    預設用「清潔／服務日期」（clean_date_s/clean_date_e）篩選，這是「月份業績統整」
    在用的欄位。use_order_date=True 時改填「訂購日期」（date_s/date_e），這是
    「訂購日期付款彙總」在用的欄位——兩者是同一個報表頁面，欄位不同而已。

    clean_start/clean_end：另外指定「服務日期」區間，跟 use_order_date 無關，用在
    「訂購日期＋服務日期」要同時套用篩選的情境（訂購日期付款彙總依服務日期拆月份，
    就是靠這個參數讓查詢同時帶 date_s/date_e 跟 clean_date_s/clean_date_e，讓後端
    直接算出該月份彙總表的已付款/待付款金額，不用自己逐筆訂單加總、也不會有稅前/
    稅後金額搞混或分頁漏單的問題）。
    """
    params = {
        "keyword": keyword,
        "name": "",
        "phone": "",
        "orderNo": "",
        "date_s": start if use_order_date else "",
        "date_e": end if use_order_date else "",
        "clean_date_s": clean_start if clean_start is not None else ("" if use_order_date else start),
        "clean_date_e": clean_end if clean_end is not None else ("" if use_order_date else end),
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
                today = datetime.today()
                dt = datetime.strptime(f"{today.year}-{raw}", "%Y-%m-%d")
                return dt.strftime("%Y-%m-%d")
            if re.fullmatch(r"\d{1,2}/\d{1,2}", raw):
                today = datetime.today()
                dt = datetime.strptime(f"{today.year}/{raw}", "%Y/%m/%d")
                return dt.strftime("%Y-%m-%d")
        except Exception:
            pass

    return None


def parse_html(html):
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
        weekly_idx = header.index("週末加價") if "週末加價" in header else None

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

            # 用儲值金付款時，週末加價是從儲值金餘額另外扣款的一筆（跟會員的儲值金歷程
            # 明細對得起來），彙總表把它跟已付款金額拆成兩欄，「已付款金額」欄本身沒有
            # 算進去，要另外加回來才是這筆訂單實際入帳的金額。
            if income_type == "儲值金" and weekly_idx is not None and len(row) > weekly_idx:
                paid += safe_int(row[weekly_idx])

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

                row = {"城市": city, "收入類型": income, "類別": category}
                for key, name in zip(REPORT_MONTH_KEYS, REPORT_MONTH_NAMES):
                    m = sub[sub["月份"] == key]
                    row[f"{name}待付"] = m["待付款"].sum()
                    row[f"{name}已付"] = m["已付款"].sum()
                    row[f"{name}加總"] = m["已付款"].sum() + m["待付款"].sum()
                rows.append(row)

    return pd.DataFrame(rows)


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

        cash_stored_df = city_df[
            (city_df["收入類型"] == "現金收入") &
            (city_df["類別"] == "儲值金")
        ]
        bm_cash_stored = cash_stored_df["本月加總"].sum()
        nm_cash_stored = cash_stored_df["次月加總"].sum()

        # 各月份加總（本月～次次次次月）：清潔服務業績。
        # 規則：
        # 1) 不論收入類型是「現金收入」或「儲值金」，都必須先依服務分類。
        # 2) 只有「清潔」類別納入各月份加總。
        # 3) 儲值金中的冷氣、洗衣機屬於家電，只放在各月份「家電加總」，不放進各月份加總。
        # 4) 儲值金中的水洗屬於水洗，不放進各月份加總。
        # 5) 現金收入的「儲值金購買」是預收款，不屬於服務業績，不放進各月份加總。
        total_df = city_df[city_df["類別"] == "清潔"]

        row = {"城市": city}
        for name in REPORT_MONTH_NAMES:
            col = f"{name}加總"
            row[col] = total_df[col].sum()
            row[f"{name}家電加總"] = appliance_df[col].sum()
        # 儲值金維持原本口徑（本月＋次月的現金收入儲值金購買）。
        row["儲值金"] = bm_cash_stored + nm_cash_stored
        rows.append(row)

    region4 = pd.DataFrame(rows)

    total_row = {"城市": "加總"}
    for name in REPORT_MONTH_NAMES:
        amount_col = f"{name}加總"
        month_sum = region4[amount_col].sum()
        region4[f"{name}佔比"] = 0 if month_sum == 0 else region4[amount_col] / month_sum
        total_row[amount_col] = month_sum
        total_row[f"{name}佔比"] = 1
        total_row[f"{name}家電加總"] = region4[f"{name}家電加總"].sum()
    total_row["儲值金"] = region4["儲值金"].sum()

    region4 = pd.concat([region4, pd.DataFrame([total_row])], ignore_index=True)

    ordered = ["城市"]
    for name in REPORT_MONTH_NAMES:
        ordered += [f"{name}加總", f"{name}佔比"]
    ordered += REGION4_APPLIANCE_COLS + ["儲值金"]
    return region4[ordered]


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

    offset = REPORT_MONTH_NAMES.index(period_label) if period_label in REPORT_MONTH_NAMES else 0
    month_index = now_obj.month - 1 + offset
    stat_month = f"{now_obj.year + month_index // 12}/{month_index % 12 + 1:02d}"

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

    log(f"✅ {period_label}統計報表完成，筆數 = {len(out)}")
    return out[cols]


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


# 月度追蹤的額外月份分頁：次次月、次次次月、次次次次月。本月、次月仍沿用 daily_df.csv／next_month_daily_df.csv。
EXTRA_PERIOD_FILES = {
    "次次月": "month3_daily_df.csv",
    "次次次月": "month4_daily_df.csv",
    "次次次次月": "month5_daily_df.csv",
}


def build_extra_period_overview_dfs(df4: pd.DataFrame, source: str = "dashboard", run_dt: Optional[datetime] = None) -> dict:
    return {
        name: _build_period_overview_df(
            df4=df4,
            source=source,
            amount_col=f"{name}加總",
            ratio_col=f"{name}佔比",
            latest_filename=filename,
            period_label=name,
            run_dt=run_dt,
        )
        for name, filename in EXTRA_PERIOD_FILES.items()
    }


# 月底快照欄位用「當月／次月…」，df4 欄位用「本月／次月…」，兩邊依序對應。
MONTH_END_NAMES = ["當月", "次月", "次次月", "次次次月", "次次次次月"]
MONTH_END_COLS = (
    ["id", "來源", "快照日期", "城市"]
    + [c for n in MONTH_END_NAMES for c in (f"{n}業績", f"{n}佔比")]
    + [f"{n}總業績" for n in MONTH_END_NAMES]
)


def build_month_end_summary_df(df4: pd.DataFrame, source: str = "dashboard") -> pd.DataFrame:
    cols = MONTH_END_COLS

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

    rows = []
    for city in CITY_ORDER + ["加總"]:
        row = {
            "id": f"{row_id_prefix}_{city}",
            "來源": source,
            "快照日期": snapshot_date,
            "城市": city,
        }
        for snap_name, df4_name in zip(MONTH_END_NAMES, REPORT_MONTH_NAMES):
            row[f"{snap_name}業績"] = get_val(city, f"{df4_name}加總")
            row[f"{snap_name}佔比"] = get_val(city, f"{df4_name}佔比")
            row[f"{snap_name}總業績"] = get_val("加總", f"{df4_name}加總")
        rows.append(row)

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
    cols = MONTH_END_COLS
    if not os.path.exists(MONTH_END_HISTORY_FILE):
        return pd.DataFrame(columns=cols)
    return pd.read_csv(MONTH_END_HISTORY_FILE, encoding="utf-8-sig")

def format_region4_for_display(df4: pd.DataFrame) -> pd.DataFrame:
    out = df4.copy()
    for col in REGION4_INT_COLS:
        if col in out.columns:
            out[col] = out[col].apply(lambda x: int(x) if pd.notna(x) else 0)
    return out


def build_region4_email_html(df4):
    mail_df = df4.copy()

    for col in REGION4_INT_COLS:
        if col in mail_df.columns:
            mail_df[col] = mail_df[col].apply(lambda x: f"{int(x):,}" if pd.notna(x) else "")

    for col in REGION4_RATIO_COLS:
        if col in mail_df.columns:
            mail_df[col] = mail_df[col].apply(lambda x: f"{x:.2%}" if pd.notna(x) else "")

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


def _email_setting(name: str, default=None):
    value = os.getenv(name)
    if value:
        return value
    try:
        import streamlit as st
        return st.secrets.get(name, default)
    except Exception:
        return default


def _split_email_recipients(raw: str) -> list[str]:
    return [email.strip() for email in str(raw or "").replace(";", ",").split(",") if email.strip()]


def send_region4_email(df4, recipient="jenny@hers.com.tw"):
    sender = _email_setting("NOTIFY_EMAIL", "jenny@hers.com.tw")
    password = _email_setting("NOTIFY_PASSWORD")
    recipients = _split_email_recipients(_email_setting("NOTIFY_TO", recipient))

    if not sender or not password or not recipients:
        log("⚠️ 缺少 NOTIFY_EMAIL / NOTIFY_PASSWORD / NOTIFY_TO，略過寄信")
        return False

    today_str = now_dt().strftime("%Y%m%d")
    subject = f"業績報表{today_str}"
    html = build_region4_email_html(df4)

    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, password)
        server.sendmail(sender, recipients, msg.as_string())

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
    month_performance_df: Optional[pd.DataFrame] = None,
    reserve_df: Optional[pd.DataFrame] = None,
    net_performance_df: Optional[pd.DataFrame] = None,
    report_start_month: Optional[str] = None,
    report_end_month: Optional[str] = None,
    order_start_date: Optional[str] = None,
    order_end_date: Optional[str] = None,
    error_msg: Optional[str] = None,
    trigger: str = "dashboard",
    extra_period_dfs: Optional[dict] = None,
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

    for period_name, period_df in (extra_period_dfs or {}).items():
        extra_path = os.path.join(LATEST_DIR, EXTRA_PERIOD_FILES[period_name])
        period_df.to_csv(extra_path, index=False, encoding="utf-8-sig")
        append_output_file_log(f"{period_name}統計報表", extra_path, trigger)

    if month_end_df is not None and not month_end_df.empty:
        month_end_df.to_csv(latest_month_end, index=False, encoding="utf-8-sig")
        append_output_file_log("月底快照", latest_month_end, trigger)

    with open(latest_html, "w", encoding="utf-8") as f:
        f.write(email_html or "")
    append_output_file_log("業績報表", latest_html, trigger)

    previous_meta = {}
    try:
        if os.path.exists(latest_meta):
            with open(latest_meta, "r", encoding="utf-8") as f:
                previous_meta = json.load(f)
    except Exception:
        previous_meta = {}

    meta = dict(previous_meta)
    meta.update({
        "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "df4_rows": int(len(df4)),
        "daily_rows": int(len(daily_df)),
        "next_month_daily_rows": int(len(next_month_daily_df)),
        "month_end_rows": int(len(month_end_df)),
        "order_date_rows": int(len(order_date_df)) if order_date_df is not None else previous_meta.get("order_date_rows", 0),
        "month_performance_rows": int(len(month_performance_df)) if month_performance_df is not None else previous_meta.get("month_performance_rows", 0),
        "reserve_rows": int(len(reserve_df)) if reserve_df is not None else previous_meta.get("reserve_rows", 0),
        "net_performance_rows": int(len(net_performance_df)) if net_performance_df is not None else previous_meta.get("net_performance_rows", 0),
        "report_start_month": report_start_month if report_start_month is not None else previous_meta.get("report_start_month"),
        "report_end_month": report_end_month if report_end_month is not None else previous_meta.get("report_end_month"),
        "order_start_date": order_start_date if order_start_date is not None else previous_meta.get("order_start_date"),
        "order_end_date": order_end_date if order_end_date is not None else previous_meta.get("order_end_date"),
        "error": error_msg,
        "trigger": trigger,
    })
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
    snap_order_date = f"{snapshot_prefix}_order_date_summary.csv"
    snap_month_performance = f"{snapshot_prefix}_month_performance_summary.csv"
    snap_reserve = f"{snapshot_prefix}_reserve_summary.csv"
    snap_net_performance = f"{snapshot_prefix}_net_performance_summary.csv"

    df4.to_csv(snap_df4, index=False, encoding="utf-8-sig")
    append_output_file_log("業績報表", snap_df4, trigger)

    for report_df, report_path in [
        (order_date_df, snap_order_date),
        (month_performance_df, snap_month_performance),
        (reserve_df, snap_reserve),
        (net_performance_df, snap_net_performance),
    ]:
        if report_df is not None:
            report_df.to_csv(report_path, index=False, encoding="utf-8-sig")
            append_output_file_log("業績報表", report_path, trigger)

    daily_df.to_csv(snap_daily, index=False, encoding="utf-8-sig")
    append_output_file_log("業績報表", snap_daily, trigger)

    next_month_daily_df.to_csv(snap_next_daily, index=False, encoding="utf-8-sig")
    append_output_file_log("次月統計報表", snap_next_daily, trigger)

    for period_name, period_df in (extra_period_dfs or {}).items():
        extra_snap = f"{snapshot_prefix}_{EXTRA_PERIOD_FILES[period_name]}"
        period_df.to_csv(extra_snap, index=False, encoding="utf-8-sig")
        append_output_file_log(f"{period_name}統計報表", extra_snap, trigger)

    if month_end_df is not None and not month_end_df.empty:
        month_end_df.to_csv(snap_month_end, index=False, encoding="utf-8-sig")
        append_output_file_log("月底快照", snap_month_end, trigger)

    with open(snap_meta, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    append_output_file_log("業績報表", snap_meta, trigger)

    with open(snap_html, "w", encoding="utf-8") as f:
        f.write(email_html or "")
    append_output_file_log("業績報表", snap_html, trigger)


def generate_sales_report(
    send_email=False,
    persist_dashboard=True,
    trigger="dashboard",
    report_start_month: Optional[str] = None,
    report_end_month: Optional[str] = None,
    order_start_date: Optional[str] = None,
    order_end_date: Optional[str] = None,
    include_extra_reports: bool = True,
):
    log("🔥 開始業績報表")

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
    # 本月起連續 5 個月（本月、次月、次次月、次次次月、次次次次月）；本月、次月沿用 get_ranges() 的結果。
    month_key_by_label = {
        current_month_label: REPORT_MONTH_KEYS[0],
        next_month_label: REPORT_MONTH_KEYS[1],
    }
    requested_ranges = [
        (current_month_label, m_start, m_end),
        (next_month_label, n_start, n_end),
    ]
    base_year, base_month = int(m_start[:4]), int(m_start[5:7])
    for offset in range(2, len(REPORT_MONTH_KEYS)):
        month_index = base_month - 1 + offset
        year = base_year + month_index // 12
        month = month_index % 12 + 1
        label = f"{year}/{month:02d}"
        month_key_by_label[label] = REPORT_MONTH_KEYS[offset]
        requested_ranges.append((
            label,
            f"{year}-{month:02d}-01",
            f"{year}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}",
        ))
    fetch_month_ranges = []
    seen_months = set()
    if include_extra_reports:
        requested_ranges.extend(report_month_ranges)
    for month_range in requested_ranges:
        if month_range[0] not in seen_months:
            seen_months.add(month_range[0])
            fetch_month_ranges.append(month_range)
    merged = {}
    order_date_records = []
    reserve_records = []
    city_errors = []

    enabled_cities = [city for city in CITY_ORDER if city in ACCOUNTS]
    missing_cities = [city for city in CITY_ORDER if city not in ACCOUNTS]

    if missing_cities:
        log(f"⚠️ ACCOUNTS 缺少城市設定，已略過：{', '.join(missing_cities)}")

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

        return {
            "raw_df": pd.DataFrame(),
            "df1": pd.DataFrame(),
            "df2": pd.DataFrame(),
            "df3": pd.DataFrame(),
            "df4": empty_df4,
            "daily_df": empty_daily,
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

    for city in enabled_cities:
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

                        rows = parse_html(res.text)
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

                            if key not in merged:
                                merged[key] = {
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

                            merged[key]["已付款"] += row["已付款"]
                            merged[key]["待付款"] += row["待付款"]

            if include_extra_reports:
                try:
                    order_items = _fetch_purchase_items(
                        session,
                        date_s=order_start_date,
                        date_e=order_end_date,
                    )
                    for item in order_items:
                        item["__city"] = city
                    order_date_records.extend(order_items)

                    reserve_items = _fetch_purchase_items(
                        session,
                        clean_date_s=report_month_ranges[0][1],
                        clean_date_e=report_month_ranges[-1][2],
                    )
                    for item in reserve_items:
                        item["__city"] = city
                    reserve_records.extend(reserve_items)
                except Exception as extra_exc:
                    msg = f"{city} 新增報表資料抓取失敗：{extra_exc}"
                    city_errors.append(msg)
                    log(f"⚠️ {msg}")

            if city_row_count == 0:
                msg = f"{city}：登入成功，但沒有抓到任何表格資料"
                city_errors.append(msg)
                log(f"⚠️ {msg}")

        except Exception as e:
            msg = f"{city} 失敗：{e}"
            city_errors.append(msg)
            log(f"❌ {msg}")

    report_raw_df = pd.DataFrame(merged.values())
    raw_df = report_raw_df.copy()
    if not raw_df.empty:
        raw_df["月份"] = raw_df["月份"].map(lambda label: month_key_by_label.get(label, label))

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

        return {
            "raw_df": pd.DataFrame(),
            "df1": pd.DataFrame(),
            "df2": pd.DataFrame(),
            "df3": pd.DataFrame(),
            "df4": empty_df4,
            "daily_df": empty_daily,
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
    df3 = build_region3_df(df2)
    df4 = build_region4_df(df2)
    order_date_df = generate_order_date_report(
        order_start_date,
        order_end_date,
        trigger=trigger,
    ) if include_extra_reports else None
    month_performance_df = build_month_performance_summary(report_raw_df, report_month_ranges) if include_extra_reports else None
    reserve_df = build_reserve_summary(reserve_records, report_month_ranges) if include_extra_reports else None
    net_performance_df = build_net_performance_summary(report_raw_df, reserve_df, report_month_ranges) if include_extra_reports else None

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
    extra_period_dfs = build_extra_period_overview_dfs(df4, source=source, run_dt=run_dt)
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
    if include_extra_reports:
        log(f"order_date_df rows = {len(order_date_df)}")
        log(f"month_performance_df rows = {len(month_performance_df)}")
        log(f"reserve_df rows = {len(reserve_df)}")
        log(f"net_performance_df rows = {len(net_performance_df)}")

    email_html = build_region4_email_html(df4)
    error_msg = None if not city_errors else " / ".join(city_errors)

    if persist_dashboard:
        persist_dashboard_payload(
            df4, daily_df, next_month_daily_df, month_end_df, email_html,
            order_date_df=order_date_df,
            month_performance_df=month_performance_df,
            reserve_df=reserve_df,
            net_performance_df=net_performance_df,
            report_start_month=report_month_ranges[0][0] if include_extra_reports else None,
            report_end_month=report_month_ranges[-1][0] if include_extra_reports else None,
            order_start_date=order_start_date if include_extra_reports else None,
            order_end_date=order_end_date if include_extra_reports else None,
            error_msg=error_msg,
            trigger=trigger,
            extra_period_dfs=extra_period_dfs,
        )
        if include_extra_reports:
            # 「月份業績統整」頁籤的保留單明細（當月～次次次月）；失敗不影響主報表。
            try:
                generate_reserve_orders_report(trigger=trigger)
            except Exception as exc:
                log(f"⚠️ 保留單明細更新失敗：{exc}")

    if send_email:
        send_region4_email(df4)

    return {
        "raw_df": raw_df,
        "df1": df1,
        "df2": df2,
        "df3": df3,
        "df4": format_region4_for_display(df4),
        "daily_df": daily_df,
        "next_month_daily_df": next_month_daily_df,
        "extra_period_dfs": extra_period_dfs,
        "month_end_df": month_end_df,
        "order_date_df": order_date_df,
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
    send_email = True
    report_start_month = None
    report_end_month = None
    order_start_date = None
    order_end_date = None

    cleaned_args = []
    raw_args = os.sys.argv[1:]
    index = 0
    while index < len(raw_args):
        arg = raw_args[index]
        if arg in {"--start-month", "--end-month", "--order-start-date", "--order-end-date"}:
            value = raw_args[index + 1] if index + 1 < len(raw_args) else ""
            if arg == "--start-month":
                report_start_month = value
            elif arg == "--end-month":
                report_end_month = value
            elif arg == "--order-start-date":
                order_start_date = value
            elif arg == "--order-end-date":
                order_end_date = value
            index += 2
            continue
        cleaned_args.append(arg)
        index += 1

    if len(cleaned_args) >= 1:
        trigger = cleaned_args[0]

    if len(cleaned_args) >= 2:
        send_email = cleaned_args[1].lower() in ("1", "true", "yes", "y")

    generate_sales_report(
        send_email=send_email,
        persist_dashboard=True,
        trigger=trigger,
        report_start_month=report_start_month,
        report_end_month=report_end_month,
        order_start_date=order_start_date,
        order_end_date=order_end_date,
    )


if __name__ == "__main__":
    main()
