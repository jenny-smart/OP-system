# -*- coding: utf-8 -*-
"""取消保留單。流程與安全規則移植自 orders-system 的 reserve_cancel.py / cancel_order.py。

取消 = 後台 POST /purchase/cancel/{id}（status=不需退款、newMemo=取消系統保留單），
再回到修改頁把客人備註寫成「取消系統保留單」。取消後無法還原。

安全規則（與 orders-system 相同）：
- 取消前重新讀取客人備註，只有「空白」或「系統保留單」才取消。
- 已經是「取消系統保留單」→ 跳過（避免重複取消）。
- 備註已有其他內容（可能是人工替客人保留）→ 跳過、不取消。
"""

import json
import re
from typing import List

import requests
from bs4 import BeautifulSoup

import performance_report as pr

SYSTEM_RESERVE_MEMO = "系統保留單"
CANCELLED_RESERVE_MEMO = "取消系統保留單"
CANCEL_STATUS_NO_REFUND = "1"  # orders-system CANCEL_STATUS_MAP["不需退款"]

JSON_BACKED_EDIT_FIELDS = [
    "isCharge", "chargeDate", "chargePayment", "chargeInvoiceDate", "chargeAmount",
    "chargeInvoice", "chargeNote", "isRefund", "refundDate", "refundPayment",
    "refundAmount", "refundNumber", "refundInvoiceDate", "refundInvoiceAmount",
    "refundInvoice", "refundNote", "progress",
]

BASE_URL = "https://backend.lemonclean.com.tw"


def purchase_id_from_order_no(order_no: str) -> str:
    digits = re.sub(r"\D", "", str(order_no or ""))
    return str(int(digits)) if digits else ""


def _csrf_from_html(page_html: str) -> str:
    m = re.search(r'<meta\s+name="csrf-token"\s+content="([^"]+)"', page_html or "", re.I)
    return m.group(1) if m else ""


def _parse_purchase_json(page_html: str) -> dict:
    m = re.search(r"purchase:\s*(\{.*?\})\s*\n?\s*\}\s*\n?\s*\}", page_html or "", re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except Exception:
        return {}


def _edit_payload_from_page(page_html: str):
    soup = BeautifulSoup(page_html, "html.parser")
    payload = {}
    form = soup.find("form", attrs={"method": re.compile("post", re.I)}) or soup.find("form")
    if form:
        for node in form.find_all(["input", "textarea", "select"]):
            name = node.get("name")
            if not name:
                continue
            if node.name == "textarea":
                payload[name] = node.get_text()
                continue
            if node.name == "select":
                selected = node.find("option", selected=True)
                if selected is not None:
                    payload[name] = selected.get("value", "")
                continue
            input_type = str(node.get("type") or "text").lower()
            if input_type in {"radio", "checkbox"}:
                if node.has_attr("checked"):
                    payload[name] = node.get("value", "1")
                continue
            if input_type in {"submit", "button", "file"}:
                continue
            payload[name] = node.get("value", "")
    purchase = _parse_purchase_json(page_html)
    for key in JSON_BACKED_EDIT_FIELDS:
        if key in purchase:
            value = purchase.get(key)
            payload[key] = "" if value is None else str(value)
    csrf = _csrf_from_html(page_html)
    if csrf:
        payload["_token"] = csrf
    payload.pop("_method", None)
    return payload, purchase


def classify_memo(memo: str) -> str:
    """回傳 'ok'（可取消）/ 'cancelled'（已取消過）/ 'other'（備註有其他內容，不取消）。"""
    memo = str(memo or "").strip()
    if CANCELLED_RESERVE_MEMO in memo:
        return "cancelled"
    if memo == "" or SYSTEM_RESERVE_MEMO in memo:
        return "ok"
    return "other"


def _read_memo(session, purchase_id: str) -> str:
    resp = session.get(f"{BASE_URL}/purchase/edit/{purchase_id}", headers=pr.HEADERS, allow_redirects=True)
    if resp.status_code != 200:
        raise RuntimeError(f"無法開啟訂單修改頁：HTTP {resp.status_code}")
    _, purchase = _edit_payload_from_page(resp.text)
    return str(purchase.get("memo") or "")


def _write_cancel_memo(session, purchase_id: str):
    edit_url = f"{BASE_URL}/purchase/edit/{purchase_id}"
    resp = session.get(edit_url, headers=pr.HEADERS, allow_redirects=True)
    if resp.status_code != 200:
        return False, f"取消成功，但讀取備註頁失敗：HTTP {resp.status_code}"
    payload, _ = _edit_payload_from_page(resp.text)
    if not payload.get("_token"):
        return False, "取消成功，但無法取得修改頁 CSRF token，備註尚未寫入"
    payload["memo"] = CANCELLED_RESERVE_MEMO
    payload["chargeNote"] = ""
    payload["refundNote"] = ""
    post = session.post(
        edit_url, data=payload,
        headers={**pr.HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
        allow_redirects=True,
    )
    if post.status_code not in (200, 302):
        return False, f"取消成功，但備註寫入失敗：HTTP {post.status_code}"
    return True, ""


def cancel_reserve_orders(orders: List[dict]) -> List[dict]:
    """orders：每筆含 城市（地區）、訂單編號。依城市分組各登入一次，逐筆取消；回傳每筆結果。"""
    results = []
    by_city = {}
    for row in orders:
        by_city.setdefault(str(row.get("地區") or ""), []).append(row)

    for city, rows in by_city.items():
        account = pr.ACCOUNTS.get(city)
        if not account:
            results += [{**r, "ok": False, "message": f"找不到「{city}」的後台帳號設定"} for r in rows]
            continue
        try:
            session = requests.Session()
            pr.login(session, account["email"], account["password"])
            csrf_page = session.get(f"{BASE_URL}/purchase", headers=pr.HEADERS, allow_redirects=True)
            csrf = _csrf_from_html(csrf_page.text)
            if not csrf:
                raise RuntimeError("無法取得取消訂單所需 CSRF token")
        except Exception as exc:
            results += [{**r, "ok": False, "message": f"登入失敗：{exc}"} for r in rows]
            continue

        for row in rows:
            order_no = str(row.get("訂單編號") or "")
            purchase_id = purchase_id_from_order_no(order_no)
            if not purchase_id:
                results.append({**row, "ok": False, "message": "無法取得 purchase_id"})
                continue
            try:
                kind = classify_memo(_read_memo(session, purchase_id))
                if kind == "cancelled":
                    results.append({**row, "ok": False, "message": "此保留單已取消過，已跳過。"})
                    continue
                if kind == "other":
                    results.append({**row, "ok": False, "message": "客人備註已有其他內容，判定可能是人工替客人保留，已跳過不取消。"})
                    continue
                resp = session.post(
                    f"{BASE_URL}/purchase/cancel/{purchase_id}",
                    json={"status": CANCEL_STATUS_NO_REFUND, "newMemo": CANCELLED_RESERVE_MEMO, "_token": csrf},
                    headers={**pr.HEADERS, "X-CSRF-TOKEN": csrf, "Referer": f"{BASE_URL}/purchase"},
                    allow_redirects=True,
                )
                if resp.status_code not in (200, 201, 204, 302):
                    raise RuntimeError(f"取消失敗：HTTP {resp.status_code}")
                note_ok, note_msg = _write_cancel_memo(session, purchase_id)
                results.append({**row, "ok": True, "message": "取消成功" if note_ok else note_msg})
            except Exception as exc:
                results.append({**row, "ok": False, "message": str(exc)})
    return results
