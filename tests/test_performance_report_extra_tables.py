import pandas as pd
import json
from datetime import datetime

from tools.scheduled_daily import performance_report as report
from tools.scheduled_daily import performance_report_runner as runner


def test_extended_report_crosses_year_and_preserves_existing_reports(tmp_path, monkeypatch):
    output = tmp_path / "extended" / "report.json"
    old_report = tmp_path / "df4.csv"
    old_report.write_bytes(b"legacy")
    monkeypatch.setattr(report, "EXTENDED_REPORT_FILE", output)
    monkeypatch.setattr(report, "LATEST_DIR", str(tmp_path))
    monkeypatch.setattr(report, "now_dt", lambda: datetime(2026, 11, 26, 10, 30))
    ranges = report.get_order_service_month_ranges("2026-11-26")
    raw = pd.DataFrame([
        {"城市": "台北", "月份": "2027/01", "服務": "居家清潔", "收入類型": "現金收入",
         "已付款": 100, "待付款": 50},
        {"城市": "台中", "月份": "2027/01", "服務": "居家清潔", "收入類型": "現金收入",
         "已付款": 50, "待付款": 0},
    ])
    orders = [{"地區": "台北", "付款狀態": "已付款", "月份金額": {"2027/02": 90}}]
    reserves = [{"__city": "台北", "name": "系統保留單", "date_clean": "2027-02-12",
                 "order_no": "reserve-1", "total": 500, "person": 2, "hour": 3}]
    for _ in range(2):
        report.save_extended_report(raw, orders, reserves, ranges, "2026-11-26", "2026-11-26")
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert old_report.read_bytes() == b"legacy"
    assert saved["summary"][0]["次次月加總"] == 150
    assert saved["summary"][0]["次次月佔比"] == 0.75
    assert saved["orders"]["已付款"][0]["2027/02已付款"] == 90
    assert saved["reserve_details"][0]["保留單時數"] == 6
    for period, month in (("次次月", "2027/01"), ("次次次月", "2027/02")):
        rows = saved["history"][period]
        assert [r["統計月份"] for r in rows] == [month, month]
        assert len({r["id"] for r in rows}) == 2

    report.save_extended_report(None, None, [], ranges, None, None)
    reserve_only = json.loads(output.read_text(encoding="utf-8"))
    assert reserve_only["reserve_details"] == []
    assert reserve_only["orders"] == saved["orders"]
    assert reserve_only["history"] == saved["history"]

    report.save_extended_report(raw, None, None, ranges, None, None)
    current_only = json.loads(output.read_text(encoding="utf-8"))
    assert current_only["orders"] == saved["orders"]
    assert current_only["reserve_details"] == []
    assert current_only["history"]["次次月"][1:] == saved["history"]["次次月"]


def test_reserve_amount_uses_net_history_debit_including_zero():
    from types import SimpleNamespace
    from tools.scheduled_daily.reserve_deductions import resolve_deductions

    pages = {
        "1": '[{"orderNo":"LC100000","debit":-600},{"orderNo":"LC200000","debit":-300}]',
        "2": '[{"orderNo":"LC100000","credit":100},{"orderNo":"LC200000","credit":300}]',
    }

    def get(url, *, params, **kwargs):
        return SimpleNamespace(
            text="storedValueList: " + pages.get(params["page"], "[]"),
            raise_for_status=lambda: None,
        )

    items = [
        {"member_id": "42", "order_no": "LC100000"},
        {"member_id": "42", "order_no": "LC200000"},
        {"member_id": "42", "order_no": "LC300000"},
    ]
    amounts = resolve_deductions(SimpleNamespace(get=get), items, "https://example.com/purchase", {})
    assert amounts == {"LC100000": 500, "LC200000": 0}
    assert report._reserve_amount({"total": 999, "__actual_reserve_deduction": amounts["LC200000"]}) == 0
    assert report._reserve_amount({"total": 999}) == 999


def test_configurable_month_ranges_cross_year():
    assert report.get_report_month_ranges("2026-11", "2027-02") == [
        ("2026/11", "2026-11-01", "2026-11-30"),
        ("2026/12", "2026-12-01", "2026-12-31"),
        ("2027/01", "2027-01-01", "2027-01-31"),
        ("2027/02", "2027-02-01", "2027-02-28"),
    ]


def test_order_date_summary_groups_region_and_splits_payment():
    records = [
        {"__city": "台北", "created_at": "2026-08-17 10:00:00", "total": "1,000", "purchase_status": "0"},
        {"__city": "台北", "created_at": "2026-08-18 10:00:00", "total": "2,000", "purchase_status": "1"},
        {"__city": "台中", "created_at": "2026-08-18 11:00:00", "total": 500, "purchase_status": "0"},
        {"__city": "台北", "created_at": "2026-08-18 12:00:00", "total": 999, "purchase_status": "1", "cancel_at": "2026-08-18"},
    ]
    out = report.build_order_date_summary(records)
    assert out.iloc[0].to_dict() == {
        "地區": "台北", "未付款": 1000,
        "已付款": 2000, "未付款＋已付款": 3000,
    }
    assert out.iloc[1]["地區"] == "台中"
    assert out.iloc[-1].to_dict() == {
        "地區": "加總", "未付款": 1500,
        "已付款": 2000, "未付款＋已付款": 3500,
    }


def test_order_date_summaries_split_service_month_and_add_stored_weekend_price():
    ranges = report.get_order_service_month_ranges("2026-12-20", month_count=4)
    assert ranges[-1][0] == "2027/03"
    records = [
        {"__city": "台北", "date_clean": "2026-12-28", "total": 1000, "purchase_status": "0"},
        {"__city": "台北", "date_clean": "2027-01-03", "total": 2000, "purchase_status": "1",
         "stored_value_weekend_price": 200},
        {"__city": "台中", "service": "儲值金", "buy": 5, "total": 3000, "purchase_status": "0"},
    ]
    tables = report.build_order_date_summaries(records, ranges)
    unpaid = tables["待付款"].set_index("地區")
    paid = tables["已付款"].set_index("地區")
    combined = tables["待付款＋已付款"].set_index("地區")

    assert unpaid.loc["台北", "2026/12待付款"] == 1000
    assert unpaid.loc["台中", "儲值金待付款"] == 3000
    assert unpaid.loc["台中", "待付款"] == 0
    assert paid.loc["台北", "2027/01已付款"] == 2200
    assert combined.loc["台北", "待付款＋已付款"] == 3200
    assert not report._purchase_is_stored_value_topup(
        {"buy": 1, "service": "居家清潔", "date_clean": "2026-12-28"}
    )


def test_paid_summary_matches_backend_service_and_stored_value_totals():
    ranges = report.get_order_service_month_ranges("2026-08-24", month_count=4)
    records = [
        {"__city": "台北", "date_clean": "2026-08-10", "total": 31600, "purchase_status": "1"},
        {"__city": "台北", "date_clean": "2026-08-20", "total": 4800,
         "payway": "儲值金", "purchase_status": "1"},
        {"__city": "台北", "date_clean": "2026-09-10", "total": 10800, "purchase_status": "1"},
        {"__city": "台北", "date_clean": "2026-09-20", "total": 21600,
         "payway": "儲值金", "purchase_status": "1"},
        {"__city": "台北", "service": "儲值金", "buy": 5, "total": 50000, "purchase_status": "1"},
    ]
    paid = report.build_order_date_summaries(records, ranges)["已付款"].set_index("地區")

    assert paid.loc["台北", "已付款"] == 68800
    assert paid.loc["台北", "2026/08已付款"] == 36400
    assert paid.loc["台北", "2026/09已付款"] == 32400
    assert paid.loc["台北", "儲值金已付款"] == 50000


def test_report_rows_use_exact_backend_paid_amounts_without_json_tail_digits():
    ranges = report.get_order_service_month_ranges("2026-08-24", month_count=4)
    records = [{
        "地區": "台北",
        "付款狀態": "已付款",
        "總金額": 42400 + 26400,
        "月份金額": {"2026/08": 31600 + 4800, "2026/09": 10800 + 21600},
        "儲值金金額": 50000,
    }]
    paid = report.build_order_date_summaries_from_report_rows(
        records, ranges,
    )["已付款"].set_index("地區")

    assert paid.loc["台北", "已付款"] == 68800
    assert paid.loc["台北", "2026/08已付款"] == 36400
    assert paid.loc["台北", "2026/09已付款"] == 32400
    assert paid.loc["台北", "儲值金已付款"] == 50000


def test_report_amount_split_keeps_stored_value_service_in_paid_total():
    rows = [
        {"收入類型": "現金收入", "服務": "居家清潔", "已付款": 42400},
        {"收入類型": "儲值金", "服務": "居家清潔", "已付款": 26400},
        {"收入類型": "現金收入", "服務": "儲值金", "已付款": 50000},
    ]

    assert report._payment_amount_from_report_rows(rows, 1) == (68800, 50000)


def test_parse_html_adds_weekend_surcharge_only_for_stored_value_table():
    html = """
    <table><tr><th>儲值金</th><th>已付款金額</th><th>待付款金額</th><th>週末加價</th></tr>
    <tr><td>居家清潔</td><td>1,000</td><td>0</td><td>200</td></tr></table>
    <table><tr><th>現金收入</th><th>已付款金額</th><th>待付款金額</th><th>週末加價</th></tr>
    <tr><td>居家清潔</td><td>2,000</td><td>0</td><td>300</td></tr></table>
    """
    rows = report.parse_html(html)
    assert rows[0]["已付款"] == 1200
    assert rows[1]["已付款"] == 2000
    scheduled_rows = runner.parse_html(html)
    assert scheduled_rows[0]["已付款"] == 1200
    assert scheduled_rows[1]["已付款"] == 2000
    assert report.parse_html(html, payment_status=0)[0]["待付款"] == 200
    assert runner.parse_html(html, payment_status=0)[0]["待付款"] == 200


def test_reserve_hours_and_net_performance():
    month_ranges = [
        ("2026/08", "2026-08-01", "2026-08-31"),
        ("2026/09", "2026-09-01", "2026-09-30"),
    ]
    reserve_records = [
        {
            "__city": "台北", "order_no": "LC1", "date_clean": "2026-08-20",
            "name": "檸檬保留", "person": 2, "hour": 4, "total": 4800,
        },
        {
            "__city": "台中", "order_no": "LC2", "date_clean": "2026-08-21",
            "notice": "大掃除檸檬保留單 reserve_1", "person": 2,
            "period_s": "09:00", "period_e": "12:00", "total": 3600,
        },
    ]
    reserve_df = report.build_reserve_summary(reserve_records, month_ranges)
    taipei = reserve_df[reserve_df["地區"] == "台北"].iloc[0]
    taichung = reserve_df[reserve_df["地區"] == "台中"].iloc[0]
    assert taipei["2026/08保留單時數"] == 8
    assert taipei["2026/08保留單業績"] == 4800
    assert taichung["2026/08保留單時數"] == 6

    raw_df = pd.DataFrame([
        {"城市": "台北", "月份": "2026/08", "收入類型": "現金收入", "服務": "居家清潔", "已付款": 8000, "待付款": 2000},
        {"城市": "台中", "月份": "2026/08", "收入類型": "現金收入", "服務": "居家清潔", "已付款": 8000, "待付款": 0},
    ])
    net_df = report.build_net_performance_summary(raw_df, reserve_df, month_ranges)
    performance_df = report.build_month_performance_summary(raw_df, month_ranges)
    taipei_net = net_df[net_df["地區"] == "台北"].iloc[0]
    taichung_net = net_df[net_df["地區"] == "台中"].iloc[0]
    assert taipei_net["2026/08業績－保留單業績"] == 5200
    assert taichung_net["2026/08業績－保留單業績"] == 4400
    assert performance_df[performance_df["地區"] == "加總"].iloc[0]["2026/08業績"] == 18000

    for city in [*report.CITY_ORDER, "加總"]:
        gross = performance_df[performance_df["地區"] == city].iloc[0]["2026/08業績"]
        reserve = reserve_df[reserve_df["地區"] == city].iloc[0]["2026/08保留單業績"]
        net = net_df[net_df["地區"] == city].iloc[0]["2026/08業績－保留單業績"]
        assert gross - reserve == net
