# OP-system

op-system.streamlit.app 的業績報表（入口 `opapp.py`）。

2026-11-26 起跟 tool-system 用同一套業績報表，**以 op-system 為主**：

| 檔案 | 用途 |
|---|---|
| `tools/performance_report_ui/report_page.py` | 畫面（目前總表、訂購日期付款彙總、保留單明細、月份業績統整） |
| `tools/scheduled_daily/performance_report.py` | 計算與產出 `dashboard_data/` |
| `tools/scheduled_daily/performance_report_runner.py`、`reserve_deductions.py` | tool-system 排程用的 runner、保留單儲值金扣款 |

- 改業績報表請改這個 repo；push 到 main 後 `.github/workflows/sync-to-tool-system.yml` 會自動把上面這些檔案同步到 tool-system 同一路徑。
- 同步需要 repository secret `TOOL_SYSTEM_SYNC_TOKEN`（對 tool-system repo 有 Contents 讀寫權限的 fine-grained PAT）。
- 不要直接改 tool-system 裡的這些檔案，下次同步會被蓋掉。
- `tools/common/lemon_backend_login.py` 是從 tool-system 複製的後台登入，不會同步回去。
