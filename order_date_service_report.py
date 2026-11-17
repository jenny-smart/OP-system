import os

import pandas as pd
import requests

import performance_report as report


def _build_summary(rows, month_ranges):
    months = [label for label, _, _ in month_ranges]
    cols = ["地區"]
    for month in months:
        for service in ("家電", "水洗"):
            cols.extend([
                f"{month}{service}待付款",
                f"{month}{service}已付款",
            ])

    work = pd.DataFrame(rows)
    result = []
    for city in report.CITY_ORDER:
        row = {"地區": city}
        for month in months:
            for service in ("家電", "水洗"):
                if work.empty:
                    sub = pd.DataFrame()
                else:
                    sub = work[
                        (work["城市"] == city)
                        & (work["月份"] == month)
                        & (work["服務類別"] == service)
                    ]
                row[f"{month}{service}待付款"] = 0 if sub.empty else sub["待付款"].sum()
                row[f"{month}{service}已付款"] = 0 if sub.empty else sub["已付款"].sum()
        result.append(row)

    out = pd.DataFrame(result, columns=cols)
    out = pd.concat([
        out,
        pd.DataFrame([{
            "地區": "加總",
            **{col: out[col].sum() for col in cols[1:]},
        }]),
    ], ignore_index=True)
    return out[cols]


def generate_service_payment_report(order_start_date: str, order_end_date: str, trigger="dashboard"):
    """依訂購日期統計家電/水洗，本月起 5 個月（本月～次次次次月）的待付款及已付款。"""
    if order_end_date < order_start_date:
        raise ValueError("訂購日期迄日不可早於起日")

    month_ranges = report._order_date_month_ranges()

    def worker(city):
        session = requests.Session()
        account = report.ACCOUNTS[city]
        report.login(session, account["email"], account["password"])
        totals = {}

        for status in [1, 0]:
            for keyword in report.get_keywords(city):
                for idx, (label, month_start, month_end) in enumerate(month_ranges):
                    clean_start = None if idx == 0 else month_start
                    response = session.get(
                        report.build_url(
                            order_start_date,
                            order_end_date,
                            status,
                            keyword,
                            use_order_date=True,
                            clean_start=clean_start,
                            clean_end=month_end,
                        ),
                        headers=report.HEADERS,
                        allow_redirects=True,
                    )
                    response.raise_for_status()

                    for item in report.parse_html(response.text):
                        category = report.to_category(item["服務"], item["收入類型"])
                        if category in ("冷氣", "洗衣機"):
                            service = "家電"
                        elif category == "水洗":
                            service = "水洗"
                        else:
                            continue

                        key = (city, label, service)
                        if key not in totals:
                            totals[key] = {
                                "城市": city,
                                "月份": label,
                                "服務類別": service,
                                "待付款": 0,
                                "已付款": 0,
                            }
                        totals[key]["待付款"] += item["待付款"]
                        totals[key]["已付款"] += item["已付款"]

        return list(totals.values())

    city_results, errors = report._parallel_city_results(worker)
    rows = [row for _, city_rows in city_results for row in city_rows]
    out = _build_summary(rows, month_ranges)

    report.ensure_dirs()
    path = os.path.join(report.LATEST_DIR, "order_date_service_summary.csv")
    out.to_csv(path, index=False, encoding="utf-8-sig")
    report.append_output_file_log("訂購日期家電水洗付款彙總", path, trigger)
    report._update_latest_meta(
        order_date_service_rows=int(len(out)),
        order_date_service_error=" / ".join(errors) if errors else None,
    )
    return out
