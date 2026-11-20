from datetime import datetime

import performance_report as report


def test_reserve_order_month_ranges_cross_year():
    ranges = report._reserve_order_month_ranges(datetime(2026, 11, 20))
    assert [(n, label) for n, label, _, _ in ranges] == [
        ("當月", "2026/11"), ("次月", "2026/12"), ("次次月", "2027/01"), ("次次次月", "2027/02"),
    ]
    assert ranges[0][2:] == ("2026-11-01", "2026-11-30")
    assert ranges[3][2:] == ("2027-02-01", "2027-02-28")


def test_build_reserve_orders_lists_date_weekday_hours_and_people():
    ranges = report._reserve_order_month_ranges(datetime(2026, 11, 20))
    records = [
        # 2026-11-21 是週六；2 人 × 4 小時
        {"__city": "台北", "order_no": "A1", "date_clean": "2026-11-21", "name": "檸檬保留", "person": 2, "hour": 4, "total": 4800},
        # 次月，用起訖時間算每人 3 小時；3 人
        {"__city": "台中", "order_no": "A2", "date_clean": "2026-12-05", "notice": "大掃除檸檬保留單", "person": 3, "period_s": "09:00", "period_e": "12:00"},
        # 已取消、一般訂單、超出範圍的都不列
        {"__city": "台北", "order_no": "X1", "date_clean": "2026-11-22", "name": "檸檬保留", "person": 1, "hour": 2, "cancel_at": "2026-11-01"},
        {"__city": "台北", "order_no": "X2", "date_clean": "2026-11-23", "name": "王小明", "person": 1, "hour": 2},
        {"__city": "台北", "order_no": "X3", "date_clean": "2027-03-01", "name": "檸檬保留", "person": 1, "hour": 2},
    ]
    df = report.build_reserve_orders_df(records, ranges)

    assert list(df.columns) == report.RESERVE_ORDER_COLS
    assert df["訂單編號"].tolist() == ["A1", "A2"]
    first = df.iloc[0]
    assert (first["月份別"], first["地區"], first["日期"], first["星期"]) == ("當月", "台北", "2026-11-21", "週六")
    assert first["時數"] == 8 and first["專員"] == 2
    second = df.iloc[1]
    assert (second["月份別"], second["星期"]) == ("次月", "週六")
    assert second["時數"] == 9 and second["專員"] == 3


def test_build_reserve_orders_empty():
    df = report.build_reserve_orders_df([], report._reserve_order_month_ranges(datetime(2026, 11, 20)))
    assert df.empty and list(df.columns) == report.RESERVE_ORDER_COLS
