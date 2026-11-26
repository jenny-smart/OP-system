"""Append-only Google Sheets store for the five OP daily history views."""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

import pandas as pd


FILES = (
    "daily_df.csv",
    "next_month_daily_df.csv",
    "month3_daily_df.csv",
    "month4_daily_df.csv",
    "month5_daily_df.csv",
)
REGIONS = ("台北", "台中", "桃園", "新竹", "高雄")
FIELDS = ["id", "來源", "統計月份", "日期"] + [
    region + suffix for region in REGIONS for suffix in ("業績", "佔比")
] + ["全區合計"]
HEADERS = ["_period", "_action", *FIELDS]
NUMERIC_FIELDS = {field for field in FIELDS if field.endswith(("業績", "佔比", "合計"))}
SHEET_NAME = "History"
OP_HISTORY_SHEET_ID = "1qDnyGYcxdqc5KJYWHvsjAJui90DuV2iShiASSXLkaUQ"
BACKEND = "google_sheets"


def _setting(name: str):
    value = os.environ.get(name)
    if value:
        return value
    try:
        import streamlit as st

        return st.secrets.get(name, "")
    except Exception:
        return ""


def enabled() -> bool:
    backend = _setting("OP_HISTORY_BACKEND")
    if backend not in ("", BACKEND):
        raise RuntimeError("OP_HISTORY_BACKEND 不支援的設定值")
    return backend == BACKEND


def _service():
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    raw = _setting("OP_HISTORY_GOOGLE_CREDENTIALS_JSON") or _setting("GOOGLE_SERVICE_ACCOUNT")
    if not raw:
        raise RuntimeError("OP 私有業績歷史未設定 Google 憑證")
    try:
        info = dict(raw) if isinstance(raw, Mapping) else json.loads(raw)
        if not isinstance(info, dict) or info.get("type") != "service_account":
            raise ValueError
        credentials = Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
    except Exception:
        raise RuntimeError("OP 私有業績歷史 Google 憑證格式錯誤") from None
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def _spreadsheet_id() -> str:
    value = _setting("OP_HISTORY_SPREADSHEET_ID") or OP_HISTORY_SHEET_ID
    if value != OP_HISTORY_SHEET_ID:
        raise RuntimeError("OP 私有業績歷史試算表 ID 與指定目標不符")
    return OP_HISTORY_SHEET_ID


def _read_values() -> list[list]:
    result = (
        _service()
        .spreadsheets()
        .values()
        .get(
            spreadsheetId=_spreadsheet_id(),
            range=f"{SHEET_NAME}!A:Q",
            valueRenderOption="UNFORMATTED_VALUE",
        )
        .execute()
    )
    return result.get("values", [])


def _append_values(rows: list[list]) -> None:
    if not rows:
        return
    (
        _service()
        .spreadsheets()
        .values()
        .append(
            spreadsheetId=_spreadsheet_id(),
            range=f"{SHEET_NAME}!A:Q",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": rows},
        )
        .execute()
    )


def _number(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)) or str(value).strip() == "":
        return ""
    try:
        return format(Decimal(str(value)).normalize(), "f")
    except InvalidOperation:
        raise ValueError("業績歷史數值欄位格式錯誤") from None


def _row_payload(row) -> tuple[dict, tuple[str, ...]]:
    payload = {}
    canonical = []
    for field in FIELDS:
        value = row.get(field, "")
        if value is None or (isinstance(value, float) and pd.isna(value)):
            value = ""
        normalized = _number(value) if field in NUMERIC_FIELDS else str(value)
        payload[field] = value
        canonical.append(normalized)
    if not str(payload["id"]).strip() or not str(payload["日期"]).strip():
        raise ValueError("業績歷史缺少 ID 或日期")
    return payload, tuple(canonical)


def _state():
    values = _read_values()
    if not values or values[0][: len(HEADERS)] != HEADERS:
        raise RuntimeError("OP 私有業績歷史表頭不符")
    active = {}
    seen = {}
    order = []
    for raw in values[1:]:
        row = list(raw) + [""] * (len(HEADERS) - len(raw))
        try:
            period = int(row[0])
        except (TypeError, ValueError):
            raise RuntimeError("OP 私有業績歷史期別格式錯誤") from None
        if period not in range(5):
            raise RuntimeError("OP 私有業績歷史期別超出範圍")
        action = str(row[1]).strip()
        record_id = str(row[2]).strip()
        key = (period, record_id)
        if action != "append":
            raise RuntimeError("OP 私有業績歷史動作格式錯誤")
        payload, canonical = _row_payload(dict(zip(FIELDS, row[2:])))
        if key in seen and seen[key] != canonical:
            raise RuntimeError("業績歷史 ID 衝突，已停止讀寫")
        if key not in seen:
            seen[key] = canonical
            active[key] = payload
            order.append(key)
    return active, seen, order


def _operation(fn):
    try:
        return fn()
    except (RuntimeError, ValueError):
        raise
    except Exception:
        raise RuntimeError("OP 私有業績歷史讀寫失敗，未回退到公開 CSV") from None


def read(filename: str) -> pd.DataFrame:
    period = FILES.index(filename)

    def work():
        active, _seen, order = _state()
        rows = [active[key] for key in order if key[0] == period and key in active]
        frame = pd.DataFrame(rows, columns=FIELDS)
        if not frame.empty:
            frame = frame.sort_values("日期", ascending=False, kind="stable").reset_index(drop=True)
        return frame

    return _operation(work)


def append(frames: dict[str, pd.DataFrame]) -> dict:
    def work():
        _active, seen, _order = _state()
        pending = {}
        output = []
        for filename, frame in frames.items():
            period = FILES.index(filename)
            for raw in frame.to_dict("records"):
                payload, canonical = _row_payload(raw)
                key = (period, str(payload["id"]))
                previous = pending.get(key, seen.get(key))
                if key in pending or key in seen:
                    if previous != canonical:
                        raise RuntimeError("業績歷史 ID 衝突，整批未寫入")
                    continue
                pending[key] = canonical
                output.append([period, "append", *[payload[field] for field in FIELDS]])
        _append_values(output)
        return {"added": len(output), "already_present": sum(len(f) for f in frames.values()) - len(output)}

    return _operation(work)


def verify_history():
    active, _seen, _order = _operation(_state)
    if {period for period, _ in active} != set(range(len(FILES))):
        raise RuntimeError("OP 私有業績歷史尚未包含完整五期")


