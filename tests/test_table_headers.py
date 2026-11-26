import pandas as pd

import table_headers as th


def test_month_labels_cross_year():
    labels = th.month_labels("2026-11-26 19:04:29")
    assert labels["本月"] == "2026/11" and labels["次次月"] == "2027/01" and labels["次次次月"] == "2027/02"


def test_split_month_header():
    labels = th.month_labels("2026-11-26")
    assert th.split_month_header("次次月加總", labels) == ("2027/01", "加總")
    assert th.split_month_header("本月家電加總", labels) == ("2026/11", "家電加總")
    assert th.split_month_header("2026/12待付款＋已付款") == ("2026/12", "待付款＋已付款")
    assert th.split_month_header("儲值金待付款") is None
    assert th.split_month_header("城市", labels) is None


def test_render_double_header_merges_same_month():
    df = pd.DataFrame([{"城市": "台北", "本月加總": 1000, "本月佔比": 0.5, "次月加總": 2, "次月佔比": 0.1, "儲值金": 3}])
    out = th.render_html_table(df, labels=th.month_labels("2026-11-01"))
    assert 'rowspan="2">城市' in out and 'colspan="2">2026/11' in out and 'colspan="2">2026/12' in out
    assert ">加總<" in out and ">佔比<" in out and "50.00%" in out and "1,000" in out
