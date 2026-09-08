import os
import json
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from datetime import datetime, timedelta, timezone

# 重要：一定要在 import dashboard_main 之前 patch performance_report。
# 目的只剩一個：確保 dashboard_main 按「更新資料」時一定會落檔。
# 不可以在這裡再呼叫 persist_dashboard_payload()，否則同一次更新會寫入兩次。
import performance_report as _performance_report
import order_date_report_ui as _order_date_report_ui

LATEST_DIR = _performance_report.LATEST_DIR
DAILY_HISTORY_DIR = _performance_report.DAILY_HISTORY_DIR

# Streamlit 會在同一個 Python process 內重跑本檔案；原函式必須只保存一次。
# 若每次 rerun 都從已包裝的 generate_sales_report 再取一次，就會形成無限遞迴。
_ORIGINAL_GENERATE_ATTR = "_opapp_original_generate_sales_report"
if not hasattr(_performance_report, _ORIGINAL_GENERATE_ATTR):
    setattr(
        _performance_report,
        _ORIGINAL_GENERATE_ATTR,
        _performance_report.generate_sales_report,
    )

_ORIGINAL_GENERATE_SALES_REPORT = getattr(
    _performance_report,
    _ORIGINAL_GENERATE_ATTR,
)

def _generate_sales_report_force_persist(*args, **kwargs):
    kwargs["persist_dashboard"] = True
    return _ORIGINAL_GENERATE_SALES_REPORT(*args, **kwargs)

_performance_report.generate_sales_report = _generate_sales_report_force_persist

from dashboard_main import render_page

st.set_page_config(
    page_title="Jenny 業績報表",
    page_icon="🍋",
    layout="wide",
)

OPAPP_VERSION = "2026-08-19-three-report-tabs-v1"

TZ_TAIPEI = timezone(timedelta(hours=8))

# ── Global CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=DM+Mono:wght@400;500&display=swap');

/* ─── Base ─── */
html, body,
[data-testid="stAppViewContainer"],
[data-testid="stApp"] {
    background: #f0f2f6 !important;
    font-family: 'DM Sans','PingFang TC','Noto Sans TC',sans-serif !important;
    color: #1e293b !important;
}
[data-testid="stHeader"],
[data-testid="stSidebar"] { display: none !important; }
.block-container { padding: 0 2.4rem 4rem !important; max-width: 1480px !important; }

/* ─── Topbar ─── */
.topbar {
    background: #fff;
    margin: 0 -2.4rem;
    padding: 0 28px;
    border-bottom: 1px solid #e8ecf0;
    position: sticky; top: 0; z-index: 999;
    box-shadow: 0 1px 8px rgba(15,23,42,.06);
}
.topbar-inner { display: flex; align-items: center; height: 64px; }
.topbar-brand { display: flex; align-items: center; gap: 9px; flex-shrink: 0; }
.topbar-logo  { font-size: 26px; line-height: 1; }
.topbar-name  { font-size: 20px; font-weight: 700; color: #0f172a; letter-spacing: -.01em; white-space: nowrap; }
.topbar-sep   { width: 1px; height: 20px; background: #dde2e8; margin: 0 18px; flex-shrink: 0; }
.topbar-clock { font-size: 12px; color: #64748b; font-weight: 500; margin-left: auto; font-variant-numeric: tabular-nums; }

/* ─── Nav strip ─── */
.nav-strip {
    background: #fff;
    margin: 0 -2.4rem;
    padding: 0 12px;
    border-bottom: 1px solid #e8ecf0;
}

/* ─── Nav buttons — ALWAYS override global button style ─── */
html body .nav-wrap div[data-testid="stButton"] > button,
html body .nav-wrap div[data-testid="stButton"] > button:focus,
html body .nav-wrap div[data-testid="stButton"] > button:active {
    height: 46px !important;
    padding: 0 16px !important;
    border-radius: 0 !important;
    border: none !important;
    border-bottom: 2px solid transparent !important;
    background: transparent !important;
    color: #64748b !important;
    font-weight: 600 !important;
    font-size: 14px !important;
    box-shadow: none !important;
    white-space: nowrap !important;
    letter-spacing: 0 !important;
}
html body .nav-wrap div[data-testid="stButton"] > button:hover {
    color: #1e293b !important;
    background: #f4f6f9 !important;
    border-bottom: 2px solid transparent !important;
}
html body .nav-wrap.active div[data-testid="stButton"] > button {
    color: #2563eb !important;
    background: #eff6ff !important;
    border-bottom: 2px solid #2563eb !important;
}

/* ─── Page header ─── */
.page-header {
    padding: 22px 0 16px;
    border-bottom: 1px solid #e8ecf0;
    margin-bottom: 22px;
    display: flex; align-items: flex-end; gap: 12px;
}
.page-title    { font-size: 34px; font-weight: 700; color: #0f172a; line-height: 1; letter-spacing: -.02em; }
.page-subtitle { font-size: 14px; font-weight: 700; letter-spacing: .14em; text-transform: uppercase; color: #94a3b8; padding-bottom: 1px; }

/* ─── KPI cards ─── */
.kpi-row { display: flex; gap: 12px; margin-bottom: 22px; }
.kpi-card {
    flex: 1; background: #fff; border: 1px solid #e8ecf0; border-radius: 12px;
    padding: 16px 20px 14px; position: relative; overflow: hidden;
    box-shadow: 0 1px 3px rgba(15,23,42,.04), 0 3px 10px rgba(15,23,42,.04);
}
.kpi-card::before {
    content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px; border-radius: 12px 12px 0 0;
}
.kpi-card.blue::before  { background: linear-gradient(90deg,#2563eb,#60a5fa); }
.kpi-card.green::before { background: linear-gradient(90deg,#059669,#34d399); }
.kpi-card.amber::before { background: linear-gradient(90deg,#b45309,#fbbf24); }
.kpi-card.red::before   { background: linear-gradient(90deg,#dc2626,#f87171); }
.kpi-label { font-size: 9.5px; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; color: #64748b; margin-bottom: 7px; }
.kpi-value { font-size: 34px; font-weight: 700; color: #0f172a; line-height: 1; letter-spacing: -.03em; font-variant-numeric: tabular-nums; }
.kpi-sub   { font-size: 11.5px; color: #64748b; font-weight: 500; margin-top: 5px; }

/* ─── Section card ─── */
.section-card {
    background: #fff; border: 1px solid #e8ecf0; border-radius: 12px;
    padding: 20px 22px 18px; margin-bottom: 16px;
    box-shadow: 0 1px 3px rgba(15,23,42,.04), 0 3px 10px rgba(15,23,42,.04);
}
.section-title {
    font-size: 11px; font-weight: 700; letter-spacing: .1em; text-transform: uppercase;
    color: #2563eb; margin-bottom: 16px; padding-bottom: 12px;
    border-bottom: 1px solid #f1f5f9;
    display: flex; align-items: center; gap: 7px;
}

/* ─── Status badges ─── */
.badge {
    display: inline-flex; align-items: center; gap: 5px;
    font-size: 11px; font-weight: 600; padding: 3px 9px; border-radius: 20px; white-space: nowrap;
}
.badge::before { content:''; width:5px; height:5px; border-radius:50%; flex-shrink:0; }
.b-green  { color:#065f46; background:#d1fae5; } .b-green::before  { background:#059669; }
.b-yellow { color:#78350f; background:#fef3c7; } .b-yellow::before { background:#d97706; }
.b-red    { color:#991b1b; background:#fee2e2; } .b-red::before    { background:#dc2626; }
.b-gray   { color:#475569; background:#f1f5f9; } .b-gray::before   { background:#94a3b8; }
.b-blue   { color:#1d4ed8; background:#dbeafe; } .b-blue::before   { background:#2563eb; }

/* ─── Run button ─── */
html body .run-btn div[data-testid="stButton"] > button {
    background: #1e293b !important; color: #f1f5f9 !important;
    border: none !important; border-radius: 6px !important;
    font-weight: 600 !important; font-size: 12px !important;
    padding: 3px 11px !important; height: 28px !important; min-height: 28px !important;
    box-shadow: none !important;
}
html body .run-btn div[data-testid="stButton"] > button:hover { background: #0f172a !important; }

/* ─── Save button ─── */
html body .save-btn div[data-testid="stButton"] > button {
    background: #f0f9ff !important; color: #0369a1 !important;
    border: 1px solid #bae6fd !important; border-radius: 6px !important;
    font-weight: 700 !important; font-size: 12px !important;
    padding: 2px 9px !important; height: 28px !important; min-height: 28px !important;
    box-shadow: none !important;
}
html body .save-btn div[data-testid="stButton"] > button:hover { background: #e0f2fe !important; }

/* ─── Inline task result (success/fail tag under each row) ─── */
.task-result-row {
    padding: 6px 4px 10px;
    border-bottom: 1px solid #f1f5f9;
    margin-bottom: 4px;
}
.task-result-ok   { background: #f0fdf4; border-radius: 8px; padding: 8px 14px; font-size: 12.5px; color: #166534; font-weight: 500; }
.task-result-fail { background: #fef2f2; border-radius: 8px; padding: 8px 14px; font-size: 12.5px; color: #991b1b; font-weight: 500; }

/* ─── Exec log panel ─── */
.exec-panel {
    background: #fff; border: 1px solid #e8ecf0; border-left: 3px solid #2563eb;
    border-radius: 10px; padding: 14px 18px; margin-top: 10px;
    box-shadow: 0 1px 3px rgba(15,23,42,.04);
}
.exec-panel.ok   { border-left-color: #059669; }
.exec-panel.fail { border-left-color: #dc2626; }
.exec-panel-title { font-size: 13px; font-weight: 700; color: #0f172a; margin-bottom: 8px; display:flex; align-items:center; gap:8px; }
.exec-label { font-size: 9.5px; font-weight: 700; letter-spacing:.1em; text-transform:uppercase; color:#94a3b8; margin: 10px 0 5px; }

/* ─── Log box ─── */
.log-box {
    background: #0d1117; border: 1px solid #1e2d3d; border-radius: 9px;
    padding: 12px 16px;
    font-family: 'DM Mono','Menlo',monospace; font-size: 12px;
    line-height: 1.75; white-space: pre-wrap; word-break: break-all;
    max-height: 380px; overflow: auto;
}
.log-err    { color: #f87171; display: block; }
.log-ok     { color: #4ade80; display: block; }
.log-warn   { color: #fbbf24; display: block; }
.log-info   { color: #60a5fa; display: block; }
.log-normal { color: #94a3b8; display: block; }
.log-meta   { font-size: 11px; color: #64748b; font-weight: 500; margin-bottom: 7px; }

/* ─── Next-run chip ─── */
.next-run {
    display: inline-flex; align-items: center; gap: 5px;
    background: #f8fafc; border: 1px solid #e8ecf0; border-radius: 7px;
    padding: 3px 9px; font-size: 11px; font-weight: 600; color: #475569;
}

/* ─── Command preview ─── */
.cmd-preview {
    background: #1e293b; color: #94a3b8; border-radius: 9px;
    padding: 10px 16px; font-family: 'DM Mono','Menlo',monospace; font-size: 12px;
    margin: 10px 0 14px; word-break: break-all;
}
.cmd-preview .cmd-hl { color: #60a5fa; }
.cmd-preview .cmd-arg { color: #a3e635; }
.cmd-preview .cmd-city { color: #fb923c; }

/* ─── Empty state ─── */
.empty-state {
    text-align: center; padding: 28px 20px; color: #94a3b8; font-size: 12.5px; font-weight: 500;
    background: #f8fafc; border-radius: 9px; border: 1px dashed #dde2e8;
}
.empty-state .icon { font-size: 26px; display: block; margin-bottom: 7px; }

/* ─── Date range chip ─── */
.date-chip {
    display: inline-flex; align-items: center; gap: 6px;
    background: #f0f9ff; border: 1px solid #bae6fd; border-radius: 8px;
    padding: 5px 12px; font-size: 12.5px; font-weight: 600; color: #0369a1; margin: 8px 0 12px;
}

/* ─── Streamlit overrides ─── */
div[data-testid="stButton"] > button {
    background: #1e293b !important; color: #f8fafc !important; border: none !important;
    border-radius: 8px !important; font-weight: 600 !important; font-size: 13px !important;
    padding: 8px 18px !important; box-shadow: 0 1px 3px rgba(15,23,42,.12) !important;
}
div[data-testid="stButton"] > button:hover { background: #0f172a !important; }

div[data-testid="stSelectbox"] > div > div,
div[data-testid="stTextInput"] > div > div > input {
    background: #fff !important; border: 1px solid #d1d9e0 !important;
    border-radius: 8px !important; color: #1e293b !important; font-size: 13.5px !important;
}
div[data-testid="stSelectbox"] label,
div[data-testid="stTextInput"] label,
div[data-testid="stTextArea"] label,
div[data-testid="stRadio"] label {
    color: #374151 !important; font-size: 13px !important; font-weight: 600 !important;
}
div[data-testid="stTextArea"] textarea {
    border: 1px solid #d1d9e0 !important; border-radius: 8px !important;
    font-family: 'DM Mono',monospace !important; font-size: 12.5px !important;
    color: #1e293b !important; background: #fafafa !important;
}
div[data-testid="stMetric"] {
    background: #fff; border-radius: 11px; padding: 14px 16px;
    border: 1px solid #e8ecf0; box-shadow: 0 1px 3px rgba(15,23,42,.04);
}
div[data-testid="stMetric"] label { color: #475569 !important; font-size: 12px !important; font-weight: 600 !important; }
div[data-testid="stMetric"] [data-testid="stMetricValue"] { color: #0f172a !important; font-size: 26px !important; font-weight: 700 !important; }
div[data-testid="stDataFrame"] { border-radius: 9px !important; overflow: hidden !important; border: 1px solid #e8ecf0 !important; }
div[data-testid="stAlert"] { border-radius: 9px !important; font-size: 13px !important; font-weight: 500 !important; }
.stCaption, div[data-testid="stCaption"] { color: #64748b !important; font-size: 11.5px !important; font-weight: 500 !important; }
div[data-testid="stCheckbox"] label { color: #374151 !important; font-size: 13px !important; font-weight: 500 !important; }
h3 { color: #0f172a !important; font-size: 22px !important; font-weight: 700 !important; }

/* ─── Friendly dropdown navigation ─── */
.mobile-nav-card {
    background: #ffffff;
    border: 1px solid #e8ecf0;
    border-radius: 14px;
    padding: 14px 16px;
    margin: 16px 0 20px;
    box-shadow: 0 1px 8px rgba(15,23,42,.06);
}
.mobile-nav-card label {
    font-size: 13px !important;
    font-weight: 700 !important;
    color: #334155 !important;
}
.mobile-nav-card div[data-testid="stSelectbox"] > div > div {
    min-height: 44px !important;
    font-size: 15px !important;
    border-radius: 12px !important;
    border: 1.5px solid #dbe3ea !important;
    background: #f8fafc !important;
}
.mobile-nav-hint {
    margin-top: 6px;
    font-size: 12px;
    color: #64748b;
    font-weight: 500;
}
@media (max-width: 768px) {
    .block-container { padding: 0 1rem 3rem !important; }
    .topbar { margin: 0 -1rem; padding: 0 16px; }
    .topbar-inner { height: 58px; }
    .topbar-name { font-size: 17px; }
    .topbar-logo { font-size: 23px; }
    .topbar-sep { display: none; }
    .topbar-clock { font-size: 11px; }
    .page-header { display: block; padding: 18px 0 14px; margin-bottom: 16px; }
    .page-title { font-size: 26px; line-height: 1.15; }
    .page-subtitle { font-size: 11px; margin-top: 6px; }
    .kpi-row { flex-direction: column; gap: 10px; }
    .section-card { padding: 16px 14px; }
    div[data-testid="stMetric"] [data-testid="stMetricValue"] { font-size: 22px !important; }
}

/* ─── Report page tabs (real Streamlit st.tabs DOM — verified against the
   installed Streamlit build, not guessed from older-version docs) ─── */
div[data-testid="stTabs"] [role="tablist"] {
    gap: 4px;
    border-bottom: 1px solid #e8ecf0;
    margin-bottom: 4px;
}
div[data-testid="stTabs"] [data-testid="stTab"] {
    padding: 12px 18px !important;
    border-radius: 9px 9px 0 0 !important;
    font-family: 'DM Sans','PingFang TC','Noto Sans TC',sans-serif !important;
    font-weight: 600 !important;
    font-size: 14.5px !important;
    color: #64748b !important;
    background: transparent !important;
    transition: background .15s ease, color .15s ease;
}
div[data-testid="stTabs"] [data-testid="stTab"] p {
    font-weight: inherit !important;
    font-size: inherit !important;
}
div[data-testid="stTabs"] [data-testid="stTab"]:hover {
    color: #1e293b !important;
    background: #f4f6f9 !important;
}
div[data-testid="stTabs"] [data-testid="stTab"][aria-selected="true"] {
    color: #2563eb !important;
    background: #eff6ff !important;
}
div[data-testid="stTabs"] [data-testid="stTab"] .react-aria-SelectionIndicator {
    background: #2563eb !important;
    height: 3px !important;
    border-radius: 3px 3px 0 0 !important;
}
div[data-testid="stTabs"] [role="tabpanel"] { padding-top: 20px; }

/* Nested tabs (月度追蹤 sub-tabs) read a touch smaller/quieter so the page
   keeps a clear top-tab → sub-tab hierarchy instead of two identical rows. */
div[class*="st-key-nested_tabs"] div[data-testid="stTabs"] [data-testid="stTab"] {
    padding: 8px 14px !important;
    font-size: 13px !important;
    border-radius: 7px 7px 0 0 !important;
}
div[class*="st-key-nested_tabs"] div[data-testid="stTabs"] [data-testid="stTab"] .react-aria-SelectionIndicator {
    height: 2px !important;
}
div[class*="st-key-nested_tabs"] div[data-testid="stTabs"] [role="tablist"] {
    margin-bottom: 2px;
}

/* ─── Report cards rendered as real st.container(border=True, key="perfcard_*")
   blocks (so widgets placed inside actually sit inside the card, unlike a
   hand-written <div> that closes before the widget renders) ─── */
div[class*="st-key-perfcard_"] {
    background: #fff !important;
    border: 1px solid #e8ecf0 !important;
    border-radius: 12px !important;
    padding: 20px 22px 18px !important;
    margin-bottom: 16px !important;
    box-shadow: 0 1px 3px rgba(15,23,42,.04), 0 3px 10px rgba(15,23,42,.04) !important;
}

/* ─── Alerts (st.info / st.warning / st.success empty-state messages) ─── */
div[data-testid="stAlertContainer"] {
    border-radius: 0 10px 10px 0 !important;
    padding: 13px 16px !important;
    font-size: 13px !important;
    font-weight: 500 !important;
    box-shadow: 0 1px 3px rgba(15,23,42,.04) !important;
    border-left-width: 3px !important;
    border-left-style: solid !important;
}
div[data-testid="stAlertContainer"] p { font-weight: 500 !important; margin: 0 !important; }

/* ─── Date pickers / multiselects used by the 訂購日期 & 月份區間 tabs ─── */
div[data-testid="stDateInput"] input {
    background: #fff !important; border: 1px solid #d1d9e0 !important;
    border-radius: 8px !important; color: #1e293b !important; font-size: 13.5px !important;
}
div[data-testid="stDateInput"] label,
div[data-testid="stMultiSelect"] label {
    color: #374151 !important; font-size: 13px !important; font-weight: 600 !important;
}
div[data-testid="stMultiSelect"] > div > div {
    background: #fff !important; border: 1px solid #d1d9e0 !important; border-radius: 8px !important;
}

</style>
""", unsafe_allow_html=True)

# ── Topbar ────────────────────────────────────────────────────────────────────
now_str = datetime.now(TZ_TAIPEI).strftime("%Y/%m/%d  %H:%M")
st.markdown(
    f"""<div class="topbar">
      <div class="topbar-inner">
        <div class="topbar-brand">
          <span class="topbar-logo">🍋</span>
          <span class="topbar-name">Jenny 排程控制台</span>
        </div>
        <div class="topbar-sep"></div>
        <div class="topbar-clock">🕐 {now_str}</div>
      </div>
    </div>""",
    unsafe_allow_html=True,
)

# ── Navigation dropdown ───────────────────────────────────────────────────────
def _read_csv_safe(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        return pd.read_csv(path, encoding="utf-8-sig")
    except Exception:
        try:
            return pd.read_csv(path)
        except Exception:
            return pd.DataFrame()




def _sort_report_rows_for_display(df: pd.DataFrame) -> pd.DataFrame:
    """Sort report rows strictly by the 日期 column, newest first.

    This fixes old rows whose id format differs from newer rows. The display
    order must never depend on id, because old ids such as 20260503010745 and
    new ids such as 20260506152607_本月 do not sort reliably together.
    """
    if df is None or df.empty:
        return df

    out = df.copy()

    if "日期" in out.columns:
        sort_dt = pd.to_datetime(out["日期"], errors="coerce")

        # If some very old rows have an unparseable 日期, recover datetime from id.
        if "id" in out.columns:
            id_text = out["id"].astype(str).str.extract(r"^(\d{14})", expand=False)
            id_dt = pd.to_datetime(id_text, format="%Y%m%d%H%M%S", errors="coerce")
            sort_dt = sort_dt.fillna(id_dt)

        out["_sort_dt"] = sort_dt

        # Repair old rows where 統計月份 was blank/None, using 日期 when possible.
        if "統計月份" in out.columns:
            month_text = out["_sort_dt"].dt.strftime("%Y/%m")
            old_month = out["統計月份"].astype(str).str.strip()
            missing_month = out["統計月份"].isna() | old_month.isin(["", "None", "nan", "NaT"])
            out.loc[missing_month, "統計月份"] = month_text[missing_month]

        # Strictly sort by actual datetime only. Do not use id as secondary sort.
        out = out.sort_values("_sort_dt", ascending=False, na_position="last")
        out = out.drop(columns=["_sort_dt"])

    return out.reset_index(drop=True)


def _format_report_df(df: pd.DataFrame) -> pd.DataFrame:
    # 保留數值型別（不轉成千分位字串），這樣 st.dataframe 才會用它原生的數字欄位
    # 靠右對齊；千分位、百分比等顯示格式改由 _report_column_config() 的
    # column_config 負責。
    out = df.copy()
    for col in out.columns:
        col_text = str(col)
        if any(k in col_text for k in ["業績", "合計", "總業績", "加總", "付款"]):
            out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0).astype(int)
        elif "時數" in col_text:
            out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0)
        elif "佔比" in col_text:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def _report_column_config(df: pd.DataFrame) -> dict:
    """讓數字欄位在 st.dataframe 用原生 NumberColumn 呈現，靠右對齊、帶千分位。"""
    config = {}
    for col in df.columns:
        col_text = str(col)
        if "佔比" in col_text:
            config[col] = st.column_config.NumberColumn(col_text, format="percent")
        elif "時數" in col_text or any(k in col_text for k in ["業績", "合計", "總業績", "加總", "付款"]):
            config[col] = st.column_config.NumberColumn(col_text, format="localized")
    return config


def _delete_rows_from_csv(path: str, selected_ids) -> bool:
    if not selected_ids or not os.path.exists(path):
        return False

    df = _read_csv_safe(path)
    if df.empty or "id" not in df.columns:
        return False

    before = len(df)
    df = df[~df["id"].astype(str).isin([str(x) for x in selected_ids])].copy()
    if len(df) == before:
        return False

    df.to_csv(path, index=False, encoding="utf-8-sig")
    return True


def _show_deletable_csv_section(
    title: str,
    path: str,
    empty_msg: str,
    key_prefix: str,
    fallback_df = None,
    source_note = None,
    display_df = None,
):
    # 預設讀 CSV；當月/次月追蹤會傳入 display_df，確保畫面直接使用
    # latest/df4.csv 補出的最新列，不會因 CSV 寫入或快取問題顯示舊資料。
    df = display_df.copy() if display_df is not None else _read_csv_safe(path)
    df = _sort_report_rows_for_display(df)

    if df.empty:
        st.info(empty_msg)
        return

    if "id" in df.columns and os.path.exists(path):
        options = df["id"].astype(str).tolist()
        selected = st.multiselect(
            "勾選要刪除的紀錄",
            options=options,
            key=f"{key_prefix}_delete_ids",
        )
        if st.button("🗑️ 刪除勾選列", key=f"{key_prefix}_delete_btn", use_container_width=True):
            if _delete_rows_from_csv(path, selected):
                st.success(f"已刪除 {len(selected)} 筆紀錄")
                st.rerun()
            else:
                st.warning("沒有刪除任何資料，請先勾選紀錄。")

    st.dataframe(
        _format_report_df(df), use_container_width=True, hide_index=True, height=360,
        column_config=_report_column_config(df),
    )

def _show_csv_section(title: str, path: str, empty_msg: str):
    _show_deletable_csv_section(title, path, empty_msg, key_prefix=title.replace(" ", "_"))


def _get_latest_payload_time() -> datetime:
    """Return the update time shown by the dashboard.

    Prefer latest/meta.json updated_at so the monthly-tracking row uses the
    same timestamp as the blue "最新更新時間" banner. Fall back to df4.csv
    mtime when meta.json is unavailable.
    """
    meta_path = os.path.join(LATEST_DIR, "meta.json")
    try:
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            updated_at = str(meta.get("updated_at") or "").strip()
            if updated_at:
                return datetime.strptime(updated_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ_TAIPEI)
    except Exception:
        pass

    df4_path = os.path.join(LATEST_DIR, "df4.csv")
    try:
        if os.path.exists(df4_path):
            return datetime.fromtimestamp(os.path.getmtime(df4_path), TZ_TAIPEI)
    except Exception:
        pass

    return datetime.now(TZ_TAIPEI)


def _get_latest_df4_mtime_ns() -> int:
    """Return latest df4.csv mtime in nanoseconds.

    This is the update event key. Every time the user presses 更新資料 and
    df4.csv is overwritten, this value changes, so the monthly tracking tabs
    can append one current-month row and one next-month row for that exact run.
    """
    df4_path = os.path.join(LATEST_DIR, "df4.csv")
    try:
        if os.path.exists(df4_path):
            return int(os.stat(df4_path).st_mtime_ns)
    except Exception:
        pass
    return 0


def _dt_from_ns(ns: int) -> datetime:
    if ns:
        return datetime.fromtimestamp(ns / 1_000_000_000, TZ_TAIPEI)
    return datetime.now(TZ_TAIPEI)

def _period_config(period_label: str, dt: datetime):
    if period_label == "次月":
        amount_col = "次月加總"
        ratio_col = "次月佔比"
        y, m = dt.year, dt.month
        stat_month = f"{y + 1}/01" if m == 12 else f"{y}/{m + 1:02d}"
    else:
        amount_col = "本月加總"
        ratio_col = "本月佔比"
        stat_month = dt.strftime("%Y/%m")
    return amount_col, ratio_col, stat_month


def _build_overview_from_df4(period_label: str, row_dt = None, run_key = None) -> pd.DataFrame:
    """Build one overview row from latest df4.csv using current/next logic."""
    df4 = _read_csv_safe(os.path.join(LATEST_DIR, "df4.csv"))
    cols = [
        "id", "來源", "統計月份", "日期",
        "台北業績", "台北佔比", "台中業績", "台中佔比",
        "桃園業績", "桃園佔比", "新竹業績", "新竹佔比",
        "高雄業績", "高雄佔比", "全區合計",
    ]
    if df4.empty:
        return pd.DataFrame(columns=cols)

    row_dt = row_dt or _get_latest_payload_time()
    amount_col, ratio_col, stat_month = _period_config(period_label, row_dt)

    def get_val(city: str, col: str):
        row = df4[df4["城市"].astype(str) == city]
        if row.empty or col not in df4.columns:
            return 0
        return row.iloc[0][col]

    source = "dashboard"
    try:
        meta_path = os.path.join(LATEST_DIR, "meta.json")
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            source = str(meta.get("trigger") or source)
    except Exception:
        pass

    row = {
        "id": f"{run_key or row_dt.strftime('%Y%m%d%H%M%S%f')}_{period_label}",
        "來源": source,
        "統計月份": stat_month,
        "日期": row_dt.strftime("%Y/%m/%d %H:%M"),
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
    return pd.DataFrame([row], columns=cols)


def _sync_period_csv_from_df4(path: str, period_label: str) -> pd.DataFrame:
    """Return monthly tracking with the latest df4 row guaranteed at top.

    The top table latest/df4.csv is the source of truth. This function first
    builds the exact row that should match the blue latest timestamp, then
    merges it with the existing historical CSV. The returned dataframe is used
    directly for display, so even if an old CSV was not updated by the report
    writer, the lower table still immediately matches the top summary.
    """
    row_dt = _get_latest_payload_time()
    run_key = row_dt.strftime("%Y%m%d%H%M%S")
    new_df = _build_overview_from_df4(period_label, row_dt=row_dt, run_key=run_key)

    if new_df.empty:
        return _read_csv_safe(path)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    old_df = _read_csv_safe(path)

    cols = list(new_df.columns)
    if old_df.empty:
        out = new_df.copy()
    else:
        for c in cols:
            if c not in old_df.columns:
                old_df[c] = ""
        old_df = old_df[cols].copy()

        new_id = str(new_df.iloc[0]["id"])
        # Remove only the same event id, then put the latest df4-derived row
        # at the top. This also repairs bad old rows with the same id.
        if "id" in old_df.columns:
            old_df = old_df[old_df["id"].astype(str) != new_id].copy()
        out = pd.concat([new_df, old_df], ignore_index=True)

    if "日期" in out.columns:
        out["_sort_dt"] = pd.to_datetime(out["日期"], errors="coerce")
        out = out.sort_values("_sort_dt", ascending=False, na_position="last").drop(columns=["_sort_dt"])
    out = out.reset_index(drop=True)

    # 只回傳給畫面使用，不寫回 CSV。
    # CSV 只能由 performance_report.generate_sales_report() 在更新資料/排程時寫入。
    return out

def _show_period_section(title: str, filename: str, period_label: str):
    # 只讀取 performance_report.py 已經寫好的 CSV。
    # 注意：畫面 render 不可以 append / 排序後寫回 CSV，否則 Streamlit rerun 會造成資料重複。
    path = os.path.join(LATEST_DIR, filename)
    _show_deletable_csv_section(
        title=title,
        path=path,
        empty_msg="目前沒有資料。請先按『更新資料』。",
        key_prefix=f"period_{period_label}",
        fallback_df=None,
        source_note=None,
        display_df=None,
    )

def _show_month_end_snapshot_tab():
    latest_path = os.path.join(LATEST_DIR, "month_end_summary.csv")
    history_path = os.path.join(DAILY_HISTORY_DIR, "month_end_summary.csv")

    latest_df = _read_csv_safe(latest_path)
    history_df = _read_csv_safe(history_path)

    if latest_df.empty and history_df.empty:
        st.info("目前還沒有月底快照。系統會在每月最後一天更新資料時，記錄當月與次月各區業績及總業績。")
        st.caption(f"目前找不到檔案：{latest_path} 或 {history_path}")
        return

    if not latest_df.empty:
        _show_deletable_csv_section(
            "最近一次月底快照",
            latest_path,
            "目前 latest 裡還沒有月底快照檔。",
            key_prefix="month_end_latest",
        )

    if not history_df.empty:
        _show_deletable_csv_section(
            "月底快照歷史",
            history_path,
            "目前還沒有月底快照歷史。",
            key_prefix="month_end_history",
        )



def _render_page_without_builtin_daily_overview():
    """Render the original performance page, but suppress blocks now owned here.

    This app renders one combined monthly-tracking area and places the email
    preview below it. Therefore the old standalone daily overview and the old
    email preview from dashboard_main are hidden during the original render.
    """
    original = {
        "markdown": st.markdown,
        "caption": st.caption,
        "multiselect": st.multiselect,
        "button": st.button,
        "dataframe": st.dataframe,
        "info": st.info,
        "warning": st.warning,
        "success": st.success,
        "write": st.write,
        "expander": st.expander,
        "components_html": components.html,
    }
    skipping = {"on": False}

    class _SkipContext:
        def __enter__(self):
            skipping["on"] = True
            return self
        def __exit__(self, exc_type, exc, tb):
            skipping["on"] = False
            return False

    def should_start_skip(args, kwargs):
        text = ""
        if args:
            text = str(args[0])
        elif "body" in kwargs:
            text = str(kwargs.get("body"))
        # dashboard_main's old standalone block. Once this starts, everything
        # after it in that render is skipped; this file renders the replacement.
        return "當月每日業績總覽" in text

    def patch_markdown(*args, **kwargs):
        if should_start_skip(args, kwargs):
            skipping["on"] = True
            return None
        if skipping["on"]:
            return None
        return original["markdown"](*args, **kwargs)

    def patch_expander(label, *args, **kwargs):
        if "信件預覽" in str(label):
            return _SkipContext()
        if skipping["on"]:
            return _SkipContext()
        return original["expander"](label, *args, **kwargs)

    def patch_components_html(*args, **kwargs):
        if skipping["on"]:
            return None
        return original["components_html"](*args, **kwargs)

    def patch_noop(name):
        def inner(*args, **kwargs):
            if skipping["on"]:
                if name == "multiselect":
                    return []
                if name == "button":
                    return False
                return None
            return original[name](*args, **kwargs)
        return inner

    try:
        st.markdown = patch_markdown
        st.caption = patch_noop("caption")
        st.multiselect = patch_noop("multiselect")
        st.button = patch_noop("button")
        st.dataframe = patch_noop("dataframe")
        st.info = patch_noop("info")
        st.warning = patch_noop("warning")
        st.success = patch_noop("success")
        st.write = patch_noop("write")
        st.expander = patch_expander
        components.html = patch_components_html
        render_page("業績報表")
    finally:
        st.markdown = original["markdown"]
        st.caption = original["caption"]
        st.multiselect = original["multiselect"]
        st.button = original["button"]
        st.dataframe = original["dataframe"]
        st.info = original["info"]
        st.warning = original["warning"]
        st.success = original["success"]
        st.write = original["write"]
        st.expander = original["expander"]
        components.html = original["components_html"]


def render_email_preview_section():
    html_path = os.path.join(LATEST_DIR, "email_preview.html")
    with st.expander("📧 信件預覽", expanded=False):
        if not os.path.exists(html_path):
            st.info("目前沒有信件預覽。請先按『更新資料』產生 email_preview.html。")
            return
        try:
            with open(html_path, "r", encoding="utf-8") as f:
                html = f.read()
            if not html.strip():
                st.info("信件預覽檔案是空的。請重新更新資料。")
                return
            components.html(html, height=520, scrolling=True)
        except Exception as e:
            st.warning(f"無法讀取信件預覽：{e}")

def render_monthly_tracking_tabs():
    # 取代 dashboard_main 原本單獨的「當月每日業績總覽」。
    # 三個區塊整併在同一個頁籤區，且各自保留刪除功能。
    st.markdown(
        '<div class="page-header"><div class="page-title">月度追蹤</div>'
        '<div class="page-subtitle">CURRENT / NEXT MONTH / SNAPSHOT</div></div>',
        unsafe_allow_html=True,
    )

    with st.container(key="nested_tabs_monthly"):
        tab_current, tab_next, tab_snapshot = st.tabs(["當月每日業績", "次月每日業績", "月底快照"])

        with tab_current:
            st.caption("資料來源：上方各區月度摘要（df4.csv）的本月加總；每次『更新資料』會新增一筆，舊紀錄會保留，除非手動勾選刪除。")
            _show_period_section("當月每日業績總覽", "daily_df.csv", "本月")

        with tab_next:
            st.caption("資料來源：上方各區月度摘要（df4.csv）的次月加總；每次『更新資料』會新增一筆，舊紀錄會保留，除非手動勾選刪除。")
            _show_period_section("次月每日業績總覽", "next_month_daily_df.csv", "次月")

        with tab_snapshot:
            _show_month_end_snapshot_tab()


def _load_report_meta() -> dict:
    path = os.path.join(LATEST_DIR, "meta.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _month_date(value, fallback):
    text = str(value or "").replace("/", "-")
    try:
        return datetime.strptime(text, "%Y-%m").date()
    except Exception:
        return fallback


def _run_filtered_performance_report(scope: str):
    today = datetime.now(TZ_TAIPEI).date()
    order_start = st.session_state.get("performance_order_start_date", today)
    order_end = st.session_state.get("performance_order_end_date", today)
    month_start = st.session_state.get("performance_report_start_month", today.replace(day=1))
    month_end = st.session_state.get("performance_report_end_month", today.replace(day=1))

    if scope == "order" and order_start > order_end:
        st.error("訂購日期迄日不可早於起日")
        return
    if scope == "month" and month_start.replace(day=1) > month_end.replace(day=1):
        st.error("結束月份不可早於起始月份")
        return

    if scope == "order":
        with st.spinner("正在更新付款彙總…"):
            _performance_report.generate_order_date_report(
                order_start.strftime("%Y-%m-%d"),
                order_end.strftime("%Y-%m-%d"),
                trigger="dashboard",
            )
            _order_date_report_ui.generate_service_report(
                order_start.strftime("%Y-%m-%d"),
                order_end.strftime("%Y-%m-%d"),
                trigger="dashboard",
            )
    elif scope == "month":
        with st.spinner("正在更新月份業績與保留單…"):
            result = _performance_report.generate_month_range_reports(
                month_start.strftime("%Y-%m"),
                month_end.strftime("%Y-%m"),
                trigger="dashboard",
            )
        if result.get("error"):
            st.warning(result["error"])
    else:
        raise ValueError(f"未知報表更新範圍：{scope}")
    st.rerun()


def _show_summary_csv(filename: str, empty_message: str):
    df = _read_csv_safe(os.path.join(LATEST_DIR, filename))
    if df.empty:
        st.info(empty_message)
        return
    st.dataframe(
        _format_report_df(df), use_container_width=True, hide_index=True, height=360,
        column_config=_report_column_config(df),
    )


def _show_order_date_summary_tables(filename: str, empty_message: str):
    """顯示待付款、已付款及待付款＋已付款三張付款彙總表。"""
    df = _read_csv_safe(os.path.join(LATEST_DIR, filename))
    if df.empty:
        st.info(empty_message)
        return

    combined_cols = {c for c in df.columns if "＋" in c}
    unpaid_cols = ["地區"] + [c for c in df.columns if c not in combined_cols and "待付款" in c]
    paid_cols = ["地區"] + [c for c in df.columns if c not in combined_cols and "已付款" in c]

    st.markdown('<div class="section-title">待付款</div>', unsafe_allow_html=True)
    unpaid_df = df[unpaid_cols]
    st.dataframe(
        _format_report_df(unpaid_df), use_container_width=True, hide_index=True, height=280,
        column_config=_report_column_config(unpaid_df),
    )

    st.markdown('<div class="section-title">已付款</div>', unsafe_allow_html=True)
    paid_df = df[paid_cols]
    st.dataframe(
        _format_report_df(paid_df), use_container_width=True, hide_index=True, height=280,
        column_config=_report_column_config(paid_df),
    )

    _order_date_report_ui.show_combined_table(
        df, _format_report_df, _report_column_config
    )


def render_order_date_report_tab():
    today = datetime.now(TZ_TAIPEI).date()
    st.markdown(
        '<div class="page-header"><div class="page-title">訂購日期付款彙總</div>'
        '<div class="page-subtitle">ORDER DATE / PAYMENT</div></div>',
        unsafe_allow_html=True,
    )
    cols = st.columns(2)
    with cols[0]:
        order_start = st.date_input(
            "訂購日期－起",
            value=today,
            key="performance_order_start_date",
        )
    with cols[1]:
        order_end = st.date_input(
            "訂購日期－迄",
            value=today,
            key="performance_order_end_date",
        )
    if order_start > order_end:
        st.error("訂購日期迄日不可早於起日")
    if st.button("✅ 確定並套用訂購日期區間", key="apply_order_date_range", use_container_width=True):
        _run_filtered_performance_report("order")
    st.caption("預設起迄日皆為當日，依訂購日期查詢，結果依地區統計。上方待付款、已付款及待付款＋已付款三張表只統計清潔類服務，排除家電、水洗與收納；每張底下再依服務日期拆出固定的月份欄位（本月＋4個月），金額都是後端財務彙總表直接算好的含稅總金額（已扣車馬費），跟地區/加總欄位同一套邏輯、口徑一致。儲值金（跟「目前總表」用同一套判斷邏輯，非用儲值金付款的清潔訂單）獨立成每張表各自的「儲值金待付款/儲值金已付款」欄位，不拆月份。")
    _show_order_date_summary_tables("order_date_summary.csv", "尚未產生付款彙總，請選擇日期後按確定。")
    _order_date_report_ui.show_service_tables(
        LATEST_DIR, _read_csv_safe, _format_report_df, _report_column_config
    )


def render_month_performance_report_tab():
    meta = _load_report_meta()
    current_month = datetime.now(TZ_TAIPEI).date().replace(day=1)
    default_start = _month_date(meta.get("report_start_month"), current_month)
    default_end = _month_date(meta.get("report_end_month"), default_start)

    st.markdown(
        '<div class="page-header"><div class="page-title">月份業績統整</div>'
        '<div class="page-subtitle">PERFORMANCE / RESERVE</div></div>',
        unsafe_allow_html=True,
    )
    cols = st.columns(2)
    with cols[0]:
        month_start = st.date_input(
            "起始月份（日期只取年月）",
            value=default_start,
            key="performance_report_start_month",
        )
    with cols[1]:
        month_end = st.date_input(
            "結束月份（日期只取年月）",
            value=default_end,
            key="performance_report_end_month",
        )
    if month_start.replace(day=1) > month_end.replace(day=1):
        st.error("結束月份不可早於起始月份")
    if st.button("✅ 確定並套用月份區間", key="apply_month_range", use_container_width=True):
        _run_filtered_performance_report("month")
    st.caption("起迄月份均包含在統計範圍內，最多可選 24 個月。")

    with st.container(border=True, key="perfcard_net"):
        st.markdown('<div class="section-title">➖ 該月業績－該月保留單業績</div>', unsafe_allow_html=True)
        _show_summary_csv("net_performance_summary.csv", "尚未產生扣除後業績，請先套用月份區間。")

    with st.container(border=True, key="perfcard_month"):
        st.markdown('<div class="section-title">📊 該月業績報表</div>', unsafe_allow_html=True)
        _show_summary_csv("month_performance_summary.csv", "尚未產生該月業績，請先套用月份區間。")

    with st.container(border=True, key="perfcard_reserve"):
        st.markdown('<div class="section-title">🕒 該月保留單業績</div>', unsafe_allow_html=True)
        st.caption("保留單時數以『人數 × 每人服務時數』計算。")
        _show_summary_csv("reserve_summary.csv", "尚未產生保留單業績，請先套用月份區間。")


def render_performance_report_page():
    current_tab, order_tab, month_tab = st.tabs([
        "📍 目前總表",
        "🧾 訂購日期付款彙總",
        "📅 月份業績統整",
    ])
    with current_tab:
        _render_page_without_builtin_daily_overview()
        render_monthly_tracking_tabs()
        render_email_preview_section()
    with order_tab:
        render_order_date_report_tab()
    with month_tab:
        render_month_performance_report_tab()


render_performance_report_page()
