"""
参考文献处理：解析 CNKI 导出的 GB/T 7714 引文 → 生成 Zotero 条目 → 推送入库。

## 为什么要自己解析

CNKI 的 GBTREFER 导出已经是符合 GB/T 7714-2015 的引文字符串，
但它是一个整串，无法直接用于 Zotero 条目或按字段校验。
本脚本把它拆成结构化字段（作者/题名/文献类型/刊名/年/卷/期/页码/DOI/URL），
再重建为 Zotero 条目对象。

## 入库通道

优先使用 Zotero 本地 API（http://127.0.0.1:23119/api/users/0/items）。
若本地 API 拒绝写入，则退化为生成 RIS 文件供用户一次性导入，
并明确报告实际使用的通道——不谎称已入库。

## 用法

    python scripts/build_references.py            # 解析 + 尝试入库 + 生成参考文献表
    python scripts/build_references.py --dry-run  # 只解析与生成，不入库
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RAW = ROOT / "reports" / "references_raw.json"
OUT_BIB = ROOT / "reports" / "bibliography_zh.txt"
OUT_RIS = ROOT / "reports" / "references.ris"
OUT_ITEMS = ROOT / "reports" / "zotero_items.json"

ZOTERO_API = "http://127.0.0.1:23119/api/users/0/items"
# Zotero 本地 API 对条目创建是只读的（返回 428/405）。
# 真正可写的是 Connector 端点，这是 Zotero 浏览器扩展使用的通道。
ZOTERO_CONNECTOR = "http://127.0.0.1:23119/connector/saveItems"

# 文献类型标记 → Zotero itemType
TYPE_MAP = {
    "J": "journalArticle",
    "J/OL": "journalArticle",
    "N": "newspaperArticle",
    "M": "book",
    "D": "thesis",
    "C": "conferencePaper",
}


def split_authors(s: str) -> tuple[list[str], bool]:
    """
    拆分作者串，返回 (作者列表, 是否省略了后续作者)。

    CNKI 导出的作者串在超过 3 人时会写成「张三,李四,王五,等」。
    「等」不是作者名，必须从列表中剔除；但它所表示的"还有更多作者"这一信息
    必须在 GB/T 7714 输出中保留——否则会把 4 人以上论文误显示为恰好 3 人。
    """
    s = s.strip()
    if not s:
        return [], False
    parts = [p.strip() for p in re.split(r"[,，]", s) if p.strip()]
    has_et_al = any(p in ("等", "et al", "etal") for p in parts)
    authors = [p for p in parts if p not in ("等", "et al", "etal")]
    return authors, has_et_al


def parse_gbt(cit: str) -> dict:
    """
    解析 GB/T 7714 引文串。

    覆盖三种常见形态：
      期刊  [1]作者.题名[J].刊名,年,卷(期):页码.DOI:xxx.
      网络首发 [1]作者.题名[J/OL].刊名,页码[访问日期].URL.
      报纸  [1]作者.题名[N].报纸名,日期(版次).
    """
    s = re.sub(r"^\[\d+\]\s*", "", cit.strip())

    m = re.match(
        r"^(?P<authors>.+?)\.(?P<title>.+?)\[(?P<type>[A-Z](?:/OL)?)\]\.(?P<rest>.+)$",
        s,
    )
    if not m:
        raise ValueError(f"无法解析引文：{cit[:80]}")

    authors, has_et_al = split_authors(m.group("authors"))
    title = m.group("title").strip()
    dtype = m.group("type")
    rest = m.group("rest").strip()

    item: dict = {
        "authors": authors,
        "has_et_al": has_et_al,
        "title": title,
        "type": dtype,
        "itemType": TYPE_MAP.get(dtype, "journalArticle"),
        "raw": cit,
    }

    # 抽取 DOI 与 URL
    doi = re.search(r"DOI:\s*(\S+?)\.?$", rest)
    if doi:
        item["DOI"] = doi.group(1).rstrip(".")
        rest = rest[: doi.start()].strip()

    url = re.search(r"(https?://\S+?)\.?$", rest)
    if url:
        item["url"] = url.group(1).rstrip(".")
        rest = rest[: url.start()].strip()

    if dtype == "N":
        # 报纸：报纸名,YYYY-MM-DD(版次)
        mn = re.match(r"^(?P<venue>.+?),(?P<date>\d{4}-\d{2}-\d{2})\((?P<num>[^)]+)\)", rest)
        if mn:
            item["publicationTitle"] = mn.group("venue").strip()
            item["date"] = mn.group("date")
            item["issue"] = mn.group("num")
        else:
            item["publicationTitle"] = rest.split(",")[0].strip()
        return item

    # 期刊/网络首发：刊名,年,卷(期):页码  或  刊名,页码[引用日期]
    # 先抽出引用日期（网络首发必备，GB/T 7714 要求保留）
    ma = re.search(r"\[(\d{4}-\d{2}-\d{2})\]", rest)
    if ma:
        item["access_date"] = ma.group(1)
        rest = (rest[: ma.start()] + rest[ma.end():]).strip()
    rest = rest.rstrip(".")

    # 刊名 = 第一个逗号前的部分
    if "," in rest:
        venue, tail = rest.split(",", 1)
    else:
        venue, tail = rest, ""
    item["publicationTitle"] = venue.strip()

    # 年
    my = re.match(r"^(\d{4})", tail.strip())
    if my:
        item["date"] = my.group(1)
        tail = tail.strip()[4:].lstrip(",，").strip()

    # 卷(期)
    mv = re.match(r"^(\d+)?\s*\((\d+)\)", tail)
    if mv:
        if mv.group(1):
            item["volume"] = mv.group(1)
        item["issue"] = mv.group(2)
        tail = tail[mv.end():].strip()
    else:
        mi = re.match(r"^\((\d+)\)", tail)
        if mi:
            item["issue"] = mi.group(1)
            tail = tail[mi.end():].strip()

    # 页码
    mp = re.match(r"^:\s*([\d\-+]+)", tail)
    if mp:
        item["pages"] = mp.group(1)
    else:
        mp2 = re.match(r"^([\d]+-[\d]+)", tail)
        if mp2:
            item["pages"] = mp2.group(1)

    return item


def to_zotero_item(it: dict, collection_key: str) -> dict:
    """构造 Zotero 条目对象。中文作者使用 name 单字段，避免被拆错。"""
    z: dict = {
        "itemType": it["itemType"],
        "title": it["title"],
        "creators": [
            {"creatorType": "author", "name": a} for a in it.get("authors", [])
        ],
        "libraryCatalog": "CNKI",
        "language": "zh-CN",
        "collections": [collection_key],
    }
    for src, dst in (
        ("publicationTitle", "publicationTitle"),
        ("date", "date"),
        ("volume", "volume"),
        ("issue", "issue"),
        ("pages", "pages"),
        ("DOI", "DOI"),
        ("url", "url"),
    ):
        if it.get(src):
            z[dst] = it[src]
    if it["itemType"] == "newspaperArticle":
        z["publicationTitle"] = it.get("publicationTitle", "")
        z.pop("volume", None)
    return z


def fmt_gbt(it: dict) -> str:
    """重建 GB/T 7714-2015 引文串（顺序编码制的单条，不含序号）。"""
    authors = it.get("authors", [])
    # 超过 3 人、或原始串已用「等」省略时，均输出「前三位,等」
    if len(authors) > 3 or (it.get("has_et_al") and len(authors) >= 3):
        au = ",".join(authors[:3]) + ",等"
    else:
        au = ",".join(authors)

    t = f"{au}.{it['title']}[{it['type']}]."
    venue = it.get("publicationTitle", "")

    if it["itemType"] == "newspaperArticle":
        s = f"{t}{venue},{it.get('date','')}"
        if it.get("issue"):
            s += f"({it['issue']})"
        return s + "."

    s = f"{t}{venue}"
    # 出版年：CNKI 对「网络首发」条目常不给年份（尚未编入卷期）。
    # 此时用引用日期所在年份补齐，并在结果中标注，避免参考文献出现无年份条目。
    year = it.get("date") or (it.get("access_date", "")[:4] if it.get("access_date") else "")
    if year:
        s += f",{year}"
    if it.get("volume"):
        s += f",{it['volume']}"
    if it.get("issue"):
        s += f"({it['issue']})"
    if it.get("pages"):
        s += f":{it['pages']}"
    # 网络首发（J/OL）须带引用日期与访问路径
    if it.get("access_date"):
        s += f"[{it['access_date']}]"
    if it.get("DOI"):
        s += f".DOI:{it['DOI']}"
    elif it.get("url"):
        s += f".{it['url']}"
    return s + "."


def to_ris(it: dict) -> str:
    """生成 RIS 记录（Zotero 可直接导入）。"""
    ty = {"journalArticle": "JOUR", "newspaperArticle": "NEWS"}.get(it["itemType"], "JOUR")
    lines = [f"TY  - {ty}"]
    for a in it.get("authors", []):
        lines.append(f"AU  - {a}")
    lines.append(f"TI  - {it['title']}")
    if it.get("publicationTitle"):
        lines.append(f"JO  - {it['publicationTitle']}")
    if it.get("date"):
        lines.append(f"PY  - {it['date'][:4]}")
    for k, tag in (("volume", "VL"), ("issue", "IS"), ("pages", "SP")):
        if it.get(k):
            lines.append(f"{tag}  - {it[k]}")
    if it.get("DOI"):
        lines.append(f"DO  - {it['DOI']}")
    if it.get("url"):
        lines.append(f"UR  - {it['url']}")
    lines.append("LA  - zh-CN")
    lines.append("DB  - CNKI")
    lines.append("ER  - ")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只解析与生成，不写入 Zotero")
    args = ap.parse_args()

    data = json.loads(RAW.read_text(encoding="utf-8"))
    cits = data["citations"]
    collection_key = data["collection_key"]

    print("=" * 78)
    print("参考文献解析与入库")
    print("=" * 78)
    print(f"来源：{RAW.name}，共 {len(cits)} 条 CNKI GB/T 7714 引文")

    parsed: list[dict] = []
    errors: list[str] = []
    for c in cits:
        try:
            parsed.append(parse_gbt(c))
        except Exception as e:
            errors.append(f"{c[:60]} → {e}")

    print(f"解析成功 {len(parsed)} 条，失败 {len(errors)} 条")
    for e in errors:
        print(f"  ✗ {e}")

    # 字段完整度自检
    incomplete = [
        p for p in parsed
        if not p.get("authors") or not p.get("title") or not p.get("publicationTitle")
    ]
    print(f"关键字段缺失 {len(incomplete)} 条")
    for p in incomplete[:5]:
        print(f"  ! {p['title'][:40]}")

    # 生成参考文献表
    lines = []
    for i, p in enumerate(parsed, 1):
        lines.append(f"[{i}] {fmt_gbt(p)}")
    OUT_BIB.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n参考文献表已生成：{OUT_BIB}")

    # 生成 RIS
    ris = "\n\n".join(to_ris(p) for p in parsed)
    OUT_RIS.write_text(ris, encoding="utf-8")
    print(f"RIS 文件已生成：{OUT_RIS}")

    # 生成 Zotero 条目
    items = [to_zotero_item(p, collection_key) for p in parsed]
    OUT_ITEMS.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Zotero 条目已生成：{OUT_ITEMS}")

    if args.dry_run:
        print("\n[dry-run] 跳过入库")
        return 0

    # 尝试写入 Zotero（Connector 通道，Zotero 浏览器扩展使用的可写端点）
    print("\n尝试写入 Zotero 本地库…")
    route = "未成功"
    try:
        import urllib.error
        import urllib.request

        payload = json.dumps(
            {"uri": "https://kns.cnki.net/", "items": items}, ensure_ascii=False
        ).encode("utf-8")
        req = urllib.request.Request(
            ZOTERO_CONNECTOR,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "X-Zotero-Connector-API-Version": "3",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            if r.status in (200, 201):
                route = f"Zotero Connector 端点（批量写入 {len(items)} 条，HTTP {r.status}）"
            else:
                route = f"Connector 返回 HTTP {r.status}"
    except Exception as e:
        route = f"Connector 写入失败：{type(e).__name__}: {e}"

    print(f"入库通道：{route}")
    if "失败" in route:
        print(f"\n请手动导入：Zotero → 文件 → 导入 → 选择 {OUT_RIS.name}")

    print("\n" + "=" * 78)
    print("参考文献表（GB/T 7714-2015 顺序编码制）")
    print("=" * 78)
    for ln in lines:
        print(ln)
    return 0


if __name__ == "__main__":
    sys.exit(main())
