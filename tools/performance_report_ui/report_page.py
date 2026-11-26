# -*- coding: utf-8 -*-
"""業績報表畫面（tool-system 與 op-system 共用同一份）。

⚠️ 這個資料夾由 op-system 自動同步到 tool-system（op-system 的 .github/workflows/sync-to-tool-system.yml）。
   要改業績報表請改 op-system；直接改 tool-system 這份，下次同步時會被蓋掉。

2026-11-26：從 tool-system toolapp.py 拆出來；目前總表、訂購日期付款彙總、保留單業績改成雙重表頭（上方月份、下方副標題）。
2026-11-26：本月/次月/次次月/次次次月一律顯示成年月；月度追蹤新日期在上；訂購日期付款彙總固定三張表。
"""
from __future__ import annotations

import html
import json
import re
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

TW_TZ = ZoneInfo("Asia/Taipei")

# 表格與卡片樣式放在這裡，op-system、tool-system 顯示一致（tool-system 主 CSS 也有同樣規則，重複無妨）。
REPORT_CSS = """
<style>
  .card { background:white; border-radius:20px; padding:16px 20px; margin-bottom:14px;
          box-shadow:0 4px 12px rgba(0,32,48,0.06); border:1px solid #e2edf2; }
  .card-title { font-size:0.96rem; font-weight:800; color:#164a5e; margin-bottom:12px;
                padding-bottom:8px; border-bottom:1.5px solid #e7f0f5; }
  .report-table-wrap { overflow-x:auto; border:1px solid #e2e8f0; border-radius:16px; background:white; }
  table.report-table { width:100%; border-collapse:separate; border-spacing:0; font-size:0.95rem; }
  .report-table th { background:#f8fafc; color:#64748b; font-weight:800; text-align:center; padding:13px 14px;
                     border-bottom:1px solid #e2e8f0; border-right:1px solid #e2e8f0; white-space:nowrap; }
  .report-table td { padding:12px 14px; border-bottom:1px solid #edf2f7; border-right:1px solid #edf2f7;
                     color:#1e293b; background:white; white-space:nowrap; }
  .report-table td.num { text-align:right !important; font-variant-numeric:tabular-nums; }
  .report-table td.text { text-align:left; }
  .report-table tr.total-row td { background:#f8fafc; font-weight:900; }
  .report-table tr:hover td { background:#f1f5f9; }
</style>
"""


def _order_col_kind(col) -> str | None:
    text = str(col)
    if text.endswith("待付款＋已付款"):
        return "待付款＋已付款"
    if text.endswith("已付款"):
        return "已付款"
    if text.endswith("待付款"):
        return "待付款"
    return None


def split_order_tables(order_tables: dict) -> dict:
    """訂購日期付款彙總固定三張表：待付款、已付款、待付款＋已付款。

    2026-11-26：舊資料（例如 op-system 舊版的 order_date_summary.csv）三種欄位混在同一張，
    這裡把每張表裡不屬於它的欄位拆到對應的表，避免全部擠在「待付款」表。
    """
    result = {name: pd.DataFrame() for name in ("待付款", "已付款", "待付款＋已付款")}
    for name, table in order_tables.items():
        if table is None or table.empty:
            continue
        key_cols = [c for c in table.columns if _order_col_kind(c) is None]
        for kind in result:
            cols = [c for c in table.columns if _order_col_kind(c) == kind]
            if cols and (result[kind].empty or kind == name):
                result[kind] = table[key_cols + cols].copy()
    return result


def _newest_first(df: pd.DataFrame) -> pd.DataFrame:
    """月度追蹤表依日期由新到舊排（日期欄格式不一，用 to_datetime 解析）。"""
    if df is None or df.empty:
        return df
    col = "日期" if "日期" in df.columns else "快照日期" if "快照日期" in df.columns else None
    if col is None:
        return df
    order = pd.to_datetime(df[col].astype(str).str.replace("/", "-"), errors="coerce", format="mixed")
    return df.assign(__sort=order).sort_values("__sort", ascending=False, na_position="last").drop(columns="__sort").reset_index(drop=True)


def _report_month_labels(base=None) -> dict:
    """本月/次月/次次月/次次次月 → 2026/11、2026/12…（依報表產生日計算，預設今天）。"""
    if isinstance(base, str):
        try:
            base = datetime.strptime(base[:10].replace("/", "-"), "%Y-%m-%d")
        except ValueError:
            base = None
    base = base or datetime.now(TW_TZ)
    labels = {}
    for offset, name in enumerate(["本月", "次月", "次次月", "次次次月", "次次次次月"]):
        idx = base.month - 1 + offset
        labels[name] = f"{base.year + idx // 12}/{idx % 12 + 1:02d}"
    return labels


def _split_month_header(col, month_labels=None, sub_renames=None):
    """欄名拆成（月份, 副標題）：「2026/12待付款」→（2026/12, 待付款）、
    「次月加總」→（2026/12, 加總）；拆不了回傳 None。"""
    text = str(col)
    match = re.fullmatch(r"(\d{4}/\d{2})(.+)", text)
    parts = match.groups() if match else None
    if parts is None and month_labels:
        for name in sorted(month_labels, key=len, reverse=True):  # 長的先比，避免「次次月」被「次月」吃掉
            if text.startswith(name) and len(text) > len(name):
                parts = (month_labels[name], text[len(name):])
                break
    if parts is None:
        return None
    return parts[0], (sub_renames or {}).get(parts[1], parts[1])


def render_html_table(
    df: pd.DataFrame,
    max_height: int | None = None,
    split_payment_header: bool = False,
    month_labels: dict | None = None,
    sub_renames: dict | None = None,
) -> None:
    """split_payment_header=True 或給 month_labels 時用雙重表頭：上方月份、下方副標題，
    相鄰同月份的欄位合併成一格月份表頭；地區、儲值金等其他欄位上下兩列合併。"""
    if df.empty:
        st.info("尚無資料")
        return

    show = df.copy()
    numeric_cols = []

    for col in show.columns:
        col_text = str(col)
        if col_text == "金額來源":
            continue
        if "佔比" in col_text:
            show[col] = (
                pd.to_numeric(show[col], errors="coerce")
                .fillna(0)
                .map(lambda x: f"{x:.2%}")
            )
            numeric_cols.append(col)
        elif "時數" in col_text:
            show[col] = (
                pd.to_numeric(show[col], errors="coerce")
                .fillna(0)
                .map(lambda x: f"{int(x):,}" if float(x).is_integer() else f"{x:,.1f}")
            )
            numeric_cols.append(col)
        elif any(x in col_text for x in ["業績", "加總", "家電", "儲值金", "金額", "總額", "付款"]):
            show[col] = (
                pd.to_numeric(show[col], errors="coerce")
                .fillna(0)
                .map(lambda x: f"{int(x):,}")
            )
            numeric_cols.append(col)

    height_style = f'max-height:{int(max_height)}px;overflow:auto;' if max_height else ''
    out = [f'<div class="report-table-wrap" style="{height_style}"><table class="report-table"><thead><tr>']
    split_headers = [_split_month_header(col, month_labels, sub_renames) for col in show.columns]
    use_split_header = (split_payment_header or bool(month_labels) or bool(sub_renames)) and any(split_headers)
    sub_headers = []
    cols = list(show.columns)
    i = 0
    while i < len(cols):
        parts = split_headers[i]
        if not use_split_header or not parts:
            rowspan = ' rowspan="2"' if use_split_header else ""
            out.append(f"<th{rowspan}>{html.escape(str(cols[i]))}</th>")
            i += 1
            continue
        j = i
        while j < len(cols) and split_headers[j] and split_headers[j][0] == parts[0]:
            sub_headers.append(split_headers[j][1])
            j += 1
        colspan = f' colspan="{j - i}"' if j - i > 1 else ""
        out.append(f"<th{colspan}>{html.escape(parts[0])}</th>")
        i = j

    if use_split_header:
        out.append("</tr><tr>")
        for sub in sub_headers:
            out.append(f"<th>{html.escape(sub)}</th>")

    out.append("</tr></thead><tbody>")

    for _, row in show.iterrows():
        is_total = str(row.get("城市", row.get("地區", ""))) == "加總"
        cls = ' class="total-row"' if is_total else ""
        out.append(f"<tr{cls}>")

        for col in show.columns:
            cell_cls = "num" if col in numeric_cols else "text"
            out.append(f'<td class="{cell_cls}">{html.escape(str(row[col]))}</td>')

        out.append("</tr>")

    out.append("</tbody></table></div>")
    st.markdown("".join(out), unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════
# 業績報表
# ═══════════════════════════════════════════════════════════
def render_report(run_script, add_log=None, on_back=None, standalone_link: str | None = None) -> None:
    """業績報表頁。

    run_script(script_path, args)：執行 tools/scheduled_daily/performance_report.py 的函式（各系統自己提供）。
    add_log(message, level)：寫執行紀錄；沒有就不記。
    on_back()：有給才顯示「← 返回主控台」。standalone_link：有給才顯示「🔗 開啟獨立連結」。
    """
    if add_log is None:
        def add_log(message, level="info"):
            return None

    st.markdown(REPORT_CSS, unsafe_allow_html=True)
    latest_dir = Path("dashboard_data/latest")
    extended_path = latest_dir.parent / "tool_report_extended" / "report.json"
    df4_path = latest_dir / "df4.csv"
    daily_path = latest_dir / "daily_df.csv"
    next_path = latest_dir / "next_month_daily_df.csv"
    month_end_path = latest_dir / "month_end_summary.csv"
    meta_path = latest_dir / "meta.json"
    html_path = latest_dir / "email_preview.html"
    order_date_path = latest_dir / "order_date_summary.csv"
    order_date_paid_path = latest_dir / "order_date_paid_summary.csv"
    order_date_combined_path = latest_dir / "order_date_combined_summary.csv"
    reserve_path = latest_dir / "reserve_summary.csv"
    net_performance_path = latest_dir / "net_performance_summary.csv"
    month_performance_path = latest_dir / "month_performance_summary.csv"

    def load_csv(path: Path) -> pd.DataFrame:
        if not path.exists():
            return pd.DataFrame()
        return pd.read_csv(path, encoding="utf-8-sig")

    def money(value) -> str:
        try:
            return f"{int(float(str(value).replace(',', '').replace('%', ''))):,}"
        except Exception:
            return "0"

    def get_total(df: pd.DataFrame, col: str) -> str:
        if df.empty or col not in df.columns:
            return "0"
        total_row = df[df["城市"].astype(str) == "加總"] if "城市" in df.columns else pd.DataFrame()
        if not total_row.empty:
            return money(total_row.iloc[0][col])
        return money(pd.to_numeric(df[col], errors="coerce").fillna(0).sum())

    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}

    default_start_date = datetime.now(TW_TZ).date().replace(day=1)
    default_end_date = default_start_date
    saved_start_month = str(meta.get("report_start_month") or "").replace("/", "-")
    if saved_start_month:
        try:
            default_start_date = datetime.strptime(saved_start_month, "%Y-%m").date()
        except ValueError:
            pass
    saved_end_month = str(meta.get("report_end_month") or "").replace("/", "-")
    if saved_end_month:
        try:
            default_end_date = datetime.strptime(saved_end_month, "%Y-%m").date()
        except ValueError:
            pass
    else:
        default_end_date = default_start_date

    today_date = datetime.now(TW_TZ).date()
    default_order_start = today_date
    default_order_end = today_date

    # 畫面上的「本月/次月/次次月/次次次月」一律用年月顯示（依報表產生日）。
    month_labels = _report_month_labels(meta.get("updated_at"))
    month_names = [month_labels[name] for name in ("本月", "次月", "次次月", "次次次月")]

    st.markdown(
        """
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:18px;">
          <div>
            <div style="font-size:2.2rem;font-weight:900;color:#0f172a;letter-spacing:1px;">
              📊 業績報表
            </div>
            <div style="font-size:0.85rem;font-weight:800;color:#94a3b8;letter-spacing:5px;margin-top:4px;">
              LATEST DATA · CURRENT / NEXT 3 MONTHS / SNAPSHOT
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    def update_performance_report(scope: str = "all") -> None:
        """更新目前總表，或依頁籤條件重新產生全部業績報表。"""
        selected_order_start = st.session_state.get("performance_order_start_date", default_order_start)
        selected_order_end = st.session_state.get("performance_order_end_date", default_order_end)
        selected_month_start = st.session_state.get("performance_report_start_month", default_start_date)
        selected_month_end = st.session_state.get("performance_report_end_month", default_end_date)
        if scope in {"all", "order"} and selected_order_start > selected_order_end:
            st.error("訂購日期迄日不可早於起日")
            return
        if scope == "all" and selected_month_start.replace(day=1) > selected_month_end.replace(day=1):
            st.error("結束月份不可早於起始月份")
            return

        try:
            label = {
                "current": "目前總表",
                "order": "訂購日期付款彙總",
                "all": "全部報表",
                "reserve": "保留單明細",
            }[scope]
            add_log(f"開始更新{label}", "info")
            args = ["dashboard", "false" if scope == "reserve" else "true", "--scope", scope]
            if scope in {"all", "order"}:
                args.extend([
                    "--order-start-date", selected_order_start.strftime("%Y-%m-%d"),
                    "--order-end-date", selected_order_end.strftime("%Y-%m-%d"),
                ])
            if scope == "all":
                args.extend([
                    "--start-month", selected_month_start.strftime("%Y-%m"),
                    "--end-month", selected_month_end.strftime("%Y-%m"),
                ])
            started = time.perf_counter()
            run_script(
                "tools/scheduled_daily/performance_report.py",
                args,
            )
            add_log(f"{label}更新完成（{time.perf_counter() - started:.1f} 秒）", "success")
            st.rerun()
        except Exception as e:
            add_log(f"業績報表更新失敗：{e}", "error")
            st.error(f"業績報表更新失敗：{e}")

    top_cols = st.columns([1, 1, 1, 1, 1])
    with top_cols[0]:
        if on_back is not None and st.button("← 返回主控台", use_container_width=True):
            on_back()
            st.rerun()

    with top_cols[1]:
        if st.button("🔄 更新目前總表", use_container_width=True):
            with st.spinner(f"正在更新 {month_names[0]}～{month_names[3]} 業績…"):
                update_performance_report("current")

    with top_cols[2]:
        if st.button("🔄 更新全部報表", use_container_width=True):
            update_performance_report("all")

    with top_cols[3]:
        if st.button("📂 重新讀取資料", use_container_width=True):
            st.rerun()

    with top_cols[4]:
        if standalone_link:
            st.link_button("🔗 開啟獨立連結", standalone_link, use_container_width=True)

    if not df4_path.exists():
        st.info("尚未產生業績報表資料，請先在主控台執行「日排程系統 → 業績報表」。")
        return

    df4 = load_csv(df4_path)
    daily_df = load_csv(daily_path)
    next_df = load_csv(next_path)
    month_end_df = load_csv(month_end_path)
    order_date_df = load_csv(order_date_path)
    order_date_paid_df = load_csv(order_date_paid_path)
    order_date_combined_df = load_csv(order_date_combined_path)
    reserve_df = load_csv(reserve_path)
    net_performance_df = load_csv(net_performance_path)
    month_performance_df = load_csv(month_performance_path)

    if meta:
        st.info(
            f"📅 最新更新時間：{meta.get('updated_at', '-')}　｜　"
            f"訂購日期：{meta.get('order_start_date', '-')}～{meta.get('order_end_date', '-')}　｜　"
            f"月份：{meta.get('report_start_month', '-')}～{meta.get('report_end_month', '-')}"
        )
        if meta.get("error"):
            st.warning(meta.get("error"))

    if df4.empty:
        st.warning("業績報表資料為空，請重新執行「日排程系統 → 業績報表」。")
        return

    extended = {}
    if extended_path.exists():
        try:
            extended = json.loads(extended_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            st.warning("次次月、次次次月與保留單的已存資料暫時無法讀取。")

    periods = ("次次月", "次次次月")
    additional = pd.DataFrame(extended.get("summary", []))
    extra_columns = [f"{name}{field}" for name in periods for field in ("加總", "佔比")]
    summary = df4.copy()
    if not additional.empty:
        # 舊版 df4 可能已經有次次月欄位，避免合併後欄位重複。
        summary = summary.drop(columns=[c for c in extra_columns if c in summary.columns])
        summary = summary.merge(additional, on="城市", how="left", validate="one_to_one")
        position = summary.columns.get_loc("次月佔比") + 1
        old_columns = [col for col in summary if col not in extra_columns]
        summary = summary[old_columns[:position] + extra_columns + old_columns[position:]]

    order_tables = {
        "待付款": order_date_df,
        "已付款": order_date_paid_df,
        "待付款＋已付款": order_date_combined_df,
    }
    previous_order_at = str(extended.get("order_updated_at") or extended.get("updated_at") or "").replace("/", "-")
    saved_order_at = str(meta.get("order_date_updated_at") or meta.get("updated_at") or "").replace("/", "-")
    use_saved_four_month_orders = bool(
        extended.get("orders") and (order_date_df.empty or previous_order_at > saved_order_at)
    )
    if use_saved_four_month_orders:
        order_tables = {name: pd.DataFrame(rows) for name, rows in extended["orders"].items()}
    order_tables = split_order_tables(order_tables)

    report_tabs = st.tabs(["📍 目前總表", "🧾 訂購日期付款彙總", "📅 保留單明細", "📊 月份業績統整"])

    with report_tabs[0]:
        k1, k2, k3, k4, k5, k6 = st.columns(6)
        with k1:
            st.metric(f"{month_names[0]} 加總", get_total(summary, "本月加總"))
        with k2:
            st.metric(f"{month_names[1]} 加總", get_total(summary, "次月加總"))
        with k3:
            st.metric(f"{month_names[2]} 加總", get_total(summary, "次次月加總") if "次次月加總" in summary.columns else "—")
        with k4:
            st.metric(f"{month_names[3]} 加總", get_total(summary, "次次次月加總") if "次次次月加總" in summary.columns else "—")
        with k5:
            st.metric(f"{month_names[0]} 家電加總", get_total(df4, "本月家電加總"))
        with k6:
            st.metric("儲值金", get_total(df4, "儲值金"))

        st.markdown('<div class="card"><div class="card-title">📍 目前總表</div>', unsafe_allow_html=True)
        # 雙重表頭：上方月份（依報表產生日），下方加總／佔比／家電加總。
        render_html_table(summary, month_labels=month_labels)
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown('<div class="card"><div class="card-title">📈 目前月度追蹤</div>', unsafe_allow_html=True)
        tracking_tabs = st.tabs([f"{m} 每日業績" for m in month_names] + ["月底快照"])
        # 月度追蹤一律新日期在上。
        with tracking_tabs[0]:
            render_html_table(_newest_first(daily_df), max_height=360)
        with tracking_tabs[1]:
            render_html_table(_newest_first(next_df), max_height=360)
        for tab, name in zip(tracking_tabs[2:4], periods):
            with tab:
                history = pd.DataFrame(extended.get("history", {}).get(name, []))
                if history.empty:
                    st.info("按「更新目前總表」即可開始累積每日業績。")
                else:
                    render_html_table(_newest_first(history), max_height=360)
        with tracking_tabs[4]:
            render_html_table(_newest_first(month_end_df), max_height=360)
        st.markdown("</div>", unsafe_allow_html=True)

    with report_tabs[1]:
        st.markdown('<div class="card"><div class="card-title">🧾 訂購日期付款彙總</div>', unsafe_allow_html=True)
        order_cols = st.columns(2)
        with order_cols[0]:
            order_start_date = st.date_input(
                "訂購日期－起",
                value=default_order_start,
                key="performance_order_start_date",
            )
        with order_cols[1]:
            order_end_date = st.date_input(
                "訂購日期－迄",
                value=default_order_end,
                key="performance_order_end_date",
            )
        st.caption(f"預設起迄日皆為當日；分別顯示總額、服務月份（{month_names[0]}～{month_names[3]}）與儲值金購買金額。")
        if st.button(
            "✅ 確定並套用訂購日期區間",
            key="apply_performance_order_dates",
            use_container_width=True,
        ):
            with st.spinner("正在平行更新各區訂購日期付款彙總…"):
                update_performance_report("order")
        if use_saved_four_month_orders:
            st.caption(f"目前顯示的訂購日期 {extended['order_start']}～{extended['order_end']}")
        for name, table in order_tables.items():
            st.markdown(f"#### {name}")
            render_html_table(table, split_payment_header=True)
        st.markdown("</div>", unsafe_allow_html=True)

    with report_tabs[2]:
        st.caption(f"更新保留單時，查詢明天起到 {month_names[3]} 底已付款、姓名包含「保留」的訂單。")
        if st.button("🔄 更新保留單", use_container_width=True):
            with st.spinner("正在更新各區保留單…"):
                update_performance_report("reserve")
        if "reserve_details" in extended:
            st.caption(f"保留單更新時間 {extended.get('reserve_updated_at') or extended.get('updated_at')}")
            if any("金額來源" not in row for row in extended["reserve_details"]):
                st.warning("這批保留單是舊版金額，按「更新保留單」即可依實際儲值金扣款重新核對。")
            reserve_summary = pd.DataFrame(extended.get("reserve", []))
            reserve_details = pd.DataFrame(extended["reserve_details"])
            regions = [
                str(region) for region in reserve_summary.get("地區", [])
                if region != "加總"
            ]
            selected_region = st.selectbox("地區", ["全部", *regions], key="performance_reserve_region")
            if selected_region != "全部":
                reserve_summary = reserve_summary[reserve_summary["地區"] == selected_region]
                if not reserve_details.empty:
                    reserve_details = reserve_details[reserve_details["地區"] == selected_region]
            st.markdown("#### 保留單業績")
            # 雙重表頭：上方月份，下方保留時數／保留單金額。
            render_html_table(
                reserve_summary,
                sub_renames={"保留單時數": "保留時數", "保留單業績": "保留單金額"},
            )
            st.markdown("#### 保留單明細")
            render_html_table(reserve_details, max_height=360)
        else:
            st.info("按「更新保留單」即可讀取明細。")

    with report_tabs[3]:
        st.markdown('<div class="card"><div class="card-title">📅 月份業績統整</div>', unsafe_allow_html=True)
        month_cols = st.columns(2)
        with month_cols[0]:
            report_start_date = st.date_input(
                "起始月份（日期只取年月）",
                value=default_start_date,
                key="performance_report_start_month",
            )
        with month_cols[1]:
            report_end_date = st.date_input(
                "結束月份（日期只取年月）",
                value=default_end_date,
                key="performance_report_end_month",
            )

        date_range_valid = order_start_date <= order_end_date
        month_range_valid = report_start_date.replace(day=1) <= report_end_date.replace(day=1)
        if not date_range_valid:
            st.error("訂購日期迄日不可早於起日")
        if not month_range_valid:
            st.error("結束月份不可早於起始月份")

        st.caption("選好起迄月份後，按下方確定鍵重新統整該區間資料（日期只取年月）。")
        if st.button(
            "✅ 確定並套用月份區間",
            key="apply_performance_month_range",
            use_container_width=True,
        ):
            update_performance_report("all")
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown('<div class="card"><div class="card-title">➖ 該月業績－該月保留單業績</div>', unsafe_allow_html=True)
        render_html_table(net_performance_df)
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown('<div class="card"><div class="card-title">📊 該月業績報表</div>', unsafe_allow_html=True)
        if month_performance_df.empty:
            st.warning("此資料尚未產生，請選好月份後按『確定並套用月份區間』。")
        render_html_table(month_performance_df)
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown('<div class="card"><div class="card-title">🕒 該月保留單業績</div>', unsafe_allow_html=True)
        st.caption("保留單時數以『人數 × 每人服務時數』計算。")
        render_html_table(reserve_df)
        st.markdown("</div>", unsafe_allow_html=True)

    with st.expander("🔎 Debug：查看 df2 / raw_df", expanded=False):
        debug_dir = Path("dashboard_data/latest")
        df2_path = debug_dir / "df2.csv"
        raw_path = debug_dir / "raw_df.csv"
        selected_city = "全部"

        if df2_path.exists():
            df2 = pd.read_csv(df2_path, encoding="utf-8-sig")
            city_options = ["全部"] + sorted(df2["城市"].dropna().astype(str).unique().tolist())
            selected_city = st.selectbox("城市", city_options, key="sales_debug_city")
            df2_view = df2 if selected_city == "全部" else df2[df2["城市"].astype(str) == selected_city]
            st.subheader("df2：分類後彙總")
            st.dataframe(df2_view, use_container_width=True, height=520)
        else:
            st.warning("找不到 df2.csv")

        if raw_path.exists():
            raw_df = pd.read_csv(raw_path, encoding="utf-8-sig")
            raw_view = raw_df
            if df2_path.exists() and selected_city != "全部":
                raw_view = raw_df[raw_df["城市"].astype(str) == selected_city]
            st.subheader("raw_df：原始抓取資料")
            st.dataframe(
                raw_view,
                use_container_width=True,
                height=520,
            )
        else:
            st.warning("找不到 raw_df.csv")
    

    with st.expander("📧 信件預覽", expanded=False):
        if html_path.exists():
            st.components.v1.html(html_path.read_text(encoding="utf-8"), height=600, scrolling=True)
        else:
            st.info("尚無 Email HTML")
