# -*- coding: utf-8 -*-
"""雙重表頭（上方月份、下方副標題）的 HTML 表格，跟 tool-system 的訂購日期付款彙總同一種表頭。

- month_labels()：把「本月/次月/…」或「當月/次月/…」對應成 2026/11、2026/12…
- split_month_header()：把欄名拆成（月份, 副標題），例如
  「本月加總」→（2026/11, 加總）、「2026/12待付款」→（2026/12, 待付款）；拆不了回傳 None。
- render_html_table()：產生表格 HTML；相鄰同月份的欄位會合併成一格月份表頭，
  拆不了的欄位（地區、儲值金…）上下兩列合併成一格。
"""

import html
import re
from datetime import date, datetime

import pandas as pd

RELATIVE_MONTH_NAMES = ["本月", "次月", "次次月", "次次次月", "次次次次月"]
_YM_RE = re.compile(r"(\d{4}/\d{2})(.+)")


def month_labels(base=None, names=RELATIVE_MONTH_NAMES) -> dict:
    """依 base（報表產生的日期，預設今天）算出每個相對月份的 YYYY/MM。"""
    if isinstance(base, str):
        try:
            base = datetime.strptime(base[:10].replace("/", "-"), "%Y-%m-%d")
        except ValueError:
            base = None
    base = base or date.today()
    labels = {}
    for offset, name in enumerate(names):
        idx = base.month - 1 + offset
        labels[name] = f"{base.year + idx // 12}/{idx % 12 + 1:02d}"
    return labels


def split_month_header(col, labels=None, renames=None):
    text = str(col)
    match = _YM_RE.fullmatch(text)
    parts = match.groups() if match else None
    if parts is None and labels:
        # 長的先比對，避免「次次月加總」被「次月」吃掉。
        for name in sorted(labels, key=len, reverse=True):
            if text.startswith(name) and len(text) > len(name):
                parts = (labels[name], text[len(name):])
                break
    if parts is None:
        return None
    month, sub = parts
    return month, (renames or {}).get(sub, sub)


def _fmt(value, kind):
    if pd.isna(value) or str(value).strip() in ("", "nan"):
        return "—"
    try:
        num = float(str(value).replace(",", ""))
    except ValueError:
        return html.escape(str(value))
    if kind == "pct":
        return f"{num:.2%}"
    if kind == "hours":
        return f"{int(num):,}" if num.is_integer() else f"{num:,.1f}"
    return f"{int(round(num)):,}"


def _kind(col):
    text = str(col)
    if "佔比" in text:
        return "pct"
    if "時數" in text or "人時" in text:
        return "hours"
    if any(k in text for k in ("業績", "加總", "合計", "金額", "付款", "儲值金", "單數")):
        return "int"
    return None


TH = (
    "padding:10px 14px;font-size:12px;font-weight:700;color:#64748b;"
    "border:1px solid #e8ecf0;white-space:nowrap;background:#fafafa;text-align:center;"
)
TD = "padding:9px 14px;font-size:13px;color:#1e293b;border:1px solid #f1f5f9;white-space:nowrap;"


def render_html_table(df: pd.DataFrame, labels=None, renames=None, max_height=None,
                      pct_cols=None, int_cols=None) -> str:
    """回傳表格 HTML（用 st.markdown(..., unsafe_allow_html=True) 顯示）。"""
    cols = list(df.columns)
    kinds = {}
    for c in cols:
        if pct_cols is not None or int_cols is not None:
            kinds[c] = "pct" if c in (pct_cols or ()) else "int" if c in (int_cols or ()) else None
        else:
            kinds[c] = _kind(c)
    splits = [split_month_header(c, labels, renames) for c in cols]
    double = any(splits)

    top, bottom = [], []
    i = 0
    while i < len(cols):
        parts = splits[i]
        if not double or parts is None:
            span = ' rowspan="2"' if double else ""
            top.append(f'<th style="{TH}"{span}>{html.escape(str(cols[i]))}</th>')
            i += 1
            continue
        j = i
        while j < len(cols) and splits[j] is not None and splits[j][0] == parts[0]:
            bottom.append(f'<th style="{TH}">{html.escape(splits[j][1])}</th>')
            j += 1
        span = f' colspan="{j - i}"' if j - i > 1 else ""
        top.append(f'<th style="{TH}"{span}>{html.escape(parts[0])}</th>')
        i = j

    head = f"<tr>{''.join(top)}</tr>" + (f"<tr>{''.join(bottom)}</tr>" if double else "")
    body = []
    for _, row in df.iterrows():
        first = str(row[cols[0]]) if cols else ""
        weight = "font-weight:700;background:#f8fafc;" if first in ("加總", "合計") else ""
        cells = "".join(
            f'<td style="{TD}{weight}text-align:{"right" if kinds[c] else "left"}">'
            f'{_fmt(row[c], kinds[c]) if kinds[c] else html.escape(str(row[c]))}</td>'
            for c in cols
        )
        body.append(f"<tr>{cells}</tr>")
    height = f"max-height:{int(max_height)}px;overflow:auto;" if max_height else "overflow-x:auto;"
    return (
        f'<div style="{height}border:1px solid #e8ecf0;border-radius:10px;margin-bottom:12px;">'
        '<table style="width:100%;border-collapse:collapse;background:#fff;">'
        f"<thead>{head}</thead><tbody>{''.join(body)}</tbody></table></div>"
    )
