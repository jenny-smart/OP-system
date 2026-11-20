import performance_report as report


class FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


class FakeSession:
    def __init__(self, pages):
        self.pages = pages
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        for key, text in self.pages.items():
            if url.endswith(key):
                return FakeResponse(text)
        return FakeResponse("", 404)


def reserve_item(order_no, name="檸檬保留", **extra):
    return {"order_no": order_no, "name": name, **extra}


def test_member_id_taken_from_items_without_extra_requests():
    session = FakeSession({})
    ids, messages = report._discover_member_ids(session, [reserve_item("LC00219328", member_id=212)])
    assert ids == {"212"} and messages == [] and session.urls == []


def test_member_id_discovered_from_edit_page_once_per_customer():
    session = FakeSession({"/edit/219328": 'x purchase: {"id":1,"member_id":212,"memo":""}'})
    items = [reserve_item("LC00219328"), reserve_item("LC00219329")]
    ids, messages = report._discover_member_ids(session, items)
    assert ids == {"212"} and messages == []
    assert len(session.urls) == 1  # 同一位顧客只抽一張訂單去查


def test_missing_member_id_reports_reason_and_fields():
    session = FakeSession({})
    ids, messages = report._discover_member_ids(session, [reserve_item("LC00219328", other_field=1)])
    assert ids == set()
    assert any("找不到會員編號" in m for m in messages)
    assert any("other_field" in m for m in messages)
