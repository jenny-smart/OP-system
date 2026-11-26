# -*- coding: utf-8 -*-
"""op-system 業績報表（op-system.streamlit.app）。

2026-11-26 版本 2026-11-26-tool-system-report-v1：
- 整套改用 tool-system 的業績報表（之前 op-system 自己的版本已被 tool-system 覆蓋）。
- 畫面：tools/performance_report_ui/report_page.py；計算：tools/scheduled_daily/performance_report.py。
- 之後由 op-system 改，push 到 main 後 GitHub Actions（sync-to-tool-system.yml）自動同步到 tool-system。
- 目前總表、訂購日期付款彙總、保留單業績是雙重表頭（上方月份、下方副標題）。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import streamlit as st

from tools.performance_report_ui import report_page

OPAPP_VERSION = "2026-11-26-tool-system-report-v1"
BASE_DIR = Path(__file__).resolve().parent

st.set_page_config(page_title="Jenny 業績報表", page_icon="🍋", layout="wide")

_ACCOUNT_ENV = {
    "taipei": "TAIPEI",
    "taichung": "TAICHUNG",
    "taoyuan": "TAOYUAN",
    "hsinchu": "HSINCHU",
    "kaohsiung": "KAOHSIUNG",
}


def _subprocess_env() -> dict:
    """Streamlit Secrets 不會自動傳給子程序：把各區帳密與字串型 secrets 放進環境變數。"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BASE_DIR) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    try:
        for key in st.secrets:
            value = st.secrets[key]
            if isinstance(value, str):
                env[key] = value
        accounts = st.secrets.get("accounts", {})
        for key, prefix in _ACCOUNT_ENV.items():
            account = accounts.get(key) if accounts else None
            if account:
                env.setdefault(f"{prefix}_EMAIL", str(account.get("email", "")))
                env.setdefault(f"{prefix}_PASSWORD", str(account.get("password", "")))
    except Exception:
        pass
    return env


def run_script(script_path: str, args=None) -> str:
    """執行業績報表計算（跟 tool-system 一樣跑 tools/scheduled_daily/performance_report.py）。"""
    script = BASE_DIR / script_path
    if not script.exists():
        raise RuntimeError(f"找不到執行檔：{script_path}")
    completed = subprocess.run(
        [sys.executable, "-u", str(script), *(args or [])],
        cwd=BASE_DIR, env=_subprocess_env(), capture_output=True, text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"執行失敗：{script_path}\nexit={completed.returncode}\n"
            f"STDOUT:\n{completed.stdout[-3000:]}\nSTDERR:\n{completed.stderr[-3000:]}"
        )
    return f"{script_path} 執行完成"


def add_log(message: str, level: str = "info") -> None:
    print(json.dumps({"level": level, "message": message}, ensure_ascii=False), flush=True)


# 2026-11-26：Streamlit 不會自動重新載入子模組，部署新版後可能還在跑舊的 report_page（曾因此出現 KeyError）。
# 每次執行都重新載入，確保畫面跟 repo 一致。
import importlib  # noqa: E402

report_page = importlib.reload(report_page)
report_page.render_report(run_script=run_script, add_log=add_log)
st.caption(f"版本 {OPAPP_VERSION}")
