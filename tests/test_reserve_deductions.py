from datetime import datetime

import performance_report as report

STORED_VALUE_HTML = """
<table>
<tr><td>2026-11-19 14:29:40</td><td>2027-01-23</td><td>LC00221045</td><td>年節加價</td><td></td><td>-900</td><td>3,904,075</td><td></td></tr>
<tr><td>2026-11-19 14:29:40</td><td>2027-01-23</td><td>LC00221045</td><td>清潔服務費</td><td></td><td>-3,600</td><td>3,904,975</td><td></td></tr>
<tr><td>2026-11-19 07:15:07</td><td>2027-01-31</td><td>LC00221022</td><td>清潔服務費返還</td><td>19,200</td><td></td><td>3,959,950</td><td></td></tr>
<tr><td>2026-11-01 00:00:00</td><td>2027-01-31</td><td>LC00221022</td><td>清潔服務費</td><td></td><td>-19,200</td><td>3,940,750</td><td></td></tr>
</table>
"""


class FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


def test_parse_stored_value_rows():
    rows = report._parse_stored_value_rows(STORED_VALUE_HTML)
    assert rows[0] == ("LC00221045", 0, -900)
    assert rows[1] == ("LC00221045", 0, -3600)
    assert rows[2] == ("LC00221022", 19200, 0)


def test_fetch_stored_value_deductions_nets_returns_and_stops_on_empty_page():
    class FakeSession:
        def get(self, url, params=None, **kwargs):
            return FakeResponse(STORED_VALUE_HTML if params["page"] == "1" else "<table></table>")

    result = report._fetch_stored_value_deductions(FakeSession(), "212", page_workers=2)
    assert result == {"LC00221045": 4500, "LC00221022": 0}


def test_fetch_stored_value_deductions_stops_when_page_param_is_ignored():
    class FakeSession:
        calls = 0

        def get(self, url, params=None, **kwargs):
            FakeSession.calls += 1
            return FakeResponse(STORED_VALUE_HTML)  # 每一頁都回一樣的內容

    result = report._fetch_stored_value_deductions(FakeSession(), "212", page_workers=3, max_pages=30)
    assert result["LC00221045"] == 4500  # 重複頁不重複計算
    assert FakeSession.calls <= 3


def test_build_reserve_orders_uses_stored_value_deduction_as_amount():
    ranges = report._reserve_order_month_ranges(datetime(2026, 11, 20))
    records = [{"__city": "台北", "order_no": "LC00221045", "date_clean": "2026-11-24", "name": "檸檬保留",
                "person": 2, "hour": 3, "__amount": 0, "__pay_status": "已付款"}]
    df = report.build_reserve_orders_df(records, ranges, {"LC00221045": 4500})
    assert df.iloc[0]["訂單金額"] == 4500


def test_member_id_comes_from_json_or_row_link():
    assert report._purchase_member_id({"member_id": 212}) == "212"
    assert report._purchase_member_id({"__member_id": "318"}) == "318"
    assert report._purchase_member_id({}) == ""
    html = """<table><tr><td>LC00219433</td>
      <td><a href="https://backend.lemonclean.com.tw/member/edit/318">檸檬保留</a></td>
      <td>2027-02-24 09:00 - 12:00</td><td>付款狀態：已付款</td></tr></table>"""
    assert report._extract_purchase_row_info(html)["LC00219433"]["__member_id"] == "318"
