# -*- coding: utf-8 -*-
"""保留單明細的統計表（純 pandas，方便測試）。

訂單金額依「服務日期」分階段：
- 大掃除第一階段：2026-12-15 ～ 2027-01-21
- 大掃除第二階段：2027-01-22 ～ 2027-02-04
- 其他日期：非大掃除
"""

import pandas as pd

CITY_ORDER = ["台北", "台中", "桃園", "新竹", "高雄"]
OTHER_STAGE = "非大掃除"
RESERVE_STAGES = [
    ("大掃除第一階段", "2026-12-15", "2027-01-21"),
    ("大掃除第二階段", "2027-01-22", "2027-02-04"),
]
STAGE_NAMES = [OTHER_STAGE] + [name for name, _, _ in RESERVE_STAGES]
NUMERIC_COLS = ("總人時", "專員人數", "訂單金額")


def reserve_stage(date_text) -> str:
    text = str(date_text or "")[:10]
    for name, start, end in RESERVE_STAGES:
        if start <= text <= end:
            return name
    return OTHER_STAGE


def prepare_reserve_df(df: pd.DataFrame) -> pd.DataFrame:
    """數字欄轉成數字，並加上「階段」欄。"""
    out = df.copy()
    for col in NUMERIC_COLS:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0)
    out["階段"] = out["日期"].map(reserve_stage) if "日期" in out.columns else OTHER_STAGE
    return out


def _sort_regions(frame: pd.DataFrame) -> pd.DataFrame:
    rank = frame["地區"].map(lambda c: CITY_ORDER.index(c) if c in CITY_ORDER else len(CITY_ORDER))
    return frame.assign(__o=rank).sort_values("__o").drop(columns="__o")


def region_summary(sub: pd.DataFrame) -> pd.DataFrame:
    """依地區統計：保留單數、總人時、專員人次、訂單金額，以及各階段金額；最後一列合計。"""
    columns = ["地區", "保留單數", "總人時", "專員人次", "訂單金額"] + [f"{s}金額" for s in STAGE_NAMES]
    if sub.empty:
        return pd.DataFrame(columns=columns)
    base = (
        sub.groupby("地區")
        .agg(保留單數=("訂單編號", "count"), 總人時=("總人時", "sum"),
             專員人次=("專員人數", "sum"), 訂單金額=("訂單金額", "sum"))
        .reset_index()
    )
    stage = sub.pivot_table(index="地區", columns="階段", values="訂單金額", aggfunc="sum", fill_value=0)
    stage = stage.reindex(columns=STAGE_NAMES, fill_value=0)
    stage.columns = [f"{s}金額" for s in STAGE_NAMES]
    result = _sort_regions(base.merge(stage.reset_index(), on="地區", how="left").fillna(0))
    total = {"地區": "合計"}
    for col in columns[1:]:
        total[col] = result[col].sum()
    return pd.concat([result[columns], pd.DataFrame([total])], ignore_index=True)


def region_month_matrix(df: pd.DataFrame, month_names) -> pd.DataFrame:
    """每一列是地區，每個月份兩欄：保留時數（總人時）、保留單金額；最後一列合計。"""
    if df.empty:
        return pd.DataFrame()
    regions = _sort_regions(pd.DataFrame({"地區": df["地區"].dropna().unique()}))["地區"].tolist()
    rows = []
    for region in regions + ["合計"]:
        scoped = df if region == "合計" else df[df["地區"] == region]
        row = {"地區": region}
        for month in month_names:
            part = scoped[scoped["月份別"] == month]
            row[f"{month}保留時數"] = part["總人時"].sum() if "總人時" in part.columns else 0
            row[f"{month}保留單金額"] = part["訂單金額"].sum()
        rows.append(row)
    return pd.DataFrame(rows)


def region_stage_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """每一列是地區，各階段的保留單數與金額（階段依服務日期，跨月份合計）。"""
    if df.empty:
        return pd.DataFrame()
    regions = _sort_regions(pd.DataFrame({"地區": df["地區"].dropna().unique()}))["地區"].tolist()
    rows = []
    for region in regions + ["合計"]:
        scoped = df if region == "合計" else df[df["地區"] == region]
        row = {"地區": region}
        for stage in STAGE_NAMES:
            part = scoped[scoped["階段"] == stage]
            row[f"{stage}單數"] = int(len(part))
            row[f"{stage}金額"] = part["訂單金額"].sum()
        rows.append(row)
    return pd.DataFrame(rows)
