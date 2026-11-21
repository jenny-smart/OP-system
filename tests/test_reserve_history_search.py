import performance_report as pr


def _page(rows):
    body = "".join(
        f"<tr><td>t</td><td>d</td><td>{no}</td><td>項目</td><td>{inc}</td><td>{ded}</td><td>0</td></tr>"
        for no, inc, ded in rows
    )
    return f"<table><tr><th>預約日期</th><th>服務日期</th><th>訂單編號</th><th>項目</th><th>入帳金額</th><th>扣款金額</th><th>餘額</th></tr>{body}</table>"


class FakeResp:
    def __init__(self, text):
        self.text = text
        self.status_code = 200
        self.url = "u"

    def raise_for_status(self):
        pass


class FakeSession:
    """只有 keyword 參數有效；其他參數被忽略並回整頁（含別張單）。"""

    def get(self, url, params=None, **kw):
        if "keyword" in (params or {}):
            no = params["keyword"]
            return FakeResp(_page([(no, "", "-450"), (no, "", "-3,600")]))
        return FakeResp(_page([("LC00000001", "", "-100"), ("LC00000002", "", "-200")]))


def test_search_finds_working_param_and_sums_deductions():
    amount, param = pr._search_stored_value_by_order(FakeSession(), "1", "LC00221042")
    assert (amount, param) == (4050, "keyword")


def test_fill_by_search_returns_amounts():
    result, param = pr._fill_deductions_by_order_search(FakeSession(), {"1"}, {"LC00221042", "LC00221043"})
    assert result == {"LC00221042": 4050, "LC00221043": 4050}
    assert param == "keyword"


def test_ignored_param_is_not_mistaken_for_result():
    class Ignores:
        def get(self, url, params=None, **kw):
            return FakeResp(_page([("LC00221042", "", "-450"), ("LC00000009", "", "-1")]))

    assert pr._search_stored_value_by_order(Ignores(), "1", "LC00221042") == (None, None)
