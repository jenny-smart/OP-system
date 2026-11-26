# -*- coding: utf-8 -*-
"""把 op-system 的業績報表資料（dashboard_data）推到 GitHub 的 op-system 與 tool-system。

2026-11-26：業績報表以 op-system 為主。op-system 手動更新或排程更新完，都把資料推到兩個 repo，
兩邊 Streamlit app 讀到的數字一致；tool-system 的業績報表排程就不用再跑。

用 GitHub Git Data API 一次 commit 多個檔案（不需要 git 指令）。
token：TOOL_SYSTEM_SYNC_TOKEN（fine-grained PAT，op-system 與 tool-system 都要有 Contents 讀寫）。
"""
import base64
import csv
import io
import json
import os
import re
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
SYNC_DIRS = ("dashboard_data/latest", "dashboard_data/tool_report_extended")
TARGET_REPOS = ("jenny-smart/op-system", "jenny-smart/tool-system")
API = "https://api.github.com"


_STAFF_FIELDS = {"專員姓名", "staff_name", "staff_names"}
_NAME_PARTS = re.compile(r"([、，,；;/／|\s]+)")


def _mask_staff(value):
    """Hide all middle characters; a two-character name keeps only its first."""
    if isinstance(value, list):
        return [_mask_staff(item) for item in value]
    if value is None:
        return value
    parts = _NAME_PARTS.split(str(value))
    for i in range(0, len(parts), 2):
        name = parts[i]
        if len(name) >= 3:
            parts[i] = name[0] + "X" + name[-1]
        elif len(name) == 2:
            parts[i] = name[0] + "X"
        elif name:
            parts[i] = "X"
    return "".join(parts)


def _is_staff_field(key: str) -> bool:
    return key.lstrip("\ufeff").strip().casefold() in _STAFF_FIELDS


def _mask_json(value):
    if isinstance(value, list):
        return [_mask_json(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _mask_staff(item) if _is_staff_field(key) else _mask_json(item)
            for key, item in value.items()
        }
    return value


def _public_payload(path: str, content: bytes) -> bytes:
    """Sanitize only the copy sent to GitHub; never mutate the source files."""
    suffix = Path(path).suffix.lower()
    if suffix not in {".csv", ".json"}:
        return content
    encoding = "utf-8-sig" if content.startswith(b"\xef\xbb\xbf") else "utf-8"
    source = content.decode(encoding)
    if suffix == ".json":
        data = json.loads(source)
        masked = _mask_json(data)
        return content if data == masked else json.dumps(masked, ensure_ascii=False).encode(encoding)
    reader = csv.reader(io.StringIO(source, newline=""), strict=True)
    header = next(reader, [])
    cols = [i for i, key in enumerate(header) if _is_staff_field(key)]
    if not cols:
        if Path(path).name == "reserve_orders.csv" and header:
            raise ValueError("reserve_orders.csv: missing staff-name column")
        return content
    sink = io.StringIO(newline="")
    writer = csv.writer(sink)
    writer.writerow(header)
    for row in reader:
        if row and len(row) != len(header):
            raise ValueError(f"{path}: inconsistent CSV width")
        for i in cols:
            if row:
                row[i] = _mask_staff(row[i])
        writer.writerow(row)
    return sink.getvalue().encode(encoding)


def _files(base_dir: Path):
    for rel in SYNC_DIRS:
        folder = base_dir / rel
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and not path.name.endswith(".lock"):
                name = path.relative_to(base_dir).as_posix()
                yield name, _public_payload(name, path.read_bytes())


def _push_repo(session, repo: str, files, message: str, branch: str = "main") -> str:
    def call(method, url, **kwargs):
        resp = session.request(method, f"{API}/repos/{repo}{url}", timeout=60, **kwargs)
        if resp.status_code >= 400:
            raise RuntimeError(f"{repo} {method} {url} → {resp.status_code} {resp.text[:200]}")
        return resp.json()

    for attempt in range(3):
        head = call("GET", f"/git/ref/heads/{branch}")["object"]["sha"]
        base_tree = call("GET", f"/git/commits/{head}")["tree"]["sha"]
        tree = []
        for path, content in files:
            blob = call("POST", "/git/blobs", json={
                "content": base64.b64encode(content).decode(), "encoding": "base64",
            })["sha"]
            tree.append({"path": path, "mode": "100644", "type": "blob", "sha": blob})
        new_tree = call("POST", "/git/trees", json={"base_tree": base_tree, "tree": tree})["sha"]
        if new_tree == base_tree:
            return "沒有變更"
        commit = call("POST", "/git/commits", json={
            "message": message, "tree": new_tree, "parents": [head],
        })["sha"]
        resp = session.patch(f"{API}/repos/{repo}/git/refs/heads/{branch}",
                             json={"sha": commit}, timeout=60)
        if resp.status_code < 400:
            return f"已推送 {commit[:7]}"
        if resp.status_code != 422 or attempt == 2:  # 422：別人剛好也推了，重抓 HEAD 再試
            raise RuntimeError(f"{repo} 更新 {branch} 失敗：{resp.status_code} {resp.text[:200]}")
    return "未完成"


def push_dashboard_data(token: str | None = None, message: str = "Sync performance report data from op-system",
                        repos=TARGET_REPOS, base_dir: Path = BASE_DIR) -> dict:
    """回傳 {repo: 結果}；沒有 token 時不推並回傳說明。"""
    token = token or os.getenv("TOOL_SYSTEM_SYNC_TOKEN")
    if not token:
        return {"_": "沒有 TOOL_SYSTEM_SYNC_TOKEN，略過同步"}
    files = list(_files(base_dir))
    if not files:
        return {"_": "沒有 dashboard_data 可同步"}
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    results = {}
    for repo in repos:
        try:
            results[repo] = _push_repo(session, repo, files, message)
        except Exception as exc:  # 一個 repo 失敗不影響另一個
            results[repo] = f"失敗：{exc}"
    return results


if __name__ == "__main__":
    import sys

    repos = sys.argv[1:] or TARGET_REPOS
    result = push_dashboard_data(repos=repos)
    print(json.dumps(result, ensure_ascii=False))
    if any(str(v).startswith("失敗") for v in result.values()):
        sys.exit(1)
