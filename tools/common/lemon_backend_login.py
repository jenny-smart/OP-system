"""檸檬後台（backend.lemonclean.com.tw）登入的唯一共用流程。

只依賴 requests、bs4，orders-system、order-system 可以原樣複製這個檔（有測試檢查內容一致）。
流程：取登入頁 _token → 送出帳密 → 確認沒有被退回登入頁；
登入成功後登記「程式結束時登出」，每次執行完都會登出後台。
"""
from __future__ import annotations

import atexit
import re
from typing import Any

import requests
from bs4 import BeautifulSoup

DEFAULT_LOGIN_URL = "https://backend.lemonclean.com.tw/login"
_ACTIVE: list[tuple[requests.Session, str, dict]] = []


def logout_url_for(login_url: str) -> str:
    return re.sub(r"/login/?$", "/logout", login_url.rstrip("/"))


def login(
    session: requests.Session,
    email: str,
    password: str,
    *,
    login_url: str = DEFAULT_LOGIN_URL,
    headers: dict[str, Any] | None = None,
    timeout: int = 60,
    check_url: str = "",
) -> None:
    """登入失敗一律丟出 RuntimeError（不回傳 False，避免呼叫端忘記檢查）。"""
    if not email or not password:
        raise RuntimeError("檸檬後台帳號或密碼未設定")
    headers = dict(headers or {})
    res = session.get(login_url, headers=headers, allow_redirects=True, timeout=timeout)
    res.raise_for_status()
    token_input = BeautifulSoup(res.text, "html.parser").find("input", {"name": "_token"})
    token = (token_input.get("value") or "").strip() if token_input else ""
    if not token:
        raise RuntimeError("檸檬後台登入頁找不到 _token")
    res = session.post(
        login_url,
        data={"_token": token, "email": email, "password": password},
        headers=headers, allow_redirects=True, timeout=timeout,
    )
    res.raise_for_status()
    if check_url:
        res = session.get(check_url, headers=headers, allow_redirects=True, timeout=timeout)
        res.raise_for_status()
    if "/login" in res.url.lower():
        raise RuntimeError(f"檸檬後台登入失敗（{email}），請確認帳密")
    _ACTIVE.append((session, logout_url_for(login_url), headers))


def logout(session: requests.Session, login_url: str = DEFAULT_LOGIN_URL, headers: dict[str, Any] | None = None) -> None:
    """登出後台；失敗不拋例外（只是結束這次連線的後台工作階段）。"""
    try:
        session.get(logout_url_for(login_url), headers=dict(headers or {}), allow_redirects=True, timeout=15)
    except Exception as exc:
        print(f"⚠️ 檸檬後台登出失敗：{exc}")
    finally:
        _ACTIVE[:] = [item for item in _ACTIVE if item[0] is not session]


@atexit.register
def _logout_all() -> None:
    """程式結束時把這次登入過的後台工作階段都登出。"""
    for session, url, headers in list(_ACTIVE):
        try:
            session.get(url, headers=headers, allow_redirects=True, timeout=10)
        except Exception:
            pass
    _ACTIVE.clear()
