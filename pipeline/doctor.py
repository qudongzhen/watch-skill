#!/usr/bin/env python
"""依赖逐项自检：出问题先看这个。

检查项按「缺了会怎样」排序，**必需 / 可选的口径只写在 `deps.py` 一处**
（早期这里有自己一套：缺 opencc 时这里退出码 0、selfcheck 退出码 1，同一台机器两种结论）。

    用法：py -3.14 pipeline/doctor.py
    退出码：0 = 必需项齐；1 = 必需项缺。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "pydeps"))  # 依赖装在项目内，不走 site-packages

import deps  # noqa: E402
from run import env  # noqa: E402  （复用同一套子进程环境：含 pipeline/compat 的 sitecustomize）

CONFIG = ROOT / "vw.config.json"


def probe_config() -> list[tuple[str, bool, str]]:
    """`vw.config.json` 里的字体路径（grid 画标题要用）。"""
    if not CONFIG.exists():
        return [("vw.config.json", False, "缺配置（可直接复制本仓库自带的一份）")]
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    font = Path(cfg.get("font", ""))
    return [("字体", font.exists(), str(font) if font.exists() else f"找不到 {font}")]


def probe_vw() -> list[tuple[str, bool, str]]:
    vw = ROOT / "vendor" / "video-watch" / "vw.py"
    if not vw.exists():
        return [("video-watch", False, "缺 vendor/video-watch（跑 scripts/bootstrap.py）")]
    r = subprocess.run([sys.executable, str(vw), "doctor"], cwd=ROOT, env=env(),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = r.stdout or ""
    ok = "必需项就绪" in out
    line = next((ln.strip() for ln in out.splitlines() if "必需项就绪" in ln), "")
    if not line:                       # 失败时给最后一行（或 stderr 末行），便于定位
        tail = [ln.strip() for ln in out.splitlines() if ln.strip()]
        if not tail:
            err = (r.stderr or "").strip().splitlines()
            tail = [err[-1]] if err else [""]
        line = tail[-1]
    return [("video-watch vw.py doctor", ok, line[:100])]


def main() -> int:
    print(f"python {sys.version.split()[0]}  {sys.platform}\n", flush=True)
    items = deps.probe()
    extras = probe_config() + probe_vw()

    for it in items:
        tag = "OK  " if it["ok"] else ("MISS" if it["required"] else "SKIP")
        print(f"  {tag}  {it['name']:<26} {it['detail']}")
    for name, ok, detail in extras:
        print(f"  {'OK  ' if ok else 'MISS'}  {name:<26} {detail}")

    print()
    req = deps.missing_required(items)
    opt = deps.missing_optional(items)
    hard_extra = [n for n, ok, _ in extras if not ok]
    for it in req:
        print(f"  必需项缺失：{it['name']} —— {it['fix']}")
    for it in opt:
        print(f"  可选链路缺失：{it['name']}（{it['detail']}）—— {it['fix']}")
    if hard_extra:
        print(f"  配置/上游缺失：{'、'.join(hard_extra)}")

    if req or hard_extra:
        print("\n必需项没齐，先修：", "、".join([i["name"] for i in req] + hard_extra))
        return 1
    print("取证链路就绪：probe / plan / grid / seq / read / sheet / report 可用")
    if opt:
        print(f"提示：{deps.summary(items)}；转写不可用时用 --no-asr，笔记缺语音证据。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
