# -*- coding: utf-8 -*-
"""
统一 pipeline：抓取 → 标注 → 建索引

用法：
    python3 scripts/pipeline.py --date 2026-11-05
    python3 scripts/pipeline.py                # 今天

环境变量 MINIMAX_CN_API_KEY 优先；缺失时从 /root/.hermes/.env 兜底读取。
"""

import argparse
import json
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(BASE, "scripts")
DATA = os.path.join(BASE, "data")


def ensure_api_key():
    """返回 key；若环境变量没有，从 .env 兜底。返回 None 表示没有。"""
    key = os.environ.get("MINIMAX_CN_API_KEY", "").strip()
    if key:
        return key
    env_path = "/root/.hermes/.env"
    if os.path.exists(env_path):
        try:
            with open(env_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("MINIMAX_CN_API_KEY="):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if val:
                            os.environ["MINIMAX_CN_API_KEY"] = val
                            return val
        except Exception:
            pass
    return None


def run(cmd):
    print(f"\n$ {' '.join(cmd)}", flush=True)
    res = subprocess.run(cmd, cwd=BASE)
    if res.returncode != 0:
        print(f"!! 命令失败，退出码 {res.returncode}", file=sys.stderr)
    return res.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="YYYY-MM-DD，默认今天")
    ap.add_argument("--yesterday", action="store_true",
                    help="抓取前一天（申论素材通常看昨天报纸）")
    ap.add_argument("--skip-fetch", action="store_true", help="跳过抓取")
    ap.add_argument("--skip-annotate", action="store_true", help="跳过标注")
    args = ap.parse_args()

    if not ensure_api_key():
        print("!! 找不到 MINIMAX_CN_API_KEY，标注步骤会失败", file=sys.stderr)

    date = args.date
    if not date:
        import datetime
        if args.yesterday:
            date = (datetime.datetime.now()
                    - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        else:
            date = datetime.datetime.now().strftime("%Y-%m-%d")

    os.makedirs(DATA, exist_ok=True)
    fetch_ok = True

    if not args.skip_fetch:
        import sources
        print(f"[1/3] 抓取 {date} ...", flush=True)
        try:
            arts = sources.fetch_all(date)
        except Exception as e:
            print(f"!! 抓取失败: {e}", file=sys.stderr)
            arts = []
            fetch_ok = False
        raw_path = os.path.join(DATA, f"raw-{date}.json")
        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump(arts, f, ensure_ascii=False, indent=1)
        print(f"    抓到 {len(arts)} 篇 → {raw_path}")
        # 抓取为 0 篇视为失败（源改版 / 网络问题），不进入标注
        if len(arts) < 5:
            print(f"!! 抓取篇数过少（{len(arts)} < 5），判定为失败，终止", file=sys.stderr)
            return 2

    if not args.skip_annotate:
        print(f"[2/3] LLM 标注 {date} ...", flush=True)
        rc = run([sys.executable, os.path.join(SCRIPTS, "annotate.py"),
                  "--date", date])
        if rc != 0:
            print(f"!! 标注步骤失败（退出码 {rc}）", file=sys.stderr)
            return 3
        # 校验标注产出
        out_path = os.path.join(DATA, f"annotated-{date}.json")
        try:
            with open(out_path, encoding="utf-8") as f:
                n = len(json.load(f))
        except Exception as e:
            print(f"!! 标注结果读取失败: {e}", file=sys.stderr)
            return 4
        if n < 5:
            print(f"!! 标注成功篇数过少（{n} < 5），判定为失败", file=sys.stderr)
            return 5

    print(f"[3/3] 重建索引 ...", flush=True)
    run([sys.executable, os.path.join(SCRIPTS, "build_index.py")])

    print("\n完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
