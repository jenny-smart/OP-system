import pandas as pd

import performance_report as report


def test_configurable_month_ranges_cross_year():
    assert report.get_report_month_ranges("2026-11", "2027-02") == [
        ("2026/11", "2026-11-01", "2026-11-30"),
        ("2026/12", "2026-12-01", "2026-12-31"),
        ("2027/01", "2027-01-01", "2027-01-31"),
        ("2027/02", "2027-02-01", "2027-02-28"),
    ]


def test_order_date_summary_groups_by_city_and_totals():
    # raw_df 跟 build_month_performance_summary 共用同一種形狀（來自財務彙總表的
    # parse_html() 結果，沒有服務日期——那張表是依服務分類彙總，不是逐筆訂單）。
    raw_df = pd.DataFrame([
        {"城市": "台北", "收入類型": "現金收入", "服務": "居家清潔", "已付款": 2000, "待付款": 1000},
        {"城市": "台中", "收入類型": "現金收入", "服務": "居家清潔", "已付款": 0, "待付款": 500},
    ])
    out = report.build_order_date_summary(raw_df)
    assert out.iloc[0].to_dict() == {
        "地區": "台北", "待付款": 1000, "已付款": 2000, "待付款＋已付款": 3000,
        "儲值金待付款": 0, "儲值金已付款": 0, "儲值金待付款＋已付款": 0,
    }
    total_row = out[out["地區"] == "加總"].iloc[0]
    assert total_row.to_dict() == {
        "地區": "加總", "待付款": 1500, "已付款": 2000, "待付款＋已付款": 3500,
        "儲值金待付款": 0, "儲值金已付款": 0, "儲值金待付款＋已付款": 0,
    }
    # 每個地區都要有自己的一列，不能像舊版那樣另外多一列「儲值金」。
    assert out["地區"].tolist() == [*report.CITY_ORDER, "加總"]
    # 沒有傳 order_rows，就不會有月份欄位，不會出錯。
    assert list(out.columns) == [
        "地區", "待付款", "已付款", "待付款＋已付款",
        "儲值金待付款", "儲值金已付款", "儲值金待付款＋已付款",
    ]


def test_order_date_summary_excludes_appliances_water_wash_and_storage():
    raw_df = pd.DataFrame([
        # 桃園清潔現金與清潔儲值金都應保留。
        {"城市": "桃園", "收入類型": "現金收入", "服務": "居家清潔", "已付款": 4800, "待付款": 5700},
        {"城市": "桃園", "收入類型": "儲值金", "服務": "居家清潔", "已付款": 7200, "待付款": 0},
        # 家電、水洗與收納不得出現在上方三張付款彙總表。
        {"城市": "桃園", "收入類型": "現金收入", "服務": "洗衣機清潔", "已付款": 4000, "待付款": 7500},
        {"城市": "桃園", "收入類型": "現金收入", "服務": "水洗", "已付款": 3000, "待付款": 2000},
        {"城市": "桃園", "收入類型": "現金收入", "服務": "收納", "已付款": 1000, "待付款": 900},
    ])

    out = report.build_order_date_summary(raw_df)
    taoyuan = out[out["地區"] == "桃園"].iloc[0]

    assert taoyuan["待付款"] == 5700
    assert taoyuan["已付款"] == 12000
    assert taoyuan["待付款＋已付款"] == 17700


def test_order_date_summary_splits_service_date_into_dynamic_month_columns():
    # 財務彙總表（raw_df）只給總額；服務日期來自逐筆訂單資料（order_rows，見
    # generate_order_date_report() 如何用 _fetch_purchase_items() 組出這份清單），
    # 台北訂單的服務日期橫跨 8 月跟 9 月，待付款/已付款
    # 底下應該動態拆成兩組月份欄位，按時間排序，但地區/加總的總額仍然以 raw_df 為準。
    raw_df = pd.DataFrame([
        {"城市": "台北", "收入類型": "現金收入", "服務": "居家清潔", "已付款": 3000, "待付款": 1500},
    ])
    order_rows = [
        {"城市": "台北", "日期": "2026-08-20", "已付款": 3000, "待付款": 0, "是否儲值金": False},
        {"城市": "台北", "日期": "2026-09-05", "已付款": 0, "待付款": 1500, "是否儲值金": False},
    ]
    out = report.build_order_date_summary(raw_df, order_rows=order_rows)
    assert list(out.columns) == [
        "地區", "待付款", "已付款", "待付款＋已付款",
        "2026/08待付款", "2026/08已付款", "2026/09待付款", "2026/09已付款",
        "儲值金待付款", "儲值金已付款", "儲值金待付款＋已付款",
    ]
    taipei_row = out[out["地區"] == "台北"].iloc[0]
    assert taipei_row["待付款"] == 1500
    assert taipei_row["已付款"] == 3000
    assert taipei_row["2026/08待付款"] == 0
    assert taipei_row["2026/08已付款"] == 3000
    assert taipei_row["2026/09待付款"] == 1500
    assert taipei_row["2026/09已付款"] == 0


def test_order_date_summary_splits_out_stored_value_by_city():
    raw_df = pd.DataFrame([
        # 一般清潔訂單，即使是用儲值金付款，服務分類仍然是「清潔」，要留在待付款/已付款。
        {"城市": "台北", "收入類型": "現金收入", "服務": "居家清潔", "已付款": 2800, "待付款": 0},
        {"城市": "台中", "收入類型": "現金收入", "服務": "居家清潔", "已付款": 0, "待付款": 500},
        # 儲值金儲值單：raw_df 裡的「服務」欄位已經是 parse_html() 正規化過的值
        # （原始表頭「VIP」會被 normalize_service 轉成「儲值金」），收入類型是「現金收入」
        # ——跟「目前總表」判斷儲值金的邏輯完全相同。同一個地區（台北）同時有清潔訂單
        # 跟儲值金訂單，確認兩者不會互相汙染。
        {"城市": "台北", "收入類型": "現金收入", "服務": "儲值金", "已付款": 0, "待付款": 50000},
        {"城市": "桃園", "收入類型": "現金收入", "服務": "儲值金", "已付款": 30000, "待付款": 0},
    ])
    out = report.build_order_date_summary(raw_df)

    taipei_row = out[out["地區"] == "台北"].iloc[0]
    assert taipei_row["待付款"] == 0
    assert taipei_row["已付款"] == 2800
    assert taipei_row["待付款＋已付款"] == 2800
    assert taipei_row["儲值金待付款"] == 50000
    assert taipei_row["儲值金已付款"] == 0
    assert taipei_row["儲值金待付款＋已付款"] == 50000

    taoyuan_row = out[out["地區"] == "桃園"].iloc[0]
    assert taoyuan_row["待付款"] == 0
    assert taoyuan_row["已付款"] == 0
    assert taoyuan_row["儲值金待付款"] == 0
    assert taoyuan_row["儲值金已付款"] == 30000
    assert taoyuan_row["儲值金待付款＋已付款"] == 30000

    total_row = out[out["地區"] == "加總"].iloc[0]
    assert total_row["待付款"] == 500
    assert total_row["已付款"] == 2800
    assert total_row["儲值金待付款"] == 50000
    assert total_row["儲值金已付款"] == 30000
    assert total_row["儲值金待付款＋已付款"] == 80000


def test_purchase_is_stored_value_topup_detects_order_title():
    # 儲值金儲值單的「訂購資訊」欄位文字（例如「儲值金-台北(儲值金50,000贈購物金
    # 2,500)」）在 purchaseList JSON 的某個欄位裡也會出現同樣的文字，整筆記錄用
    # json 字串搜尋比對，跟 _purchase_is_reserve() 判斷保留單的作法一致。
    topup_item = {"order_no": "LC1", "name": "儲值金-台北(儲值金50,000贈購物金2,500)"}
    cleaning_item = {"order_no": "LC2", "name": "代客預訂：Jenny 居家清潔"}
    assert report._purchase_is_stored_value_topup(topup_item) is True
    assert report._purchase_is_stored_value_topup(cleaning_item) is False


def test_month_performance_minus_reserve_equals_net():
    month_ranges = [("2026/08", "2026-08-01", "2026-08-31")]
    raw_df = pd.DataFrame([
        {"城市": "台北", "月份": "2026/08", "收入類型": "現金收入", "服務": "居家清潔", "已付款": 8000, "待付款": 2000},
        {"城市": "台中", "月份": "2026/08", "收入類型": "現金收入", "服務": "居家清潔", "已付款": 8000, "待付款": 0},
    ])
    reserve_records = [
        {"__city": "台北", "order_no": "LC1", "date_clean": "2026-08-20", "name": "檸檬保留", "person": 2, "hour": 4, "total": 4800},
        {"__city": "台中", "order_no": "LC2", "date_clean": "2026-08-21", "notice": "大掃除檸檬保留單", "person": 2, "period_s": "09:00", "period_e": "12:00", "total": 3600},
    ]
    performance_df = report.build_month_performance_summary(raw_df, month_ranges)
    reserve_df = report.build_reserve_summary(reserve_records, month_ranges)
    net_df = report.build_net_performance_summary(raw_df, reserve_df, month_ranges)

    assert reserve_df[reserve_df["地區"] == "台北"].iloc[0]["2026/08保留單時數"] == 8
    assert reserve_df[reserve_df["地區"] == "台中"].iloc[0]["2026/08保留單時數"] == 6

    for city in [*report.CITY_ORDER, "加總"]:
        gross = performance_df[performance_df["地區"] == city].iloc[0]["2026/08業績"]
        reserve = reserve_df[reserve_df["地區"] == city].iloc[0]["2026/08保留單業績"]
        net = net_df[net_df["地區"] == city].iloc[0]["2026/08業績－保留單業績"]
        assert gross - reserve == net
