import reserve_cancel as rc


def test_classify_memo_follows_orders_system_safety_rules():
    assert rc.classify_memo("") == "ok"
    assert rc.classify_memo("  ") == "ok"
    assert rc.classify_memo("系統保留單") == "ok"
    assert rc.classify_memo("取消系統保留單") == "cancelled"
    assert rc.classify_memo("代客保留：王小明") == "other"


def test_purchase_id_from_order_no():
    assert rc.purchase_id_from_order_no("LC00219433") == "219433"
    assert rc.purchase_id_from_order_no("") == ""
