"""Match reserve orders to actual stored-value debits and returns."""

import json
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from bs4 import BeautifulSoup


def _parse_stored_value_rows(html: str) -> list:
    """儲值金歷程表格：預約日期／服務日期／訂單編號／項目／入帳金額／扣款金額／餘額／備註。

    回傳 [(訂單編號, 入帳金額, 扣款金額)]，入帳為正數、扣款為負數（沒有填就是 0）。
    """
    rows = []
    # 歷程頁是 Vue 頁面：表格列由前端畫出，資料在 script 的 storedValueList: [...] JSON 裡。
    text = str(html or "")
    marker = text.find("storedValueList:")
    if marker >= 0:
        start = text.find("[", marker)
        try:
            data, _ = json.JSONDecoder().raw_decode(text[start:])
        except Exception:
            data = None
        if isinstance(data, list):
            def num(value):
                digits = re.sub(r"[^\d\-]", "", str(value or ""))
                return int(digits) if digits not in ("", "-") else 0

            for entry in data:
                order_no = str(entry.get("orderNo") or "").strip()
                if re.fullmatch(r"[A-Z]{2}\d{5,}", order_no):
                    rows.append((order_no, num(entry.get("credit")), num(entry.get("debit"))))
            return rows
    try:
        soup = BeautifulSoup(text, "lxml")
    except Exception:
        soup = BeautifulSoup(text, "html.parser")

    def money(cell):
        digits = re.sub(r"[^\d\-]", "", cell.get_text(strip=True))
        return int(digits) if digits not in ("", "-") else 0

    for table in soup.find_all("table"):
        # 依表頭找欄位位置；找不到就用預設位置（訂單編號第3欄、入帳第5欄、扣款第6欄）
        idx_order, idx_income, idx_deduct = 2, 4, 5
        head = table.find("tr")
        if head is not None:
            labels = [c.get_text(strip=True) for c in head.find_all(["th", "td"])]
            for i, label in enumerate(labels):
                if "訂單編號" in label:
                    idx_order = i
                elif "入帳" in label or "返還" in label:
                    idx_income = i
                elif "扣款" in label:
                    idx_deduct = i
        for tr in table.find_all("tr"):
            cells = tr.find_all("td")
            if len(cells) <= max(idx_order, idx_income, idx_deduct):
                continue
            order_match = re.search(r"\b[A-Z]{2}\d{5,}\b", cells[idx_order].get_text(" ", strip=True))
            if not order_match:
                continue
            rows.append((order_match.group(0), money(cells[idx_income]), money(cells[idx_deduct])))
    return rows


def _member_id_for_order(session, purchase_url, order_no, headers):
    digits = re.sub(r"\D", "", order_no)
    if not digits:
        return ""
    response = session.get(f"{purchase_url}/edit/{int(digits)}", headers=headers, timeout=20)
    response.raise_for_status()
    for pattern in (r"""["']member_id["']\s*:\s*["']?(\d+)""", r"/member/(?:edit/)?(\d+)"):
        match = re.search(pattern, response.text)
        if match:
            return match.group(1)
    return ""


def resolve_deductions(session, items, purchase_url, headers):
    """Return amounts for matched orders only. A matched zero is a valid net debit."""
    member_orders = defaultdict(set)
    member_names = {}
    unidentified = defaultdict(list)
    for item in items:
        order = str(item.get("order_no") or item.get("orderNo") or item.get("purchase_id") or "").strip()
        if not order:
            continue
        name = str(item.get("name") or item.get("customer_name") or "").strip()
        member = str(item.get("member_id") or item.get("memberId") or item.get("__member_id") or "").strip()
        if member.isdigit():
            member_orders[member].add(order)
            if name:
                member_names.setdefault(name, set()).add(member)
        else:
            unidentified[name or order].append(order)

    for name, orders in unidentified.items():
        known = member_names.get(name, set())
        if len(known) != 1:
            try:
                member = _member_id_for_order(session, purchase_url, orders[0], headers)
            except Exception:
                continue
            known = {member} if member else set()
        for member in known:
            member_orders[member].update(orders)

    result = {}
    origin = purchase_url.rsplit("/", 1)[0]
    for member, orders in member_orders.items():
        url = f"{origin}/member/{member}/stored_value_histories"
        totals = defaultdict(int)
        signatures = set()

        def fetch(page):
            response = session.get(url, params={"page": str(page)}, headers=headers, timeout=20)
            response.raise_for_status()
            return _parse_stored_value_rows(response.text)

        try:
            with ThreadPoolExecutor(max_workers=3) as pool:
                finished = False
                for page in range(1, 151, 3):
                    for rows in pool.map(fetch, range(page, page + 3)):
                        signature = tuple(rows)
                        if not rows or signature in signatures:
                            finished = True
                            break
                        signatures.add(signature)
                        for order, credit, debit in rows:
                            if order in orders and (credit or debit):
                                totals[order] += abs(debit) - credit
                    if finished:
                        break
                if finished:
                    result.update(totals)
        except Exception:
            # An incomplete history is not a verified deduction.
            continue
    return result
