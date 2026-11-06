# -*- coding: utf-8 -*-
"""申论规范词基础库（种子）——转换为全局词库 data/terms.json"""

import json
import os

SEED = os.path.join(os.path.dirname(__file__), "..", "data", "terms_seed.json")
OUT = os.path.join(os.path.dirname(__file__), "..", "data", "terms.json")


def load_seed():
    with open(SEED, encoding="utf-8") as f:
        return json.load(f)


def main():
    seed = load_seed()
    terms = seed.get("terms", [])
    for t in terms:
        t.setdefault("count", 1)
        t.setdefault("last_seen", t.get("date", seed.get("updated", "")))
        t.pop("count_note", None)

    out = {
        "version": seed.get("version", 1),
        "updated": seed.get("updated", ""),
        "note": seed.get("note", ""),
        "terms": terms,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"已生成 {OUT}，共 {len(terms)} 个规范词")


if __name__ == "__main__":
    main()
