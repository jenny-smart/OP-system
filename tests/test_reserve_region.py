from datetime import datetime

import performance_report as report


def test_city_from_address():
    assert report._city_from_address("新竹縣竹北市縣政二十路84號") == "新竹"
    assert report._city_from_address("新北市板橋區三民路二段35巷7號") == "台北"
    assert report._city_from_address("臺北市大安區信義路四段210號4樓") == "台北"
    assert report._city_from_address("台南市東區") == "高雄"
    assert report._city_from_address("桃園市中壢區") == "桃園"
    assert report._city_from_address("") == ""


def test_row_info_reads_service_address():
    html = """<table><tr><td>LC00218347</td>
      <td>代客預訂：EJ<br>新竹代客保留<br>0922278899<br>新竹縣竹北市縣政二十路84號<br>2026-10-29 16:16:34</td>
      <td>2026-12-10 (四) 09:00 - 12:00</td><td>付款狀態：已付款</td></tr></table>"""
    info = report._extract_purchase_row_info(html)["LC00218347"]
    assert info["__address"] == "新竹縣竹北市縣政二十路84號"


def test_same_order_seen_by_two_accounts_is_listed_once_in_address_city():
    ranges = report._reserve_order_month_ranges(datetime(2026, 11, 20))
    base = {"order_no": "LC00218347", "date_clean": "2026-12-10", "name": "新竹代客保留", "person": 2, "hour": 3,
            "__address": "新竹縣竹北市縣政二十路84號", "__pay_status": "已付款"}
    records = [{**base, "__city": "高雄"}, {**base, "__city": "新竹"}]
    df = report.build_reserve_orders_df(records, ranges)
    assert df["訂單編號"].tolist() == ["LC00218347"]
    assert df.iloc[0]["地區"] == "新竹"


def test_city_falls_back_to_fetching_account_when_address_unknown():
    ranges = report._reserve_order_month_ranges(datetime(2026, 11, 20))
    records = [{"__city": "台中", "order_no": "LC1", "date_clean": "2026-11-24", "name": "檸檬保留", "person": 1, "hour": 2}]
    df = report.build_reserve_orders_df(records, ranges)
    assert df.iloc[0]["地區"] == "台中"


def test_order_found_only_in_one_backend_keeps_that_backend_even_if_address_differs():
    ranges = report._reserve_order_month_ranges(datetime(2026, 11, 20))
    records = [{"__city": "桃園", "order_no": "LC00217731", "date_clean": "2026-12-04", "name": "檸檬保留",
                "person": 2, "hour": 3, "__address": "新北市林口區文化三路", "__pay_status": "已付款"}]
    df = report.build_reserve_orders_df(records, ranges)
    assert df.iloc[0]["地區"] == "桃園"  # 在桃園後台查得到，取消與金額都要用桃園帳號
