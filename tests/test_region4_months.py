import pandas as pd

import performance_report as report


def _raw_row(city, month, service, paid, unpaid, income="現金收入"):
    return {
        "城市": city, "月份": month, "日期": "", "收入類型": income, "資料來源": "主表",
        "服務": service, "子項目": "", "已付款": paid, "待付款": unpaid,
    }


def test_region4_has_five_months_appliance_and_stored_value():
    raw_df = pd.DataFrame([
        _raw_row("台北", "本月", "居家清潔", 1000, 500),
        _raw_row("台北", "下月", "居家清潔", 0, 2000),
        _raw_row("台北", "次次月", "居家清潔", 300, 0),
        _raw_row("台北", "次次次月", "居家清潔", 0, 400),
        _raw_row("台北", "次次次次月", "居家清潔", 50, 50),
        _raw_row("桃園", "本月", "洗衣機清潔", 4000, 1000),
        _raw_row("桃園", "次次次次月", "冷氣清潔", 0, 700),
    ])
    region4 = report.build_region4_df(report.build_region2_df(raw_df))

    assert list(region4.columns) == [
        "城市",
        "本月加總", "本月佔比", "次月加總", "次月佔比",
        "次次月加總", "次次月佔比", "次次次月加總", "次次次月佔比",
        "次次次次月加總", "次次次次月佔比",
        "本月家電加總", "次月家電加總", "次次月家電加總", "次次次月家電加總", "次次次次月家電加總",
        "儲值金",
    ]
    assert region4["城市"].tolist() == [*report.CITY_ORDER, "加總"]

    taipei = region4[region4["城市"] == "台北"].iloc[0]
    assert taipei["本月加總"] == 1500
    assert taipei["次月加總"] == 2000
    assert taipei["次次月加總"] == 300
    assert taipei["次次次月加總"] == 400
    assert taipei["次次次次月加總"] == 100

    taoyuan = region4[region4["城市"] == "桃園"].iloc[0]
    # 家電只放在各月份「家電加總」，不進各月份加總。
    assert taoyuan["本月加總"] == 0
    assert taoyuan["本月家電加總"] == 5000
    assert taoyuan["次次次次月家電加總"] == 700

    total = region4[region4["城市"] == "加總"].iloc[0]
    assert total["次次次次月加總"] == 100
    assert total["次次次次月佔比"] == 1
    assert total["本月家電加總"] == 5000
    assert taipei["次次次次月佔比"] == 1  # 該月只有台北有清潔業績


def test_region4_display_formats_all_integer_columns():
    raw_df = pd.DataFrame([_raw_row("台北", "次次次月", "居家清潔", 10, 5)])
    region4 = report.build_region4_df(report.build_region2_df(raw_df))
    shown = report.format_region4_for_display(region4)
    for col in report.REGION4_INT_COLS:
        assert shown[col].map(type).eq(int).all()
    html = report.build_region4_email_html(region4)
    assert "次次次次月加總" in html and "15" in html
