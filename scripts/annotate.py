#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
申论素材「三色笔记」自动标注
================================

对抓取到的文章调用 MiniMax 大模型，输出结构化的三色笔记：
  - outline：总论点 thesis / 论点 points / 案例 cases
  - tags   ：湖南独有 hunan / 可迁移全国 transferable / 湖南数据 data
  - terms  ：规范词（政策术语、动宾短语、四字规范表述）
  - angle  ：申论适用角度

用法：
  python3 scripts/annotate.py --date 2026-11-05
  python3 scripts/annotate.py --date 2026-11-05 --input data/raw-2026-11-05.json
  python3 scripts/annotate.py --date 2026-11-05 --out data/annotated-2026-11-05.json

只依赖标准库；API key 只从环境变量或 /root/.hermes/.env 读，不落盘。
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sources  # noqa: E402

CST = timezone(timedelta(hours=8))

API_URL = "https://api.minimaxi.com/anthropic/v1/messages"
MODEL = "MiniMax-M3.1-Flash"
MAX_TOKENS = 8000
MAX_WORKERS = 3          # 并发上限，避免触发限流
CHUNK_CHARS = 6000      # 单次请求的正文长度上限
CHUNK_PARAS = 12        # 单次请求的段落数上限
TIMEOUT = 180

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT_DIR, "data")

SCENE_OPTIONS = ("对策题", "大作文", "标题", "过渡句")


# ---------------------------------------------------------------- API key

def read_api_key():
    """先读环境变量，兜底再解析 /root/.hermes/.env 里的同名配置。"""
    key = os.environ.get("MINIMAX_CN_API_KEY", "").strip()
    if key:
        return key
    env_path = os.environ.get("HERMES_ENV_FILE", "/root/.hermes/.env")
    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("MINIMAX_CN_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


# ---------------------------------------------------------------- 调用大模型

class ApiLimitError(Exception):
    """429 限额。"""


class ApiError(Exception):
    """其它接口错误。"""


def call_llm(prompt, max_tokens=MAX_TOKENS):
    """调一次 MiniMax，返回正文文本。thinking 块会被忽略。"""
    key = read_api_key()
    if not key:
        raise ApiError("缺少 MINIMAX_CN_API_KEY")
    payload = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "ignore")[:300]
        except Exception:
            pass
        if exc.code == 429:
            raise ApiLimitError(f"429 限额: {detail}") from exc
        raise ApiError(f"HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        raise ApiError(f"请求失败: {exc}") from exc

    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ApiError(f"响应不是合法 JSON: {raw[:200]}") from exc

    text, thinking = "", ""
    for block in data.get("content") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text += block.get("text") or ""
        elif block.get("type") == "thinking":
            thinking += block.get("thinking") or block.get("text") or ""
    if not text.strip() and thinking.strip():
        text = thinking
    return text


def extract_json(text):
    """从模型输出里抠出 JSON：剥 ```json 包裹，取第一个 { 到最后一个 }。"""
    if not text:
        raise ApiError("模型返回为空")
    t = text.strip()
    if "```" in t:
        t = re.sub(r"```[a-zA-Z]*", "", t)
        t = t.replace("```", "")
    t = t.strip()
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ApiError(f"输出里找不到 JSON: {text[:200]}")
    try:
        return json.loads(t[start:end + 1])
    except ValueError as exc:
        raise ApiError(f"JSON 解析失败: {exc}") from exc


# ---------------------------------------------------------------- Prompt

OUTPUT_SCHEMA = """{
  "thesis": "总论点，一句话，40字以内",
  "thesis_para": 0,
  "points": [{"title": "论点小标题，12字内", "summary": "该论点一句话概括，50字内", "paras": [0, 1]}],
  "cases": [{"title": "案例标题，14字内", "summary": "案例一句话，60字内", "paras": [2, 3]}],
  "quote_paras": [5],
  "tags": {
    "hunan": ["湖南独有提法或做法，10字内，最多6个"],
    "transferable": ["可迁移到全国的通用观点，14字内，最多6个"],
    "data": ["文中出现的湖南数据/成效，写成'500万元'这种短句，最多6个"]
  },
  "terms": ["规范词，最多10个"],
  "term_details": [{"term": "与terms完全一致的词", "definition": "该词在本篇语境下的含义，30字内", "scene": "对策题、大作文、标题、过渡句（多选用顿号分隔）"}],
  "angle": "申论适用角度：一句话说明这篇能怎么用，60字内"
}"""

ANNOTATE_RULES = """【标注规则】
1. thesis：全文总论点，一句话，40字以内。
2. thesis_para：最能代表总论点的那一段的编号（0 起，只能取上面给出的编号）。
3. points：2-5 个论点。每个含 title（论点小标题，12字内）、summary（该论点一句话概括，50字内）、
   paras（支撑该论点的段落编号数组）。
4. cases：0-3 个案例。每个含 title（案例标题，14字内）、summary（案例一句话，60字内）、
   paras（案例涉及的段落编号数组）。
5. quote_paras：权威引语或金句所在的段落编号数组，没有就给空数组。
6. tags：三色标签
   - hunan：湖南独有的提法/做法/数据，10字以内，最多6个。只有湖南才有的才算，
     例如"三个高地""中部崛起""长株潭一体化""洞庭湖生态经济带""湘超""中非经贸深度合作先行区"。
   - transferable：可迁移到全国的通用观点，14字以内，最多6个。任何省都能写，
     例如"破除体制机制障碍""打通要素流动""营造创新生态""完善顶层设计"。
   - data：文中具体的湖南数据或成效，写成"500万元"这样的短句，最多6个。
7. terms：本文涉及的规范词，最多10个。判定标准：
   - 政策术语、动宾短语、四字规范表述，例如"压实责任""靶向发力""破题开路""提质增效"
     "系统集成""协同发力""提标扩面""向新向实"；
   - 湖南专属提法，例如"三个高地""中部崛起""长株潭一体化""守护好一江碧水"；
   - 权威引用的固定表述，例如"把方向搞准""干字当头"；
   - 排除：普通动词、普通形容词、专有名词（如"湖南日报""习近平总书记"）、纯数字。
8. term_details：与 terms 一一对应，每项含 term（必须与 terms 里的词完全一致）、
   definition（该词在本篇语境下的含义，30字以内）、scene（适用场景，多选用顿号"、"分隔，
   可选值：对策题、大作文、标题、过渡句）。
9. angle：一句话说明这篇能怎么用于申论，60字以内。"""


def split_chunks(paragraphs, max_chars=CHUNK_CHARS, max_paras=CHUNK_PARAS):
    """把段落切成若干块，返回 [[(全局段号, 段文本), ...], ...]。"""
    chunks, cur, size = [], [], 0
    for i, text in enumerate(paragraphs):
        if cur and (size + len(text) > max_chars or len(cur) >= max_paras):
            chunks.append(cur)
            cur, size = [], 0
        cur.append((i, text))
        size += len(text)
    if cur:
        chunks.append(cur)
    return chunks or [[]]


def build_prompt(article, chunk, block_no, total):
    """拼一篇文章（或一段文章）的标注 prompt。"""
    lines = []
    lines.append("你是一名申论培训专家，正在为「湖南省考申论」素材库做结构化标注。")
    lines.append("输出会被程序直接解析，所以只输出一个 JSON 对象，"
                 "不要任何解释文字，不要 markdown 代码块，不要前后缀。")
    lines.append("")
    lines.append(f"【文章来源】{article.get('source', '')} "
                 f"{article.get('date', '')} {article.get('column', '')}")
    lines.append(f"【标题】{article.get('title', '')}")
    if article.get("author"):
        lines.append(f"【作者】{article['author']}")
    lines.append("")
    lines.append("【正文段落】（编号从 0 起，引用时只能用下面出现的编号）")
    for idx, text in chunk:
        lines.append(f"{idx}. {text}")
    lines.append("")
    if total > 1:
        lines.append(f"【说明】全文较长，分 {total} 块送入，这是第 {block_no} 块。"
                     "若不是第一块，请只输出 points、cases、quote_paras、terms、"
                     "term_details，其余字段给空字符串或空数组。")
        lines.append("")
    lines.append(ANNOTATE_RULES)
    lines.append("")
    lines.append("【输出 JSON 结构】")
    lines.append(OUTPUT_SCHEMA)
    return "\n".join(lines)


# ---------------------------------------------------------------- 标注主流程

def _as_int_list(value):
    """把模型给的段落编号整理成 int 列表。"""
    out = []
    if isinstance(value, list):
        for v in value:
            try:
                out.append(int(v))
            except (TypeError, ValueError):
                continue
    return out


def _merge_tag(dst, src):
    """标签去重合并，最多保留 6 条。"""
    for item in src if isinstance(src, list) else []:
        item = str(item).strip()
        if item and item not in dst:
            dst.append(item)
    return dst[:6]


def annotate_article(article):
    """标注单篇文章，返回 outline / tags / terms / angle。"""
    paragraphs = article.get("paragraphs") or []
    chunks = split_chunks(paragraphs)
    merged = {
        "thesis": "", "thesis_para": 0,
        "points": [], "cases": [], "quote_paras": [],
        "tags": {"hunan": [], "transferable": [], "data": []},
        "terms": [], "term_details": [], "angle": "",
    }
    for i, chunk in enumerate(chunks):
        prompt = build_prompt(article, chunk, block_no=i + 1,
                              total=len(chunks))
        data = extract_json(call_llm(prompt))
        if not isinstance(data, dict):
            raise ApiError("模型返回的不是 JSON 对象")
        if i == 0:
            merged["thesis"] = str(data.get("thesis") or "").strip()
            try:
                merged["thesis_para"] = int(data.get("thesis_para") or 0)
            except (TypeError, ValueError):
                merged["thesis_para"] = 0
            merged["angle"] = str(data.get("angle") or "").strip()
        for key in ("hunan", "transferable", "data"):
            src = (data.get("tags") or {}).get(key)
            _merge_tag(merged["tags"][key], src)
        for pt in data.get("points") or []:
            if not isinstance(pt, dict):
                continue
            merged["points"].append({
                "title": str(pt.get("title") or "").strip(),
                "summary": str(pt.get("summary") or "").strip(),
                "paras": _as_int_list(pt.get("paras")),
            })
        for cs in data.get("cases") or []:
            if not isinstance(cs, dict):
                continue
            merged["cases"].append({
                "title": str(cs.get("title") or "").strip(),
                "summary": str(cs.get("summary") or "").strip(),
                "paras": _as_int_list(cs.get("paras")),
            })
        for q in _as_int_list(data.get("quote_paras")):
            if q not in merged["quote_paras"]:
                merged["quote_paras"].append(q)
        for t in data.get("terms") or []:
            t = str(t).strip()
            if t and t not in merged["terms"]:
                merged["terms"].append(t)
        for d in data.get("term_details") or []:
            if isinstance(d, dict):
                term = str(d.get("term") or "").strip()
                if term:
                    merged["term_details"].append({
                        "term": term,
                        "definition": str(d.get("definition") or "").strip(),
                        "scene": str(d.get("scene") or "").strip(),
                    })
            elif isinstance(d, str) and d.strip():
                term = d.strip()
                if term and term not in merged["terms"]:
                    merged["terms"].append(term)
    merged["terms"] = merged["terms"][:10]
    return merged


def is_authority_quote(text):
    """含引号且像权威引语。"""
    if not re.search(r"[“”\"「」]", text):
        return False
    return bool(re.search(
        r"强调|指出|要求|告诫|明确提出|谆谆嘱托|殷殷嘱托|总书记|讲话", text))


def classify_paragraphs(paragraphs, outline):
    """按段落编号回填 kind：point 优先于 case，再 quote、thesis、plain。"""
    point_of, case_of = {}, {}
    for i, pt in enumerate(outline.get("points") or []):
        for idx in pt.get("paras") or []:
            point_of.setdefault(idx, i + 1)
    for i, cs in enumerate(outline.get("cases") or []):
        for idx in cs.get("paras") or []:
            case_of.setdefault(idx, i + 1)
    quotes = set(outline.get("quote_paras") or [])
    thesis_para = outline.get("thesis_para", 0)
    out = []
    for i, text in enumerate(paragraphs):
        kind = "plain"
        point = ""
        if i in point_of:
            kind, point = "point", f"p{point_of[i]}"
        elif i in case_of:
            kind = "case"
        elif (text.startswith("习近平总书记强调") or is_authority_quote(text)
                or i in quotes):
            kind = "quote"
        elif i == 0 or i == thesis_para:
            kind = "thesis"
        item = {"index": i, "text": text, "kind": kind}
        if point:
            item["point"] = point
        out.append(item)
    return out


def build_record(article, result):
    """组装单篇文章的最终结构。"""
    outline = {
        "thesis": result.get("thesis", ""),
        "points": result.get("points", []),
        "cases": result.get("cases", []),
        "quote_paras": result.get("quote_paras", []),
        "thesis_para": result.get("thesis_para", 0),
    }
    return {
        "id": article.get("id", ""),
        "source": article.get("source", ""),
        "column": article.get("column", ""),
        "date": article.get("date", ""),
        "url": article.get("url", ""),
        "title": article.get("title", ""),
        "author": article.get("author", ""),
        "outline": outline,
        "tags": result.get("tags", {"hunan": [], "transferable": [], "data": []}),
        "terms": result.get("terms", []),
        "angle": result.get("angle", ""),
        "paragraphs": classify_paragraphs(article.get("paragraphs") or [], outline),
    }


# ---------------------------------------------------------------- 词库

def normalize_scene(scene):
    """场景标签统一用顿号分隔。"""
    scene = re.sub(r"[|,，、]", "、", str(scene or ""))
    scene = re.sub(r"、+", "、", scene).strip("、 ")
    parts = [p for p in scene.split("、") if p in SCENE_OPTIONS]
    return "、".join(parts)


def build_term_batch(records, date_str):
    """生成当日规范词增量。"""
    seen, rows = set(), []
    for rec in records:
        source = " ".join(x for x in (rec.get("source", ""), date_str,
                                      rec.get("column", "")) if x)
        details = rec.get("_term_details") or []
        for d in details:
            term = (d.get("term") or "").strip()
            if not term or term in seen:
                continue
            seen.add(term)
            rows.append({
                "term": term,
                "definition": (d.get("definition") or "").strip()[:40],
                "scene": normalize_scene(d.get("scene")),
                "source": source,
                "date": date_str,
            })
    return {"terms": rows}


def update_global_terms(global_path, batch_rows, date_str):
    """把当日新词合并进全局词库：老词保留 definition、累加 count，新词 count=1。"""
    old = {"terms": []}
    if os.path.exists(global_path):
        try:
            with open(global_path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict) and isinstance(loaded.get("terms"), list):
                old = loaded
            elif isinstance(loaded, list):
                old = {"terms": loaded}
        except (OSError, ValueError):
            old = {"terms": []}

    index = {}
    for item in old.get("terms") or []:
        if not isinstance(item, dict):
            continue
        term = (item.get("term") or "").strip()
        if term:
            index[term] = item

    for row in batch_rows:
        term = row.get("term")
        if not term:
            continue
        cur = index.get(term)
        if cur is None:
            index[term] = {
                "term": term,
                "definition": row.get("definition", ""),
                "scene": row.get("scene", ""),
                "source": row.get("source", ""),
                "date": row.get("date", date_str),
                "last_seen": date_str,
                "count": 1,
            }
        else:
            cur["count"] = int(cur.get("count", 1)) + 1
            cur["last_seen"] = date_str
            if not cur.get("definition"):
                cur["definition"] = row.get("definition", "")
            if not cur.get("scene"):
                cur["scene"] = row.get("scene", "")

    merged = list(index.values())
    # count 降序；同频按 term 字序（纯标准库没有拼音库，用码位序近似）
    merged.sort(key=lambda x: (-int(x.get("count", 1)), str(x.get("term", ""))))
    with open(global_path, "w", encoding="utf-8") as f:
        json.dump({"terms": merged}, f, ensure_ascii=False, indent=2)
    return merged


# ---------------------------------------------------------------- 主流程

def load_articles(input_path, date_str):
    """读入文章；没有 --input 就现场抓一份并落到 data/raw-<date>.json。"""
    if input_path:
        with open(input_path, encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            loaded = loaded.get("articles", [])
        return loaded if isinstance(loaded, list) else []
    articles = sources.fetch_all(date_str)
    os.makedirs(DATA_DIR, exist_ok=True)
    raw_path = os.path.join(DATA_DIR, f"raw-{date_str}.json")
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(articles, f, ensure_ascii=False, indent=2)
    print(f"已抓取 {len(articles)} 篇 -> {raw_path}", file=sys.stderr)
    return articles


def main():
    ap = argparse.ArgumentParser(description="申论素材三色笔记自动标注")
    ap.add_argument("--date", required=True, help="日期，格式 2026-11-05")
    ap.add_argument("--input", help="原文 JSON，默认现场抓取并写 data/raw-<date>.json")
    ap.add_argument("--out", help="标注结果 JSON，默认 data/annotated-<date>.json")
    args = ap.parse_args()

    date_str = args.date
    out_path = args.out or os.path.join(DATA_DIR, f"annotated-{date_str}.json")
    batch_path = os.path.join(DATA_DIR, f"terms-{date_str}.json")
    global_path = os.path.join(DATA_DIR, "terms.json")

    articles = load_articles(args.input, date_str)
    if not articles:
        print("没有可标注的文章", file=sys.stderr)
        return 1

    results, failures = [], []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(annotate_article, a): a for a in articles}
        for fut in as_completed(futures):
            art = futures[fut]
            try:
                result = fut.result()
            except Exception as exc:
                failures.append({
                    "id": art.get("id", ""),
                    "title": art.get("title", ""),
                    "url": art.get("url", ""),
                    "error": f"{type(exc).__name__}: {exc}",
                })
                print(f"[失败] {art.get('title', '')[:30]} -> {exc}",
                      file=sys.stderr)
                continue
            record = build_record(art, result)
            record["_term_details"] = result.get("term_details", [])
            results.append(record)

    results.sort(key=lambda r: (r.get("source", ""), r.get("column", ""),
                                r.get("title", "")))
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    batch_rows = build_term_batch(results, date_str)
    with open(batch_path, "w", encoding="utf-8") as f:
        json.dump({"terms": batch_rows}, f, ensure_ascii=False, indent=2)

    merged = update_global_terms(global_path, batch_rows, date_str)

    print(f"总文章 {len(articles)} 篇，成功 {len(results)} 篇，"
          f"失败 {len(failures)} 篇", file=sys.stderr)
    print(f"标注结果 -> {out_path}", file=sys.stderr)
    print(f"当日词增量 {len(batch_rows)} 条 -> {batch_path}", file=sys.stderr)
    print(f"全局词库 {len(merged)} 条 -> {global_path}", file=sys.stderr)
    if failures:
        err_path = os.path.join(DATA_DIR, f"annotated-{date_str}.errors.json")
        with open(err_path, "w", encoding="utf-8") as f:
            json.dump(failures, f, ensure_ascii=False, indent=2)
        print(f"失败明细 -> {err_path}", file=sys.stderr)
        for item in failures:
            print(f"  - {item['title'][:30]} | {item['error']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
