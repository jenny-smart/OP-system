from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

from performance_report import (
    generate_sales_report,
    LATEST_DIR,
    REGION4_AMOUNT_COLS,
    REGION4_APPLIANCE_COLS,
    REGION4_INT_COLS,
    REGION4_RATIO_COLS,
)

TZ_TAIPEI = timezone(timedelta(hours=8))


def now_taipei():
    return datetime.now(TZ_TAIPEI)


def file_mtime(path):
    if not path or not path.exists():
        return "-"
    return datetime.fromtimestamp(
        path.stat().st_mtime,
        tz=TZ_TAIPEI
    ).strftime("%m/%d %H:%M")


def file_size_str(path):
    if not path or not path.exists():
        return "-"
    s = path.stat().st_size
    if s < 1024:
        return f"{s} B"
    if s < 1024 * 1024:
        return f"{s/1024:.1f} KB"
    return f"{s/1024/1024:.1f} MB"


def load_sales_latest_payload():
    ld = Path(LATEST_DIR)
    payload = {
        "df4": pd.DataFrame(),
        "daily_df": pd.DataFrame(),
        "meta": {},
        "email_html": "",
    }

    for key, fname in [
        ("df4", "df4.csv"),
        ("daily_df", "daily_df.csv"),
    ]:
        fp = ld / fname
        if fp.exists():
            try:
                payload[key] = pd.read_csv(fp, encoding="utf-8-sig")
            except Exception as e:
                payload[f"{key}_error"] = str(e)

    mp = ld / "meta.json"
    if mp.exists():
        try:
            import json
            payload["meta"] = json.loads(
                mp.read_text(encoding="utf-8")
            )
        except Exception:
            pass

    hp = ld / "email_preview.html"
    if hp.exists():
        payload["email_html"] = hp.read_text(encoding="utf-8")

    return payload


def _fmt_int(x):
    try:
        return f"{int(float(x)):,}"
    except Exception:
        return "—"


def _fmt_pct(x):
    try:
        return f"{float(x):.2%}"
    except Exception:
        return "—"


def render_html_table(df, right_cols, pct_cols, int_cols):
    def cell(val, col):
        if pd.isna(val) or str(val).strip() in ("", "nan"):
            return "—"
        if col in pct_cols:
            return _fmt_pct(val)
        if col in int_cols:
            return _fmt_int(val)
        return str(val)

    th = (
        "padding:10px 14px;font-size:10px;font-weight:700;"
        "color:#64748b;border-bottom:2px solid #e8ecf0;"
        "white-space:nowrap;background:#fafafa;"
    )
    td = (
        "padding:9px 14px;font-size:13px;color:#1e293b;"
        "border-bottom:1px solid #f1f5f9;white-space:nowrap;"
    )

    headers = "".join(
        f'<th style="{th}text-align:{"right" if c in right_cols else "left"}">{c}</th>'
        for c in df.columns
    )

    rows = []
    for _, row in df.iterrows():
        cells = "".join(
            f'<td style="{td}text-align:{"right" if c in right_cols else "left"}">'
            f'{cell(row[c], c)}</td>'
            for c in df.columns
        )
        rows.append(f"<tr>{cells}</tr>")

    return (
        '<div style="overflow-x:auto;border:1px solid #e8ecf0;border-radius:10px;">'
        '<table style="width:100%;border-collapse:collapse;background:#fff;">'
        f'<thead><tr>{headers}</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
    )


def render_sales_page():
    st.markdown(
        '<div class="page-header"><div class="page-title">業績報表</div>'
        '<div class="page-subtitle">Latest Data · Send Later</div></div>',
        unsafe_allow_html=True
    )

    result = None
    c1, c2, c3 = st.columns([1, 1, 1.5])
    with c1:
        update_btn = st.button("🔄 更新資料", use_container_width=True)
    with c2:
        send_btn = st.button("📧 寄送目前結果", use_container_width=True)
    with c3:
        if st.button("📂 重新讀取已存資料", use_container_width=True):
            st.rerun()

    if update_btn:
        with st.spinner("更新資料中…"):
            order_start = st.session_state.get("performance_order_start_date")
            order_end = st.session_state.get("performance_order_end_date")
            month_start = st.session_state.get("performance_report_start_month")
            month_end = st.session_state.get("performance_report_end_month")
            result = generate_sales_report(
                send_email=False,
                persist_dashboard=False,
                trigger="dashboard",
                order_start_date=order_start.strftime("%Y-%m-%d") if order_start else None,
                order_end_date=order_end.strftime("%Y-%m-%d") if order_end else None,
                report_start_month=month_start.strftime("%Y-%m") if month_start else None,
                report_end_month=month_end.strftime("%Y-%m") if month_end else None,
                include_extra_reports=False,
            )

    if result is not None:
        df4 = result.get("df4", pd.DataFrame())
        daily_df = result.get("daily_df", pd.DataFrame())
        email_html = result.get("email_html", "")
        updated_at = now_taipei().strftime("%Y-%m-%d %H:%M:%S")
        error_msg = result.get("error")
    else:
        payload = load_sales_latest_payload()
        df4 = payload.get("df4", pd.DataFrame())
        daily_df = payload.get("daily_df", pd.DataFrame())
        meta = payload.get("meta", {})
        email_html = payload.get("email_html", "")
        raw_ts = meta.get("updated_at", "") if isinstance(meta, dict) else ""
        updated_at = raw_ts if raw_ts else "尚未產生資料"
        error_msg = meta.get("error") if isinstance(meta, dict) else None

        if payload.get("df4_error"):
            st.warning(f"df4.csv 讀取錯誤：{payload['df4_error']}")
        if payload.get("daily_df_error"):
            st.warning(f"daily_df.csv 讀取錯誤：{payload['daily_df_error']}")

    if send_btn:
        if df4.empty:
            st.warning("目前沒有可寄送資料，請先更新資料")
        else:
            try:
                from performance_report import send_region4_email
                send_region4_email(df4)
                st.success("寄信完成")
            except Exception as e:
                st.error(f"寄信失敗：{e}")

    if error_msg:
        st.error(f"上次執行有錯誤：{error_msg}")

    st.info(f"📅 最新更新時間：{updated_at}")

    total = None
    if not df4.empty:
        t = df4[df4["城市"] == "加總"]
        if not t.empty:
            total = t.iloc[0]

    def _kpi(col):
        # 舊的 df4.csv 還沒有新月份欄位時顯示「—」，按「更新資料」後就會有。
        if total is None or col not in total.index:
            return "—"
        return _fmt_int(total.get(col, 0))

    for cols_group in (REGION4_AMOUNT_COLS, REGION4_APPLIANCE_COLS + ["儲值金"]):
        for st_col, col in zip(st.columns(len(cols_group)), cols_group):
            st_col.metric(col, _kpi(col))

    with st.container(border=True, key="perfcard_region"):
        st.markdown('<div class="section-title">📊 各區月度摘要</div>', unsafe_allow_html=True)
        if df4.empty:
            st.markdown(
                '<div class="empty-state"><span class="icon">📭</span>目前沒有資料，請先按「更新資料」</div>',
                unsafe_allow_html=True
            )
        else:
            int4 = set(REGION4_INT_COLS)
            pct4 = set(REGION4_RATIO_COLS)
            st.markdown(
                render_html_table(df4, right_cols=int4 | pct4, pct_cols=pct4, int_cols=int4),
                unsafe_allow_html=True
            )

    st.markdown('<div class="section-card"><div class="section-title">📅 當月每日業績總覽</div>', unsafe_allow_html=True)

    df4_csv = Path(LATEST_DIR) / "df4.csv"
    daily_csv = Path(LATEST_DIR) / "daily_df.csv"

    parts = [
        f"daily_df.csv {'存在' if daily_csv.exists() else '⚠️ 不存在'}（{file_size_str(daily_csv)}，{file_mtime(daily_csv)}）",
        f"載入：{len(daily_df)} 行 × {len(daily_df.columns)} 欄"
    ]
    if not daily_df.empty:
        parts.append(
            f"欄位：{', '.join(daily_df.columns[:8].tolist())}{'…' if len(daily_df.columns) > 8 else ''}"
        )

    st.caption("  ·  ".join(parts))

    if daily_df.empty:
        reason = "daily_df.csv 不存在，請先按「更新資料」。" if not daily_csv.exists() else "CSV 存在但無資料列。"
        st.markdown(
            f'<div class="empty-state"><span class="icon">📭</span>{reason}</div>',
            unsafe_allow_html=True
        )
    else:
        if "id" in daily_df.columns:
            del_ids = st.multiselect(
                "勾選要刪除的每日業績總覽紀錄",
                options=daily_df["id"].astype(str).tolist(),
                key="del_daily_df_ids"
            )

            if st.button("🗑 刪除勾選列", key="del_daily_df_btn", use_container_width=True):
                keep_df = daily_df[~daily_df["id"].astype(str).isin([str(x) for x in del_ids])].copy()
                keep_df.to_csv(daily_csv, index=False, encoding="utf-8-sig")
                st.success(f"已刪除 {len(daily_df) - len(keep_df)} 筆")
                st.rerun()

        show_cols = [
            "id", "來源", "日期",
            "台北業績", "台北佔比",
            "台中業績", "台中佔比",
            "桃園業績", "桃園佔比",
            "新竹業績", "新竹佔比",
            "高雄業績", "高雄佔比",
            "全區合計"
        ]
        show_cols = [c for c in show_cols if c in daily_df.columns]

        int_d = {c for c in show_cols if "業績" in c or c == "全區合計"}
        pct_d = {c for c in show_cols if "佔比" in c}

        st.markdown(
            render_html_table(
                daily_df[show_cols].copy(),
                right_cols=int_d | pct_d,
                pct_cols=pct_d,
                int_cols=int_d
            ),
            unsafe_allow_html=True
        )

    st.markdown("</div>", unsafe_allow_html=True)

    if email_html:
        with st.expander("📧 信件預覽"):
            st.components.v1.html(email_html, height=520, scrolling=True)


def render_page(page="業績報表"):
    render_sales_page()
