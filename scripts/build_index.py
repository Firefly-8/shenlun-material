# -*- coding: utf-8 -*-
"""构建站点索引：扫描 data/ 目录，生成 index.json（前端唯一真相源）

规范词库合并策略：
  1. data/terms_seed.json      —— 预置基础库（122 条打底）
  2. data/terms-<date>.json    —— 每日从文章抽取的新词
  3. data/terms.json           —— 合并后的全局词库（由本脚本写出，供前端与后续增量合并）

去重键：term 文本。合并时保留 count 累加、last_seen 取最新、definition 取非空者。
"""

import glob
import json
import os
from collections import OrderedDict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "data")
OUT = os.path.join(BASE, "index.json")
GLOBAL_TERMS = os.path.join(DATA, "terms.json")


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def merge_terms(seed_terms, daily_terms):
    """合并基础库与每日新词。同名 term 累加 count，更新 last_seen。"""
    merged = OrderedDict()

    for t in seed_terms or []:
        term = (t.get("term") or "").strip()
        if not term:
            continue
        item = dict(t)
        item.setdefault("count", 1)
        item.setdefault("last_seen", item.get("date", ""))
        merged[term] = item

    for t in daily_terms or []:
        term = (t.get("term") or "").strip()
        if not term:
            continue
        if term in merged:
            old = merged[term]
            old["count"] = old.get("count", 1) + t.get("count", 1)
            if t.get("last_seen") or t.get("date"):
                old["last_seen"] = max(old.get("last_seen", ""), t.get("last_seen") or t.get("date", ""))
            if t.get("definition") and not old.get("definition"):
                old["definition"] = t["definition"]
            if t.get("source") and t.get("date") and t["date"] > old.get("date", ""):
                old["source"] = t["source"]
                old["date"] = t["date"]
            old.pop("count_note", None)
        else:
            item = dict(t)
            item.setdefault("count", 1)
            item.setdefault("last_seen", item.get("date", ""))
            merged[term] = item

    return list(merged.values())


def collect_terms():
    """读 seed + 所有每日 terms-*.json，返回合并后的词条列表。"""
    seed = load_json(os.path.join(DATA, "terms_seed.json")) or {}
    seed_terms = seed.get("terms", [])

    daily_terms = []
    for path in sorted(glob.glob(os.path.join(DATA, "terms-*.json"))):
        data = load_json(path)
        if not data:
            continue
        if isinstance(data, list):
            daily_terms.extend(data)
        elif isinstance(data, dict):
            inner = data.get("terms", [])
            if isinstance(inner, list):
                daily_terms.extend(inner)

    return merge_terms(seed_terms, daily_terms), seed


def main():
    index = {
        "days": [],
        "articles": [],
        "terms": [],
        "stats": {},
    }

    # 1. 每日素材
    for path in sorted(glob.glob(os.path.join(DATA, "annotated-*.json"))):
        day = os.path.basename(path)[len("annotated-"):-len(".json")]
        data = load_json(path)
        if not data:
            continue
        articles = data if isinstance(data, list) else data.get("articles", [])
        index["days"].append({
            "date": day,
            "count": len(articles),
        })
        index["articles"].extend(articles)

    # 2. 规范词库（seed + 每日新词合并）
    all_terms, seed_meta = collect_terms()
    index["terms"] = all_terms

    with open(GLOBAL_TERMS, "w", encoding="utf-8") as f:
        json.dump({
            "version": seed_meta.get("version", 1),
            "updated": seed_meta.get("updated", ""),
            "note": seed_meta.get("note", ""),
            "terms": all_terms,
        }, f, ensure_ascii=False, indent=2)

    # 3. 统计
    by_source = {}
    by_column = {}
    for a in index["articles"]:
        s = a.get("source", "未知")
        by_source[s] = by_source.get(s, 0) + 1
        c = a.get("column", "")
        if c:
            by_column[c] = by_column.get(c, 0) + 1

    hunan_tags = set()
    for a in index["articles"]:
        for t in (a.get("tags") or {}).get("hunan", []):
            hunan_tags.add(t)

    index["stats"] = {
        "total_articles": len(index["articles"]),
        "total_days": len(index["days"]),
        "total_terms": len(index["terms"]),
        "by_source": by_source,
        "by_column": by_column,
        "hunan_tags": sorted(hunan_tags),
    }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=1)

    print(f"index.json 已生成：{len(index['articles'])} 篇文章 / "
          f"{len(index['days'])} 天 / {len(index['terms'])} 个规范词")


if __name__ == "__main__":
    main()
