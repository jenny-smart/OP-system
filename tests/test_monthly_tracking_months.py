from datetime import datetime

import pandas as pd

import performance_report as report


def _df4():
    rows = []
    for city, base in [("台北", 100), ("台中", 50), ("桃園", 30), ("新竹", 20), ("高雄", 10), ("加總", 210)]:
        row = {"城市": city}
        for i, name in enumerate(report.REPORT_MONTH_NAMES):
            row[f"{name}加總"] = base * (i + 1)
            row[f"{name}佔比"] = base / 210
        rows.append(row)
    return pd.DataFrame(rows)


def test_extra_period_overview_rows_use_the_right_month_and_columns(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "LATEST_DIR", str(tmp_path))
    run_dt = datetime(2026, 11, 18, 14, 46, 51)

    extra = report.build_extra_period_overview_dfs(_df4(), source="dashboard", run_dt=run_dt)

    assert list(extra) == ["次次月", "次次次月", "次次次次月"]
    # 本月 2026/11 → 次月 2026/12 → 次次月 2027/01 …
    assert extra["次次月"].iloc[0]["統計月份"] == "2027/01"
    assert extra["次次次月"].iloc[0]["統計月份"] == "2027/02"
    assert extra["次次次次月"].iloc[0]["統計月份"] == "2027/03"
    # 台北 base=100：次次月是第 3 個月 → 300，次次次次月是第 5 個月 → 500
    assert extra["次次月"].iloc[0]["台北業績"] == 300
    assert extra["次次次次月"].iloc[0]["全區合計"] == 210 * 5
    assert extra["次次月"].iloc[0]["id"].endswith("_次次月")


def test_next_month_label_unchanged_for_existing_tabs(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "LATEST_DIR", str(tmp_path))
    run_dt = datetime(2026, 12, 20, 8, 0, 0)

    this_month = report.build_daily_overview_df(_df4(), run_dt=run_dt)
    next_month = report.build_next_month_overview_df(_df4(), run_dt=run_dt)

    assert this_month.iloc[0]["統計月份"] == "2026/12"
    assert next_month.iloc[0]["統計月份"] == "2027/01"
    assert next_month.iloc[0]["台北業績"] == 200


def test_month_end_snapshot_has_five_months(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "DASHBOARD_DIR", str(tmp_path))
    monkeypatch.setattr(report, "LATEST_DIR", str(tmp_path / "latest"))
    monkeypatch.setattr(report, "SNAPSHOT_DIR", str(tmp_path / "snapshots"))
    monkeypatch.setattr(report, "EXEC_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(report, "DAILY_HISTORY_DIR", str(tmp_path / "hist"))
    monkeypatch.setattr(report, "NEXT_MONTH_HISTORY_DIR", str(tmp_path / "next_hist"))
    monkeypatch.setattr(report, "MONTH_END_HISTORY_FILE", str(tmp_path / "hist" / "month_end_summary.csv"))
    monkeypatch.setattr(report, "OUTPUT_LOG_FILE", str(tmp_path / "output_file_log.csv"))
    monkeypatch.setattr(report, "now_dt", lambda: datetime(2026, 11, 30, 23, 0, 0))
    report.ensure_dirs()

    out = report.build_month_end_summary_df(_df4())

    assert list(out.columns) == report.MONTH_END_COLS
    taipei = out[out["城市"] == "台北"].iloc[0]
    assert taipei["當月業績"] == 100
    assert taipei["次月業績"] == 200
    assert taipei["次次次次月業績"] == 500
    assert taipei["次次次月總業績"] == 210 * 4
    assert len(report.load_month_end_history()) == len(out)
