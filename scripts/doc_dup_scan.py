#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""同义收口的立账工具：跨文件重复行 + 一条事实的可编辑落点数。

用法:
    python doc_dup_scan.py <目录> [--min-len 19] [--fact "串" ...] [--ignore 文件 ...]

  --ignore   额外不计入的文件名，可重复
  --fact     要数落点的事实串，可重复；逐行计数并打印行号

输出分两类:
  「真重复」= 忽略集之外，同一整行出现在 ≥2 个文件里 —— 会让退出码变 1
  「仅快照内重复」= 命中里含历史快照 —— 只报告，不影响退出码

退出码: 0 = 无真重复且每条事实落点 ≤1；1 = 有真重复或事实落点 >1；2 = 用法错误。
"""
import argparse
import pathlib
import sys
from collections import defaultdict

SNAPSHOTS = set()                            # 声明为历史快照、允许与正文重复的文件名


def _norm_metric_value(s: str) -> str:
    """把 7 / 7.0 / +7 归到同一个键；单位（pp / %）分开算。

    只比字符串会把 7 与 7.0 报成冲突——这类误报一多，整条检查就会被弃用。
    符号仍保留：−7 与 7 是两个数。
    """
    t = s.replace("−", "-").strip()
    unit = ""
    for suf in ("pp", "%"):
        if t.endswith(suf):
            unit, t = suf, t[: -len(suf)]
            break
    try:
        return f"{float(t):+.10g}|{unit}"
    except ValueError:
        return s


def selftest() -> bool:
    """对抗断言：格式差异不算冲突，真冲突必须报。"""
    import contextlib
    import io
    import os
    import tempfile

    cases = [("7", "7.0", 0), ("7", "+7", 0), ("7", "9", 1), ("7", "7%", 1)]
    fails = []
    for a, b, want in cases:
        with tempfile.TemporaryDirectory() as td:
            open(os.path.join(td, "a.md"), "w", encoding="utf-8").write("M1_before=" + a + "\n")
            open(os.path.join(td, "b.md"), "w", encoding="utf-8").write("M1_before=" + b + "\n")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = main([td, "--metric", "M1_before"])
            if rc != want:
                fails.append(f"{a} vs {b}: exit={rc}，期望 {want}")
    print("selftest:", "PASS" if not fails else "FAIL")
    for item in fails:
        print("  [FAIL]", item)
    return not fails


def main(argv=None):
    ap = argparse.ArgumentParser(description="同义收口立账：跨文件重复行与事实落点")
    ap.add_argument("root", nargs="?")
    ap.add_argument("--selftest", action="store_true", help="跑对抗断言（格式差异 vs 真冲突）")
    ap.add_argument("--min-len", type=int, default=19, help="计入比较的最小行长（默认 19）")
    ap.add_argument("--fact", action="append", default=[], help="要数落点的事实串，可重复")
    ap.add_argument("--ignore", action="append", default=[], help="额外忽略的文件名，可重复")
    ap.add_argument("--metric", action="append", default=[], help="检查同名指标跨文件数值是否一致（如 M1、M1_before）")
    args = ap.parse_args(argv)
    if args.selftest:
        return 0 if selftest() else 1
    if not args.root:
        print("用法: doc_dup_scan.py <目录> [--metric 指标 ...] | --selftest", file=sys.stderr)
        return 2

    root = pathlib.Path(args.root)
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2

    ignored = SNAPSHOTS | set(args.ignore)
    all_md = [p for p in root.rglob("*.md") if p.is_file()]
    files = sorted(p for p in all_md if p.name not in ignored)
    snaps = sorted(p for p in all_md if p.name in ignored)
    if not files:
        print("no .md files", file=sys.stderr)
        return 2

    def rel(p):
        return p.as_posix().replace(root.as_posix() + "/", "")

    def lines_of(p):
        return p.read_text(encoding="utf-8", errors="replace").split("\n")

    owner = defaultdict(list)               # 整行 -> [(rel, lineno)]
    for path in files:
        for i, line in enumerate(lines_of(path), 1):
            s = line.strip()
            if len(s) < args.min_len:
                continue
            if set(s) <= set("-|: >"):
                continue
            if s.startswith("```"):
                continue
            owner[s].append((rel(path), i))

    real, snap_only = {}, {}
    for s, places in owner.items():
        if len({r for r, _ in places}) > 1:
            real[s] = places
    for path in snaps:                       # 快照与正文的重合只登记，不判失败
        seen = defaultdict(list)
        for i, line in enumerate(lines_of(path), 1):
            s = line.strip()
            if len(s) >= args.min_len and s in owner:
                seen[s].append((rel(path), i))
        for s, places in seen.items():
            snap_only[s] = places

    print(f"扫描 {len(files)} 个文件（另忽略 {len(snaps)} 个快照）；真重复 {len(real)} 条，"
          f"仅快照内重复 {len(snap_only)} 条")
    for s, places in sorted(real.items(), key=lambda kv: (-len(kv[0]), kv[1])):
        where = ", ".join(f"{r}:{n}" for r, n in places)
        print(f"  [真重复 {len(s)}] {where}\n      {s[:100]}")
    if snap_only and not real:
        print(f"  （{len(snap_only)} 条重复全部来自历史快照 {sorted(SNAPSHOTS)}，按设计允许）")

    rc = 1 if real else 0
    if args.fact:
        print("\n事实落点：")
        for fact in args.fact:
            hits = []
            for p in files:  # 只数当前可编辑正文；历史快照不能充当有效事实落点
                for i, line in enumerate(lines_of(p), 1):
                    if fact in line:
                        hits.append((rel(p), i, line.strip()[:60]))
            if not hits:
                print(f"  [MISS] {fact!r} -> 0 处")
                rc = 1
                continue
            mark = "OK" if len(hits) == 1 else "DUP"
            print(f"  [{mark}] {fact!r} -> {len(hits)} 处")
            for r, n, txt in hits:
                kind = "定义?" if any(k in txt for k in ("是", "指", "定义", "=")) else "指针?"
                print(f"      {r}:{n}  ({kind})  {txt}")
            if len(hits) > 1:
                rc = 1
    if args.metric:
        import re as _re
        print("\n指标数值一致性：")
        for mname in args.metric:
            pat = _re.compile(_re.escape(mname) + r"\s*(?:=|:|：)\s*([+-−]?\d+(?:\.\d+)?(?:pp|%)?)")
            vals = defaultdict(list)
            shown = {}
            for p in files:
                for i, line in enumerate(lines_of(p), 1):
                    for m in pat.finditer(line):
                        raw = m.group(1).replace("−", "-")
                        key = _norm_metric_value(raw)
                        vals[key].append(f"{rel(p)}:{i}")
                        shown.setdefault(key, raw)
            if len(vals) > 1:
                detail = {shown[k]: v for k, v in vals.items()}
                print(f"  [CONFLICT] {mname!r} 出现 {len(vals)} 种不同数值: {detail}")
                rc = 1
            elif len(vals) == 1:
                k = next(iter(vals))
                print(f"  [OK] {mname!r} = {shown[k]} ({len(vals[k])} 处一致；7/7.0/+7 视为同值)")
            else:
                print(f"  [NONE] {mname!r} 未命中 `指标=数值` 形态")
    return rc


if __name__ == "__main__":
    sys.exit(main())
