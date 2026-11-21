import performance_report as pr

PAGE = (
    "<script>let app = new Vue({data() { return {"
    'storedValueList: [{"createdAt":"2026-11-19 14:29:39","dateClean":"2027-01-21","orderNo":"LC00221042",'
    '"itemName":"年節加價","credit":"","debit":"-450","less":"3,917,350","memo":""},'
    '{"createdAt":"2026-11-19 14:29:39","dateClean":"2027-01-21","orderNo":"LC00221042",'
    '"itemName":"清潔服務費","credit":"","debit":"-3,600","less":"3,917,800","memo":""},'
    '{"createdAt":"2026-11-02 14:07:08","dateClean":"2027-01-31","orderNo":"LC00219325",'
    '"itemName":"清潔服務費返還","credit":"3,600","debit":"","less":"3,944,500","memo":""}],'
    'memberStoredValueList: [{"id":1,"orderNo":"2018-09-13 LC00017658"}],'
    "}}})</script>"
    "<table><tr><td>{{ row.orderNo }}</td></tr></table>"
)


def test_parse_rows_from_vue_json():
    assert pr._parse_stored_value_rows(PAGE) == [
        ("LC00221042", 0, -450),
        ("LC00221042", 0, -3600),
        ("LC00219325", 3600, 0),
    ]


def test_deductions_net_returns():
    class Resp:
        status_code = 200
        url = "u"

        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            pass

    class Session:
        def get(self, url, params=None, **kw):
            return Resp(PAGE if (params or {}).get("page") == "1" else "")

    out = pr._fetch_stored_value_deductions(Session(), "212", page_workers=1)
    assert out == {"LC00221042": 4050, "LC00219325": -3600}
