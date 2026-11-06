# -*- coding: utf-8 -*-
"""
数据源定义 —— 湖南省考申论素材站

三类源：
  1. 湖南日报电子版  —— 理论立意 + 地方案例
  2. 湖南省政府网    —— 政策原文（对策权威依据）
  3. 红网            —— 省级评论观点

每个源实现 fetch(date_str) -> list[Article]
Article 结构统一，方便下游 LLM 标注脚本处理。
"""

import re
import urllib.request
import urllib.error
from urllib.parse import urljoin, urlparse
from datetime import datetime, timedelta, timezone

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

CST = timezone(timedelta(hours=8))


# ---------------------------------------------------------------- 工具

def http_get(url, timeout=25):
    """带 UA 的 GET，返回解码后的 HTML。失败抛异常由调用方处理。"""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    for enc in ("utf-8", "gbk", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


def strip_tags(fragment):
    """把 HTML 片段转成纯文本段落列表。"""
    text = fragment
    text = re.sub(r"(?is)<(script|style|nav|footer|header|aside|form)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
    lines = []
    for line in text.split("\n"):
        line = line.replace("　", " ").strip()
        line = re.sub(r"\s+", " ", line)
        if line:
            lines.append(line)
    return lines


def fetch_date(date_str=None):
    """默认取 '今天'（东八区）。"""
    if not date_str:
        return datetime.now(CST).strftime("%Y-%m-%d")
    return date_str


def ym(date_str):
    """2026-11-05 -> 2026-11"""
    return date_str[:7]


# ---------------------------------------------------------------- 1. 湖南日报电子版

# 版面：node id -> (版名, 是否纳入申论素材)
HN_DAILY_PAGES = {
    201: ("头版", False),
    202: ("要闻", False),
    203: ("要闻", False),
    204: ("理论·新论", True),
    205: ("市州", True),
    206: ("深读", True),
    207: ("金融", False),
    208: ("时事", False),
}

HN_DAILY_BASE = "http://epaper.voc.com.cn/hnrb/html/{ym}/{dd}/node_{node}.htm"

# 图片新闻这类只有图片说明、没有实质论述，直接丢
HN_DAILY_SKIP_TITLES = ("图片新闻", "图片报道")


def _hn_layout(node, date_str):
    url = HN_DAILY_BASE.format(ym=ym(date_str), dd=date_str[8:10], node=node)
    html = http_get(url)
    m = re.search(r"第(\d\d)版：<STRONG>([^<]+)</STRONG>", html)
    page_name = m.group(2).strip() if m else ""
    seg = re.search(r'<ul class="ul02_l">(.*?)</ul>', html, re.S)
    items = []
    if seg:
        pat = r'href=(?:\./)?content_(\d+)\.htm\?div=-1><div[^>]*>(.*?)</div>'
        for cid, inner in re.findall(pat, seg.group(1), re.S):
            title = re.sub(r"(?i)<br\s*/?>", " ", inner)
            title = re.sub(r"<[^>]+>", "", title)
            title = re.sub(r"\s+", " ", title).strip()
            if title:
                items.append((cid, title))
    return page_name, items


def _hn_content(cid, date_str):
    url = (f"http://epaper.voc.com.cn/hnrb/html/{ym(date_str)}/"
           f"{date_str[8:10]}/content_{cid}.htm")
    html = http_get(url)

    def meta(key):
        m = re.search(rf"<founder-{key}>(.*?)</founder-{key}>", html, re.S)
        return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""

    title = meta("title").replace("<BR/>", " ").replace("<br/>", " ")
    title = re.sub(r"\s+", " ", title).strip()
    author = meta("author")
    subtitle = meta("subtitle")
    intro = meta("introtitle")

    seg = re.search(r"(?is)<!--enpcontent-->(.*?)</div>", html)
    body_html = seg.group(1) if seg else ""
    paragraphs = strip_tags(body_html)
    # 去掉重复标题行
    if paragraphs and paragraphs[0].strip() == title:
        paragraphs = paragraphs[1:]
    return {
        "title": title, "author": author, "subtitle": subtitle,
        "intro": intro, "paragraphs": paragraphs,
    }


def fetch_hunan_daily(date_str):
    articles = []
    for node, (page_hint, keep) in HN_DAILY_PAGES.items():
        if not keep:
            continue
        try:
            page_name, items = _hn_layout(node, date_str)
        except Exception:
            continue
        if not items:
            continue
        for cid, title in items:
            try:
                body = _hn_content(cid, date_str)
            except Exception:
                body = {"title": title, "author": "", "subtitle": "",
                        "intro": "", "paragraphs": []}
            if body["title"] in HN_DAILY_SKIP_TITLES:
                continue
            if len(body["paragraphs"]) < 4:
                continue
            articles.append({
                "id": f"hnrb-{date_str}-{cid}",
                "source": "湖南日报",
                "source_code": "hnrb",
                "column": page_name or page_hint,
                "date": date_str,
                "fetch_date": date_str,
                "url": (f"http://epaper.voc.com.cn/hnrb/html/{ym(date_str)}/"
                        f"{date_str[8:10]}/content_{cid}.htm"),
                "title": body["title"] or title,
                "author": body["author"],
                "subtitle": body["subtitle"],
                "intro": body["intro"],
                "paragraphs": body["paragraphs"],
            })
    return articles


# ---------------------------------------------------------------- 2. 湖南省政府网

GOV_INDEX = "http://www.hunan.gov.cn/hnszf/xxgk/xxgk.html"
GOV_RULES = "http://www.hunan.gov.cn/hnszf/xxgk/zfgz/index.html"
GOV_HOME = "http://www.hunan.gov.cn/"

GOV_ENTRY_RE = re.compile(
    r'href="(?P<url>[^"]*t(?P<date>20\d{6})_\d+\.html)"[^>]*>\s*'
    r'(?P<title>[^<]{6,90}?)\s*</a>')


def _gov_collect(url, limit=25):
    html = http_get(url)
    out, seen = [], set()
    for m in GOV_ENTRY_RE.finditer(html):
        d = m.group("date")
        if d < "20260101":
            continue
        title = re.sub(r"\s+", " ", m.group("title")).strip()
        href = m.group("url")
        if href.startswith("/"):
            href = "http://www.hunan.gov.cn" + href
        key = (d, title)
        if key in seen:
            continue
        seen.add(key)
        # date 统一成 YYYY-MM-DD；compact 保留紧凑格式供 14 天窗口比较
        iso = f"{d[0:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else d
        out.append({"date": iso, "compact": d, "title": title, "url": href})
        if len(out) >= limit:
            break
    return out


def _gov_content(url):
    html = http_get(url)
    title = ""
    m = re.search(r"(?is)<title>(.*?)</title>", html)
    if m:
        title = re.sub(r"<[^>]+>", "", m.group(1))
        title = re.sub(r"\s+", " ", title).strip()
        title = re.split(r"-|_|－", title)[0].strip()
    body = ""
    for pat in (r'(?is)<div[^>]*class="[^"]*(?:TRS_Editor|article-content|'
                r'content-article|xl_content)[^"]*"[^>]*>(.*?)</div>\s*</div>',
                r'(?is)<div[^>]*id="(?:content|UCAP-CONTENT|zoom)"[^>]*>(.*?)</div>\s*</div>'):
        mm = re.search(pat, html)
        if mm:
            body = mm.group(1)
            break
    if not body:
        body = html
    paragraphs = strip_tags(body)
    paragraphs = [p for p in paragraphs if len(p) >= 8]
    return {"title": title, "paragraphs": paragraphs}


def fetch_hunan_gov(date_str):
    articles = []
    entries = []
    for idx in (GOV_INDEX, GOV_RULES, GOV_HOME):
        try:
            entries.extend(_gov_collect(idx))
        except Exception:
            continue
    seen = set()
    picked = []
    for e in entries:
        key = e["title"]
        if key in seen:
            continue
        seen.add(key)
        picked.append(e)
    # 只取最近 14 天内的，且优先取"规划/通知/意见/办法"这类有申论价值的
    latest = date_str.replace("-", "")
    oldest = (datetime.strptime(latest, "%Y%m%d")
              - timedelta(days=14)).strftime("%Y%m%d")
    for e in picked[:12]:
        # 超出 14 天窗口的直接跳过
        if e.get("compact") and not (oldest <= e["compact"] <= latest):
            continue
        try:
            body = _gov_content(e["url"])
        except Exception:
            continue
        if len(body["paragraphs"]) < 5:
            continue
        articles.append({
            "id": f"hngov-{date_str}-{e['compact']}-{abs(hash(e['title'])) % 100000}",
            "source": "湖南省人民政府",
            "source_code": "hngov",
            "column": "政策文件",
            "date": e["date"],
            "fetch_date": date_str,
            "url": e["url"],
            "title": body["title"] or e["title"],
            "author": "湖南省人民政府",
            "subtitle": "",
            "intro": "",
            "paragraphs": body["paragraphs"][:80],
        })
    return articles


# ---------------------------------------------------------------- 3. 红网

REDNET_HOME = "https://hn.rednet.cn/"
REDNET_THEORY = "https://theory.rednet.cn/"

# 理论频道：实事求是 / 理论研究 / 时代湘潮
REDNET_CHANNEL_IDS = {
    "实事求是": 8404,
    "理论研究": 8408,
    "时代湘潮": 8415,
}
REDNET_CHANNELS = {
    name: f"https://theory.rednet.cn/channel/{cid}.html"
    for name, cid in REDNET_CHANNEL_IDS.items()
}
REDNET_CHANNEL_NAMES = tuple(REDNET_CHANNEL_IDS)

# 红网页面标题尾部带站点后缀链，如 "标题_经济.民生_湖南频道_红网"
# 红网标题尾部站点/栏目后缀：_红网 / _湖南频道_红网 / _红观湖南 / _实事求是 / _理论研究 ...
REDNET_TITLE_SUFFIX_RE = re.compile(
    r"(?:_(?:红网|湖南频道|红观湖南|理论频道|实事求是|理论研究|时代湘潮|经济\.民生|"
    r"科教\.文卫|观点|要闻|国内|国际|社会|财经|文化|体育|法治|评论|时政))+$")

# 红网噪声标题：天气/演出票务/公告/图解/漫画，正文对申论没价值
REDNET_NOISE_RES = [
    re.compile(r"暴雨|大风|雷阵雨|雨势|气温|天气|寒潮|高温|台风|预警"),
    re.compile(r"门票|开票|票务|演出|赛事直播|演唱会|庙会|灯会|灯光秀"),
    re.compile(r"公告|招标|中标|公示|更正|讣告|启事"),
    re.compile(r"^图解[|｜]"),
    re.compile(r"漫画"),
]

# 正文尾部的版权声明、原文链接、作者行、相关阅读列表
REDNET_TAIL_LINE_RES = [
    re.compile(r"^本文为.*原创文章"),
    re.compile(r"^本文链接[:：]"),
    re.compile(r"^作者[:：]"),
    re.compile(r"^@全省县融"),
]
REDNET_TAIL_INLINE_RES = [
    re.compile(r"本文为[^。]*原创文章[^。]*[。.]?"),
    re.compile(r"本文链接[:：]\s*https?://\S+"),
    re.compile(r"@全省县融\S*"),
]
REDNET_AUTHOR_TAIL_RE = re.compile(r"作者[:：]\s*\S{1,20}\s*$")

# 链接抽取：兼容绝对地址、相对地址、<a> 与 </a> 之间夹嵌套标签的情况
REDNET_A_RE = re.compile(r"(?is)<a\b([^>]*)>(.*?)</a>")
REDNET_HREF_RE = re.compile(
    r"""(?is)(?<![\w-])href\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""")
REDNET_TITLE_ATTR_RE = re.compile(
    r"""(?is)(?<![\w-])title\s*=\s*(?:"([^"]*)"|'([^']*)')""")

REDNET_BODY_RES = [
    r'(?is)<div[^>]*class="[^"]*(?:atc|article-content|news_content|'
    r'content-body|TRS_Editor|art_con|article_con|detail-content)[^"]*"[^>]*>'
    r'(.*?)</div>\s*</div>',
    r'(?is)<div[^>]*id="(?:content|zoom|UCAP-CONTENT|articleContent)"[^>]*>'
    r'(.*?)</div>\s*</div>',
]


def _attr_value(m):
    """取正则里第一个非空分组（兼容单双引号与无引号三种写法）。"""
    for g in m.groups():
        if g is not None:
            return g
    return ""


def _clean_rednet_title(title):
    """去掉红网标题尾部的站点后缀链，如 '_经济.民生_湖南频道_红网'。"""
    if not title:
        return title
    title = title.strip()
    if "_" not in title:
        return title
    cleaned = REDNET_TITLE_SUFFIX_RE.sub("", title).strip()
    if cleaned == title:
        # 没有 '_红网' 结尾时，退化为去掉最后一个 '_' 之后的内容
        cleaned = title.rsplit("_", 1)[0].strip()
    return cleaned or title


def _is_rednet_noise(title):
    """判断红网标题是不是噪声稿（天气/票务/公告/图解/漫画等）。"""
    if not title:
        return True
    return any(rx.search(title) for rx in REDNET_NOISE_RES)


def _clean_rednet_paragraphs(paragraphs):
    """去掉正文尾部的版权声明、原文链接、作者行与以 '|' 开头的相关阅读列表。"""
    out = []
    for p in paragraphs:
        if p.startswith("|"):
            continue
        if any(rx.search(p) for rx in REDNET_TAIL_LINE_RES):
            continue
        out.append(p)
    # 末尾几条可能与正文粘连，再做一次行内清理
    for i in range(max(0, len(out) - 3), len(out)):
        text = out[i]
        for rx in REDNET_TAIL_INLINE_RES:
            text = rx.sub("", text)
        text = REDNET_AUTHOR_TAIL_RE.sub("", text)
        out[i] = text.strip()
    return [p for p in out if p]


def _rednet_articles(url, limit=12, label=""):
    """从红网页面抽文章链接与标题，兼容绝对/相对两种链接形态。"""
    html = http_get(url)
    items, seen = [], set()
    for m in REDNET_A_RE.finditer(html):
        attrs, inner = m.group(1), m.group(2)
        hm = REDNET_HREF_RE.search(attrs)
        if not hm:
            continue
        href = _attr_value(hm).strip()
        if not href or href.lower().startswith(("javascript:", "mailto:", "#")):
            continue
        cands = [urljoin(url, href)]
        if not href.startswith(("http://", "https://", "//", "/")):
            # 形如 2026/11/05/c_123.htm 的相对路径，再补一个站点根目录版本
            alt = urljoin(urljoin(url, "/"), href)
            if alt not in cands:
                cands.append(alt)
        full = cands[0]
        parts = urlparse(full)
        if not parts.netloc.lower().endswith("rednet.cn"):
            continue
        if not re.search(r"\.(?:html?|shtml)$", parts.path, re.I):
            continue
        if re.search(r"/channel/\d+\.html?$", parts.path, re.I):
            continue
        # 标题：优先用锚文本，没有就用 a 的 title 属性
        text = re.sub(r"(?is)<[^>]+>", " ", inner)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) < 8:
            tm = REDNET_TITLE_ATTR_RE.search(attrs)
            if tm:
                text = re.sub(r"\s+", " ", _attr_value(tm)).strip()
        if text.startswith("[") and "]" in text:
            text = text.split("]", 1)[1].strip()
        if len(text) < 8:
            continue
        if full in seen:
            continue
        seen.add(full)
        items.append({"url": full, "title": text, "label": label,
                      "alt": cands[1] if len(cands) > 1 else ""})
        if len(items) >= limit:
            break
    return items


def _rednet_body(html):
    """定位红网正文容器，取不到就退回整页（配合 strip_tags 清导航）。"""
    for pat in REDNET_BODY_RES:
        m = re.search(pat, html)
        if m:
            return m.group(1)
    return html


def _rednet_content(urls):
    """抓红网正文。urls 是候选地址列表，逐个尝试直到拿到够长的正文。"""
    if isinstance(urls, str):
        urls = [urls]
    best_title, best_paras = "", []
    for u in urls:
        try:
            html = http_get(u)
        except Exception:
            continue
        m = re.search(r"(?is)<title>(.*?)</title>", html)
        raw_title = ""
        if m:
            raw_title = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        paras = strip_tags(_rednet_body(html))
        paras = [p for p in paras if len(p) >= 12]
        paras = _clean_rednet_paragraphs(paras)
        if len(paras) > len(best_paras):
            best_title, best_paras = raw_title, paras
        if len(paras) >= 4:
            break
    return {"title": _clean_rednet_title(best_title),
            "raw_title": best_title, "paragraphs": best_paras}


def _rednet_column(url, label, raw_title=""):
    """按频道域名判定栏目；理论频道再按文章标题后缀细分。"""
    host = urlparse(url).netloc.lower()
    if host.startswith("gov."):
        return "政务"
    if host.startswith("theory."):
        for seg in (raw_title or "").split("_")[-3:]:
            for name in REDNET_CHANNEL_NAMES:
                if name in seg:
                    return name
        if label in REDNET_CHANNEL_NAMES:
            return label
        return "理论研究"
    return "湖南新闻"


def fetch_rednet(date_str):
    articles = []
    entries = []
    seen = set()

    def add(items):
        for item in items:
            if item["url"] in seen:
                continue
            seen.add(item["url"])
            entries.append(item)

    # 1) 三个理论频道页；频道页抓不到就退回理论频道首页
    for label, url in REDNET_CHANNELS.items():
        try:
            got = _rednet_articles(url, limit=8, label=label)
        except Exception:
            got = []
        if not got:
            try:
                got = _rednet_articles(REDNET_THEORY, limit=12, label=label)
            except Exception:
                got = []
        add(got)

    # 2) 湖南新闻首页
    try:
        add(_rednet_articles(REDNET_HOME, limit=10, label="湖南新闻"))
    except Exception:
        pass

    for e in entries:
        if _is_rednet_noise(e["title"]):
            continue
        cands = [e["url"]]
        if e.get("alt"):
            cands.append(e["alt"])
        try:
            body = _rednet_content(cands)
        except Exception:
            continue
        if len(body["paragraphs"]) < 4:
            continue
        title = body["title"] or e["title"]
        if _is_rednet_noise(title):
            continue
        articles.append({
            "id": f"rednet-{date_str}-{abs(hash(e['url'])) % 100000}",
            "source": "红网",
            "source_code": "rednet",
            "column": _rednet_column(e["url"], e.get("label", ""),
                                     body["raw_title"]),
            "date": date_str,
            "fetch_date": date_str,
            "url": e["url"],
            "title": title,
            "author": "红网",
            "subtitle": "",
            "intro": "",
            "paragraphs": body["paragraphs"][:60],
        })
        if len(articles) >= 10:
            break
    return articles


# ---------------------------------------------------------------- 汇总

def fetch_all(date_str=None):
    date_str = fetch_date(date_str)
    articles = []
    for fn in (fetch_hunan_daily, fetch_hunan_gov, fetch_rednet):
        try:
            articles.extend(fn(date_str))
        except Exception:
            continue
    return articles


if __name__ == "__main__":
    import json
    import sys

    d = sys.argv[1] if len(sys.argv) > 1 else None
    arts = fetch_all(d)
    print(json.dumps(arts, ensure_ascii=False, indent=2))
    print(f"\n共 {len(arts)} 篇", file=sys.stderr)
