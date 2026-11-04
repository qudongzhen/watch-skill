#!/usr/bin/env python
"""用 GitHub Git Data API 发布（git push 走不通时的兜底路径）。

    py -3.14 scripts/publish_api.py                  # 把发布树整体推成一个新提交
    py -3.14 scripts/publish_api.py -m "说明"        # 指定提交说明
    py -3.14 scripts/publish_api.py --dry-run        # 只统计要传什么，不写远端

为什么需要它：本机 `github.com:443` 与 `api.github.com` 走的不是同一条路——实测
git push 报 `Failed to connect to github.com port 443`，而 `gh api` 一直正常。
git/curl 在这个沙箱里走 schannel，Python 的 OpenSSL 才是可靠的传输（DECISIONS D3）。

和大白名单脚本的关系：**先跑 `scripts/publish.py` 生成/校准发布树与报告**，再用本脚本
把 `work/publish/watch-skill` 整棵树推上去。本脚本自己不复制文件、不做敏感扫描。

语义：
  - 每个文件建成一个 blob，再建一棵**不带 base_tree** 的树——远端只会有发布树里的文件，
    白名单外的历史残留会被这次提交清掉；
  - 无父提交时（空库）先落一个 `.gitignore` 当首个提交，因为空库不接受建 blob（409）；
  - token 只在本进程内存里，不落盘、不进命令行参数。
"""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.github.com"
OWNER, REPO = "qudongzhen", "watch-skill"
DEFAULT_TREE = ROOT / "work" / "publish" / "watch-skill"


def token() -> str:
    out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True).stdout.strip()
    if not out:
        raise SystemExit("拿不到 gh token（先 gh auth login）")
    return out


HDR: dict[str, str] = {}


def call(method: str, path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(API + path, data=data, headers=HDR, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        raise SystemExit(f"{method} {path} -> {e.code} {detail}")


def tree_files(tree: Path) -> list[Path]:
    return [p for p in sorted(tree.rglob("*")) if p.is_file() and ".git" not in p.parts]


def head_sha() -> str | None:
    try:
        return call("GET", f"/repos/{OWNER}/{REPO}/git/ref/heads/main")["object"]["sha"]
    except SystemExit:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="用 Git Data API 发布发布树")
    ap.add_argument("-m", "--message", default="publish: 同步本地项目树")
    ap.add_argument("--tree", default=str(DEFAULT_TREE), help="发布树目录")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    global HDR
    HDR = {"Authorization": f"Bearer {token()}",
           "Accept": "application/vnd.github+json",
           "X-GitHub-Api-Version": "2022-11-28",
           "User-Agent": "watch-skill-publish-api"}

    tree = Path(args.tree)
    if not tree.is_dir():
        raise SystemExit(f"发布树不存在：{tree}（先跑 scripts/publish.py）")
    files = tree_files(tree)
    total = sum(p.stat().st_size for p in files)
    print(f"发布树：{tree}")
    print(f"文件 {len(files)} 个，合计 {total:,} 字节")

    parent = head_sha()
    print("远端 HEAD：", parent[:8] if parent else "空库")
    if args.dry_run:
        print("（dry-run：没有写远端）")
        return 0

    if not parent:
        gi = (tree / ".gitignore").read_text(encoding="utf-8")
        boot = call("PUT", f"/repos/{OWNER}/{REPO}/contents/.gitignore",
                    {"message": "init: 忽略规则",
                     "content": base64.b64encode(gi.encode()).decode(), "branch": "main"})
        parent = boot["commit"]["sha"]
        print("空库，先落首个提交：", parent[:8])

    entries = []
    for p in files:
        rel = p.relative_to(tree).as_posix()
        blob = call("POST", f"/repos/{OWNER}/{REPO}/git/blobs",
                    {"content": base64.b64encode(p.read_bytes()).decode("ascii"),
                     "encoding": "base64"})
        entries.append({"path": rel, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    print(f"blob：{len(entries)} 个")

    new_tree = call("POST", f"/repos/{OWNER}/{REPO}/git/trees", {"tree": entries})
    commit = call("POST", f"/repos/{OWNER}/{REPO}/git/commits",
                  {"message": args.message, "tree": new_tree["sha"], "parents": [parent]})
    call("PATCH", f"/repos/{OWNER}/{REPO}/git/refs/heads/main",
         {"sha": commit["sha"], "force": True})
    call("PATCH", f"/repos/{OWNER}/{REPO}", {"default_branch": "main"})
    print(f"提交 {commit['sha'][:8]} 已推送：https://github.com/{OWNER}/{REPO}")
    paths = call("GET", f"/repos/{OWNER}/{REPO}/git/trees/main?recursive=1")["tree"]
    notes = [x["path"] for x in paths if x["path"].startswith("notes/")]
    print(f"远端路径 {len(paths)} 个，其中 notes/ 开头 {len(notes)} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
