import pandas as pd

import reserve_views as rv


def sample_df():
    rows = [
        {"月份別": "當月", "地區": "台北", "訂單編號": "A", "日期": "2026-11-24", "總人時": 6, "專員人數": 2, "訂單金額": 100},
        {"月份別": "次月", "地區": "台北", "訂單編號": "B", "日期": "2026-12-20", "總人時": 6, "專員人數": 2, "訂單金額": 4275},
        {"月份別": "次次月", "地區": "新竹", "訂單編號": "C", "日期": "2027-01-25", "總人時": 4, "專員人數": 2, "訂單金額": 4500},
        {"月份別": "次次月", "地區": "新竹", "訂單編號": "D", "日期": "2027-01-21", "總人時": 4, "專員人數": 2, "訂單金額": 900},
    ]
    return rv.prepare_reserve_df(pd.DataFrame(rows))


def test_reserve_stage_boundaries():
    assert rv.reserve_stage("2026-12-14") == "非大掃除"
    assert rv.reserve_stage("2026-12-15") == "大掃除第一階段"
    assert rv.reserve_stage("2027-01-21") == "大掃除第一階段"
    assert rv.reserve_stage("2027-01-22") == "大掃除第二階段"
    assert rv.reserve_stage("2027-02-04") == "大掃除第二階段"
    assert rv.reserve_stage("2027-02-05") == "非大掃除"


def test_region_summary_splits_amount_by_stage_with_total_row():
    summary = rv.region_summary(sample_df())
    taipei = summary[summary["地區"] == "台北"].iloc[0]
    assert taipei["訂單金額"] == 4375
    assert taipei["非大掃除金額"] == 100 and taipei["大掃除第一階段金額"] == 4275
    hsinchu = summary[summary["地區"] == "新竹"].iloc[0]
    assert hsinchu["大掃除第一階段金額"] == 900 and hsinchu["大掃除第二階段金額"] == 4500
    total = summary.iloc[-1]
    assert total["地區"] == "合計" and total["訂單金額"] == 4375 + 5400 and total["保留單數"] == 4
    assert summary["地區"].tolist() == ["台北", "新竹", "合計"]


def test_region_month_matrix_per_region_per_month():
    matrix = rv.region_month_matrix(sample_df(), ["當月", "次月", "次次月"])
    taipei = matrix[matrix["地區"] == "台北"].iloc[0]
    assert taipei["當月金額"] == 100 and taipei["次月金額"] == 4275 and taipei["次次月單數"] == 0
    assert matrix.iloc[-1]["次次月金額"] == 5400


def test_region_stage_matrix_and_empty():
    stage = rv.region_stage_matrix(sample_df())
    hsinchu = stage[stage["地區"] == "新竹"].iloc[0]
    assert hsinchu["大掃除第二階段金額"] == 4500 and hsinchu["大掃除第一階段單數"] == 1
    assert rv.region_summary(rv.prepare_reserve_df(pd.DataFrame(columns=["日期"]))).empty
