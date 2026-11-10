#!/usr/bin/env python3
"""version_lint —— 版本单一性校验

唯一真源：SKILL.md frontmatter 的 `version:`。
其余出现版本号的地方必须与它一致；不一致直接退出码 1。

用法:
    python version_lint.py [包根目录]
    python version_lint.py --json

检查项：
  1. SKILL.md frontmatter version
  2. README.md 第一行标题里的 `vX.Y.Z`
  3. README.md 更新日志第一条 `**vX.Y.Z**`
  4. 可选：外部索引文件（--index 路径）里本包的版本串
退出码: 0 一致 / 1 不一致 / 2 用法错误
"""
import argparse
import json
import os
import re
import sys


def read(path):
    return open(path, encoding="utf-8-sig").read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--index", help="额外校验一个索引文件（找 design-doctrine 版本的相邻文本）")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    root = os.path.abspath(a.root)
    skill = os.path.join(root, "SKILL.md")
    readme = os.path.join(root, "README.md")
    if not (os.path.isfile(skill) and os.path.isfile(readme)):
        print("用法: version_lint.py <含 SKILL.md 与 README.md 的目录>")
        sys.exit(2)

    skill_text = read(skill)
    fm = re.match(r"\A---\s*\n(.*?)\n---\s*(?:\n|$)", skill_text, re.S)
    m = re.search(r"(?m)^version:\s*([0-9]+\.[0-9]+\.[0-9]+)", fm.group(1)) if fm else None
    if not m:
        print("SKILL.md 缺 frontmatter version")
        sys.exit(1)
    ver = m.group(1)

    problems = []
    rm = read(readme)
    h1 = rm.splitlines()[0] if rm else ""
    m1 = re.search(r"v([0-9]+\.[0-9]+\.[0-9]+)", h1)
    if not m1:
        problems.append(f"README 第一行没有版本号：{h1[:60]}")
    elif m1.group(1) != ver:
        problems.append(f"README 标题 v{m1.group(1)} ≠ SKILL {ver}")

    m2 = re.search(r"\*\*v([0-9]+\.[0-9]+\.[0-9]+)\*\*", rm)
    if not m2:
        problems.append("README 更新日志没有 `**vX.Y.Z**` 条目")
    elif m2.group(1) != ver:
        problems.append(f"README 日志最新条目 v{m2.group(1)} ≠ SKILL {ver}（应为最新在最上）")

    if a.index:
        if not os.path.isfile(a.index):
            print(f"索引文件不存在: {a.index}")
            sys.exit(2)
        idx = read(a.index)
        # 只校验「当前版本」那一行；历史变更日志与归档说明里的旧版本号不算冲突
        for line in idx.splitlines():
            if not any(k in line for k in ("唯一真源", "当前版本", "执行版")):
                continue
            m3 = re.search(r"[vV]([0-9]+\.[0-9]+\.[0-9]+)", line)
            if m3 and m3.group(1) != ver:
                problems.append(f"索引当前版本行写 v{m3.group(1)}（真源 {ver}）：{line.strip()[:60]}")

    out = {"version": ver, "root": root, "problems": problems}
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"版本真源: {ver}")
        if problems:
            for p in problems:
                print(f"  [FAIL] {p}")
        else:
            print("  [OK] README 标题、更新日志首条一致")
    sys.exit(0 if not problems else 1)


if __name__ == "__main__":
    main()