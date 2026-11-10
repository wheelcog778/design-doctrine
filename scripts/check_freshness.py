#!/usr/bin/env python3
"""check_freshness —— 开工前查「这是哪一版、到几点为止」。

主人规矩第 1 条：调用最新项目时必须先查时间，不许拿记忆里的状态当现状。

用法:
    python check_freshness.py <项目目录> [<项目目录> ...]
    python check_freshness.py --json <目录>

输出每个项目：
    版本       读常见版本源（SKILL.md / pyproject.toml / package.json / CURRENT.txt / VERSION / brand.py）
    最新产物     docs|reports|src 等目录下最近改动的文件与距今小时数
    仓库         git HEAD 短哈希 + 未提交文件数（无 .git 则标 —）
退出码: 0 = 全部可读；1 = 有目录不存在或 Git 状态不可读；2 = 用法错误
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SCAN_DIRS = ("docs", "reports", "src", "gate", "data", "logs", "日志", "references", "scripts")
SKIP = {"__pycache__", ".git", "node_modules", "build", "dist", ".venv", "_归档"}
VERSION_FILES = ("SKILL.md", "pyproject.toml", "package.json", "CURRENT.txt",
                 "VERSION", "version.txt")
VERSION_RE = re.compile(r'VERSION\s*=\s*["\']([^"\']+)["\']')


def read_version(root: Path):
    for name in VERSION_FILES:
        p = root / name
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")[:4000]
        except OSError:
            continue
        if name == "SKILL.md":
            fm = re.match(r"\A---\s*\n(.*?)\n---\s*(?:\n|$)", text, re.S)
            m = re.search(r"(?m)^version:\s*([0-9][^\s]*)", fm.group(1)) if fm else None
            if m:
                return m.group(1), name
        elif name == "package.json":
            m = re.search(r'"version"\s*:\s*"([^"]+)"', text)
            if m:
                return m.group(1), name
        elif name == "pyproject.toml":
            m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
            if m:
                return m.group(1), name
        else:
            m = re.search(r"([0-9]+\.[0-9]+(?:\.[0-9]+)?)", text)
            if m and len(text.strip()) < 200:
                return m.group(1), name

    # 任何 brand.py / __init__.py 里的 VERSION 常量（不假设项目结构）
    cands = [root / "__init__.py", root / "brand.py"]
    cands += sorted(root.glob("*/brand.py"))[:3]
    cands += sorted(root.glob("*/__init__.py"))[:3]
    for q in cands:
        if not q.is_file():
            continue
        m = VERSION_RE.search(q.read_text(encoding="utf-8", errors="ignore"))
        if m:
            return m.group(1), str(q.relative_to(root)).replace("\\", "/")
    return "—", ""


def newest(root: Path):
    best, best_t = "", 0.0
    for sub in SCAN_DIRS:
        base = root / sub
        if not base.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in SKIP]
            for fn in filenames:
                if fn.endswith((".pyc", ".log")):
                    continue
                p = Path(dirpath) / fn
                try:
                    t = p.stat().st_mtime
                except OSError:
                    continue
                if t > best_t:
                    best, best_t = str(p), t
    return best, best_t


def git_head(root: Path):
    """返回 (HEAD, 脏文件数, 错误)；有 .git 却读不出时不得伪装成「无 git」。"""
    if not (root / ".git").exists():
        return "", -1, ""
    try:
        hp = subprocess.run(["git", "-C", str(root), "log", "-1", "--format=%h %s"],
                            capture_output=True, text=True, timeout=15)
        if hp.returncode != 0:
            return "", -1, (hp.stderr.strip() or "git log 失败")
        sp = subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                            capture_output=True, text=True, timeout=15)
        if sp.returncode != 0:
            return "", -1, (sp.stderr.strip() or "git status 失败")
        head = hp.stdout.strip()
        return head, len([x for x in sp.stdout.splitlines() if x.strip()]), ""
    except Exception as exc:                           # noqa: BLE001
        return "", -1, str(exc)


def one(root: Path):
    ver, src = read_version(root)
    newest_path, ts = newest(root)
    head, dirty, git_error = git_head(root)
    age_h = (datetime.now().timestamp() - ts) / 3600 if ts else None
    return {"path": str(root), "version": ver, "version_src": src,
            "newest": newest_path,
            "newest_at": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "",
            "age_hours": round(age_h, 1) if age_h is not None else None,
            "git": head, "dirty": dirty, "git_error": git_error}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="*", help="项目目录")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if not a.dirs:
        print(__doc__)
        sys.exit(2)
    rows, missing = [], 0
    for d in a.dirs:
        p = Path(d).expanduser().resolve()
        if not p.is_dir():
            rows.append({"path": str(p), "error": "目录不存在"})
            missing += 1
            continue
        rows.append(one(p))
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print(f"检查时间: {datetime.now():%Y-%m-%d %H:%M}\n")
        for r in rows:
            name = Path(r["path"]).name
            if r.get("error"):
                print(f"{name:<22} {r['error']}")
                continue
            v = f"v{r['version']}" + (f"（{r['version_src']}）" if r["version_src"] else "")
            g = ("git 无法读取" if r.get("git_error") else
                 (f"git {r['git'][:7]}" + (f" · 脏 {r['dirty']}" if r["dirty"] > 0 else " · 干净"))
                 if r["git"] else "无 git")
            age = f"{r['age_hours']:.1f}h 前" if r["age_hours"] is not None else "—"
            print(f"{name:<22} {v:<26} 最新产物 {r['newest_at']}（{age}）  {g}")
    unreadable = sum(1 for r in rows if r.get("git_error"))
    sys.exit(1 if missing or unreadable else 0)


if __name__ == "__main__":
    main()