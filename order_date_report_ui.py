import os

import pandas as pd
import streamlit as st

import order_date_service_report as service_report


def generate_service_report(order_start, order_end, trigger="dashboard"):
    return service_report.generate_service_payment_report(order_start, order_end, trigger=trigger)


def _show(df, format_df, column_config, height=280):
    st.dataframe(format_df(df), use_container_width=True, hide_index=True, height=height, column_config=column_config(df))


def show_combined_table(df, format_df, column_config):
    combined_cols = [c for c in df.columns if "＋" in c]
    if combined_cols:
        combined = df[["地區"] + combined_cols].copy()
    else:
        combined = pd.DataFrame({"地區": df["地區"]})
        for unpaid_col in [c for c in df.columns if "待付款" in c and "＋" not in c]:
            paid_col = unpaid_col.replace("待付款", "已付款")
            if paid_col in df.columns:
                combined_col = unpaid_col.replace("待付款", "待付款＋已付款")
                combined[combined_col] = pd.to_numeric(df[unpaid_col], errors="coerce").fillna(0) + pd.to_numeric(df[paid_col], errors="coerce").fillna(0)
    st.markdown('<div class="section-title">待付款＋已付款</div>', unsafe_allow_html=True)
    _show(combined, format_df, column_config)


def show_service_tables(latest_dir, read_csv, format_df, column_config):
    df = read_csv(os.path.join(latest_dir, "order_date_service_summary.csv"))
    if df.empty:
        st.info("尚未產生家電／水洗付款彙總，請重新套用訂購日期區間。")
        return
    st.markdown('<div class="section-title">家電／水洗付款（本月起 5 個月）</div>', unsafe_allow_html=True)
    for service in ("家電", "水洗"):
        left, right = st.columns(2)
        for container, status in ((left, "待付款"), (right, "已付款")):
            cols = ["地區"] + [c for c in df.columns if service in c and status in c]
            with container:
                st.markdown(f"**{service}{status}**")
                view = df[cols].copy()
                view.columns = ["地區"] + [c.replace(service, "") for c in cols[1:]]
                _show(view, format_df, column_config)
