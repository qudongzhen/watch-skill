#!/usr/bin/env python
"""白名单发布：生成可上传的项目树、出体积/敏感报告，并把"远端漂移"挡在推送之前。

    py -3.14 scripts/publish.py                  # 重建发布树 + 报告（不联网、不写远端）
    py -3.14 scripts/publish.py --check          # 只扫描已有发布树，不复制
    py -3.14 scripts/publish.py --push           # 报告通过后提交并推送（写远端，先确认再用）
    py -3.14 scripts/publish.py --push --force   # 跳过远端漂移检查（会覆盖别人合进来的改动）

三条规矩：

1. **只复制项目本体**。notes/ exports/ work/ tools/ pydeps/ 旧的 .git 历史，以及含本机
   绝对路径的 vw.config.json 一律不进发布树（改发 vw.config.example.json）。
2. **发布树里的 .git 保留**。每次发布是同一条历史上的新提交，不是重建仓库；历史断了就没法
   安全推送了。
3. **推之前先看远端**。上次发布之后远端若是多了提交（有人提 PR 并合了、或网页上直接改），
   先把那些改动回灌到本地，否则这次发布会把它们覆盖掉。--force 是"我知道，照发"。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "work" / "publish" / "watch-skill"
REPORT = ROOT / "work" / "publish" / "report.txt"
BASE = ROOT / "work" / "publish" / "base.txt"   # 上次发布成功的提交 sha
REPO = "qudongzhen/watch-skill"
BRANCH = "main"
IDENTITY = ("qudongzhen", "qudongzhen@users.noreply.github.com")

DIRS = ["pipeline", "scripts", "docs", "vendor/video-watch", "examples"]
FILES = [
    "README.md", "AGENTS.md", "DECISIONS.md", "CONTRIBUTING.md", "LICENSE", "NOTICE",
    "requirements.txt", ".gitignore", ".gitattributes", "asr-terms.json",
    "vw.config.example.json",
]
SKIP = {"__pycache__", ".DS_Store", ".pytest_cache", "node_modules"}
BINARY = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".exe", ".zip", ".ttc", ".ttf"}

PUBLIC_IGNORE = """
# ---- 以下由 scripts/publish.py 追加，只出现在公开仓库里 ----
# 成品笔记与素材不随仓库分发；本地配置含机器绝对路径，请复制 *.example.json
notes/
vw.config.json
"""

CHECKS = [
    ("绝对路径", re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/](?!n)"), "warn"),
    ("用户目录/邮箱", re.compile(r"[\\/]Users[\\/][^\\/]|21690|@qq\.com"), "warn"),
    ("凭据", re.compile(
        r"gh[opsu]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9]{20,}"
        r"|SESSDATA=[A-Za-z0-9%]{10,}|bili_jct=[0-9a-f]{20,}|AKIA[0-9A-Z]{16}"), "fail"),
]
# 允许的绝对路径：示例配置（用户本来就要照抄）与 vendor/（上游原样，示例路径）
ALLOWED_PATH_PREFIX = "vendor/"
ALLOWED_PATH_FILES = {"vw.config.example.json"}
# 扫描器自身：里面的正则就是敏感词，不该被自己命中
SKIP_SCAN = {"scripts/publish.py"}

# ---------------------------------------------------------------- 子进程


def git(*args: str, cwd: Path | None = None) -> tuple[int, str]:
    r = subprocess.run(["git", *args], cwd=str(cwd or DEST), capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def gh(*args: str) -> tuple[int, str]:
    try:
        r = subprocess.run(["gh", *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return 127, "gh 不在 PATH 里"
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def mask(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret else text


# ---------------------------------------------------------------- 复制


def _force_remove(func, path, _exc):
    """git 会把 objects/ 写成只读，删之前先去掉只读位。"""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def build() -> list[str]:
    missing = [f for f in FILES if not (ROOT / f).is_file()]
    if missing:
        return [f"根文件缺失：{', '.join(missing)}"]

    DEST.mkdir(parents=True, exist_ok=True)
    for child in list(DEST.iterdir()):
        if child.name == ".git":          # 保留历史：发布树是同一条线上的新提交
            continue
        if child.is_dir():
            shutil.rmtree(child, onexc=_force_remove)
        else:
            _force_remove(os.unlink, str(child), None)

    for d in DIRS:
        src = ROOT / d
        if not src.is_dir():
            return [f"目录缺失：{d}"]
        shutil.copytree(src, DEST / d, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(*SKIP))

    for f in FILES:
        shutil.copy2(ROOT / f, DEST / f)

    ignore = DEST / ".gitignore"
    text = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
    ignore.write_text(text.rstrip("\n") + "\n" + PUBLIC_IGNORE, encoding="utf-8")
    return []


# ---------------------------------------------------------------- 报告


def walk() -> list[Path]:
    """发布树里的普通文件；.git/ 是本地提交用的，不算发布内容。"""
    return sorted(
        p for p in DEST.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(DEST).parts
    )


def scan(files: list[Path]) -> list[tuple[str, str, int, str]]:
    hits: list[tuple[str, str, int, str]] = []
    for p in files:
        if p.suffix.lower() in BINARY:
            continue
        rel = p.relative_to(DEST).as_posix()
        if rel in SKIP_SCAN:
            continue
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for no, line in enumerate(lines, 1):
            for name, pat, level in CHECKS:
                if not pat.search(line):
                    continue
                if name == "绝对路径" and (rel in ALLOWED_PATH_FILES or rel.startswith(ALLOWED_PATH_PREFIX)):
                    continue
                hits.append((level, name, no, f"{rel}:{no}  {line.strip()[:90]}"))
    return hits


def report(files: list[Path], hits: list[tuple[str, str, int, str]]) -> str:
    total = sum(p.stat().st_size for p in files)
    out: list[str] = []
    out.append(f"发布树：{DEST}")
    out.append(f"文件 {len(files)} 个，合计 {total:,} 字节（{total / 1048576:.2f} MB）")
    out.append("")

    out.append("按目录：")
    groups: dict[str, list[int]] = {}
    for p in files:
        rel = p.relative_to(DEST)
        key = rel.parts[0] if len(rel.parts) > 1 else "<根目录>"
        g = groups.setdefault(key, [0, 0])
        g[0] += 1
        g[1] += p.stat().st_size
    for key in sorted(groups, key=lambda k: -groups[k][1]):
        n, size = groups[key]
        out.append(f"  {key:<18} {n:>3} 个  {size:>9,} B")
    out.append("")

    out.append("最大的 10 个文件：")
    for p in sorted(files, key=lambda p: -p.stat().st_size)[:10]:
        out.append(f"  {p.stat().st_size:>8,} B  {p.relative_to(DEST).as_posix()}")
    out.append("")

    if not hits:
        out.append("敏感项：无")
    else:
        out.append(f"敏感项：{len(hits)} 处")
        for level, name, _no, desc in hits:
            tag = "FAIL" if level == "fail" else "warn"
            out.append(f"  [{tag}] {name}  {desc}")
    return "\n".join(out)


# ---------------------------------------------------------------- 远端漂移


def local_head() -> str:
    code, out = git("rev-parse", "HEAD")
    return out.strip() if code == 0 else ""


def remote_head() -> str:
    code, out = gh("api", f"repos/{REPO}/commits/{BRANCH}", "--jq", ".sha")
    return out.strip() if code == 0 else ""


def read_base() -> str:
    return BASE.read_text(encoding="utf-8").strip() if BASE.is_file() else ""


def write_base(sha: str) -> None:
    BASE.parent.mkdir(parents=True, exist_ok=True)
    BASE.write_text(sha + "\n", encoding="utf-8")


def changed_on_remote(base: str, head: str) -> list[str]:
    code, out = gh("api", f"repos/{REPO}/compare/{base}...{head}", "--jq",
                   ".files[].filename")
    if code != 0:
        return []
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def drift_check(force: bool) -> tuple[bool, list[str]]:
    """远端自上次发布后是否前进。返回 (能否继续, 提示行)。"""
    notes: list[str] = []
    head = remote_head()
    if not head:
        notes.append("远端检查：跳过（gh 不可用或网络不通）")
        return True, notes

    base = read_base()
    if not base:
        ours = local_head()
        if ours and ours == head:
            write_base(head)
            notes.append(f"远端检查：首次记录基线 {head[:7]}")
            return True, notes
        notes.append(f"远端检查：没有基线，但远端已有 {head[:7]}（不是我们最后推的）")
        notes.append("  → 先确认本地是否包含远端那些改动，否则发布会覆盖它们")
        return bool(force), notes

    if head == base:
        notes.append(f"远端检查：未前进（{head[:7]}），可以直接发")
        return True, notes

    files = changed_on_remote(base, head)
    notes.append(f"远端检查：远端自 {base[:7]} 前进到 {head[:7]}——有人合了 PR 或在网页上改过")
    if files:
        notes.append(f"  涉及 {len(files)} 个文件：")
        for f in files[:20]:
            notes.append(f"    {f}")
        if len(files) > 20:
            notes.append(f"    …… 还有 {len(files) - 20} 个")
    notes.append("  → 先把这些改动回灌到本地树（否则本次发布会把它们覆盖）")
    notes.append("  → 确认本地已包含，或就是要覆盖：加 --force")
    return bool(force), notes


# ---------------------------------------------------------------- 提交与推送


def commit(message: str) -> tuple[int, str]:
    code, out = git("add", "-A")
    if code != 0:
        return code, out
    code, out = git("status", "--porcelain")
    if not out.strip():
        return 0, "（发布树与上次提交一致，无需新提交）"
    code, out = git("-c", f"user.name={IDENTITY[0]}", "-c", f"user.email={IDENTITY[1]}",
                    "commit", "-m", message)
    return code, out


def push() -> tuple[int, str]:
    code, tok = gh("auth", "token")
    tok = tok.strip()
    if code != 0 or not tok:
        return 1, "拿不到 gh token（先跑 gh auth login）"
    url = f"https://x-access-token:{tok}@github.com/{REPO}.git"
    # 本沙箱的 schannel 拿不到客户端凭证，必须走 openssl；token 只出现在本次命令行里，不落盘
    code, out = git("-c", "http.sslBackend=openssl", "push", url, f"HEAD:{BRANCH}")
    return code, mask(out, tok)


def main() -> int:
    ap = argparse.ArgumentParser(description="白名单发布：生成可上传的项目树并报告体积")
    ap.add_argument("--check", action="store_true", help="只扫描已有发布树，不重新复制")
    ap.add_argument("--push", action="store_true", help="报告通过后提交并推送到远端")
    ap.add_argument("--force", action="store_true", help="跳过远端漂移检查（会覆盖远端改动）")
    ap.add_argument("-m", "--message", default="publish: 同步本地项目树", help="提交说明")
    args = ap.parse_args()

    if not args.check:
        errors = build()
        if errors:
            for e in errors:
                print(f"错误：{e}", file=sys.stderr)
            return 1

    if not DEST.is_dir():
        print(f"错误：发布树不存在（先不加 --check 跑一次）：{DEST}", file=sys.stderr)
        return 1

    files = walk()
    hits = scan(files)
    text = report(files, hits)
    print(text)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(text + "\n", encoding="utf-8")

    fails = [h for h in hits if h[0] == "fail"]
    print()
    if fails:
        print("结论：有凭据命中，不许上传")
        return 1

    ok, notes = drift_check(args.force)
    print("漂移检查：")
    for line in notes:
        print("  " + line)
    if not ok:
        print()
        print("结论：远端有本地没有的改动，先处理（或 --force 覆盖）")
        return 2

    if not args.push:
        print()
        print("结论：可以通过（warn 项请人眼确认）。确认无误后加 --push 提交并推送。")
        return 0

    code, out = commit(args.message)
    print("提交：" + out.strip().splitlines()[0] if out.strip() else "提交：无变化")
    if code != 0:
        return 3

    code, out = push()
    print("推送：" + out.strip().splitlines()[-1] if out.strip() else "推送：无输出")
    if code != 0:
        return 4

    write_base(local_head())
    print(f"完成：https://github.com/{REPO}  （基线 {local_head()[:7]}，{datetime.now():%Y-%m-%d %H:%M}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
