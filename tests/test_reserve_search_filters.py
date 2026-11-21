import performance_report as report


class FakeResponse:
    text = "purchaseList: {\"data\": []}"

    def raise_for_status(self):
        pass


class CaptureSession:
    def __init__(self):
        self.params = []

    def get(self, url, params=None, **kwargs):
        self.params.append(dict(params))
        return FakeResponse()


def test_search_filters_are_sent_to_backend():
    session = CaptureSession()
    report._fetch_purchase_items(
        session, name="保留", purchase_status="1", clean_date_s="2026-11-22", clean_date_e="2026-11-30",
    )
    sent = session.params[0]
    assert sent["name"] == "保留" and sent["purchase_status"] == "1"
    assert sent["clean_date_s"] == "2026-11-22" and sent["clean_date_e"] == "2026-11-30"
