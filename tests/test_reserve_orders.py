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
    assert first["總人時"] == 8 and first["專員人數"] == 2 and first["訂單金額"] == 4800
    second = df.iloc[1]
    assert (second["月份別"], second["星期"]) == ("次月", "週六")
    assert second["總人時"] == 9 and second["專員人數"] == 3


def test_build_reserve_orders_staff_names_amount_and_paid_only():
    ranges = report._reserve_order_month_ranges(datetime(2026, 11, 20))
    records = [
        {"__city": "台北", "order_no": "P1", "date_clean": "2026-11-24", "name": "檸檬保留", "person": 2, "hour": 3,
         "__staff": ["黃柏維", "李佩蓉"], "__pay_status": "已付款", "__amount": 0, "__period": "09:00-12:00"},
        {"__city": "台北", "order_no": "P2", "date_clean": "2026-11-25", "name": "檸檬保留", "person": 1, "hour": 3,
         "__staff": ["王小明"], "__pay_status": "待付款", "__amount": 100, "__period": "09:00-12:00"},
    ]
    df = report.build_reserve_orders_df(records, ranges)
    assert df["訂單編號"].tolist() == ["P1"]
    row = df.iloc[0]
    assert row["專員姓名"] == "黃柏維 X 李佩蓉"
    assert row["服務時段"] == "09:00-12:00" and row["付款狀態"] == "已付款" and row["訂單金額"] == 0


def test_extract_purchase_row_info_reads_staff_status_amount_period():
    html = """
    <table><tr>
      <td>LC00219433</td><td>代客預訂：淑慧 檸檬保留</td>
      <td>2027-02-24 (三)<br>09:00 - 12:00
        <a href="/schedule/edit?id=1"><span>黃柏維</span>(2)</a> X
        <a href="/schedule/edit?id=2"><span>李佩蓉</span>(4)</a></td>
      <td>總金額：0<br>付款狀態：已付款</td><td>已處理</td><td>取消訂單</td>
    </tr></table>
    """
    info = report._extract_purchase_row_info(html)["LC00219433"]
    assert info["__staff"] == ["黃柏維", "李佩蓉"]
    assert info["__pay_status"] == "已付款" and info["__amount"] == 0
    assert info["__period"] == "09:00-12:00"


def test_build_reserve_orders_empty():
    df = report.build_reserve_orders_df([], report._reserve_order_month_ranges(datetime(2026, 11, 20)))
    assert df.empty and list(df.columns) == report.RESERVE_ORDER_COLS


def test_fetch_purchase_items_parallel_pages_match_sequential():
    import json

    class FakeResponse:
        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            pass

    class FakeSession:
        def __init__(self):
            self.pages = []

        def get(self, url, params=None, **kwargs):
            page = int(params["page"])
            self.pages.append(page)
            counts = {1: 20, 2: 20, 3: 5}
            n = counts.get(page, 0)
            data = [{"order_no": f"LC{page:02d}{i:04d}"} for i in range(n)]
            return FakeResponse("purchaseList: " + json.dumps({"data": data}))

    sequential = report._fetch_purchase_items(FakeSession())
    parallel_session = FakeSession()
    parallel = report._fetch_purchase_items(parallel_session, page_workers=3)
    assert len(sequential) == len(parallel) == 45
    assert {i["order_no"] for i in sequential} == {i["order_no"] for i in parallel}
    assert sorted(parallel_session.pages) == [1, 2, 3]
