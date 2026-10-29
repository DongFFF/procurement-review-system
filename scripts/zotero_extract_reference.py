"""
从「参照文档」中提取 Zotero 引文字段的真实结构。

## 为什么需要这个工具

要程序化生成 Zotero 引文字段，必须知道两类信息：
  1. 字段的 XML 形态（begin / instrText / separate / 结果 / end）
  2. 若干**无法从公开文档查到**的本机参数：条目 URI 里的 userID、
     文档偏好的实际格式（XML 还是 JSON）、CSL itemData 的字段约定等

这两类信息在官方文档和网络教程里都不完整，且随 Zotero 版本变化。
唯一可靠的做法是：找一份**由 Zotero Word 插件真实生成过引文**的 docx，
从里面把结构原样读出来，再据此复刻。

本工具就是把这个"读出来"的动作自动化，输出一份可直接对照的参数清单。

## 如何获得参照文档

任意一个已插入过 Zotero 引文的 docx 都可以：

    Word 打开新文档 → Zotero 工具栏 → Add/Edit Citation
    → 随便选一条文献插入 → 保存为 .docx

若本机已有既往论文（插入过引文），直接用那些文件即可，无需新建。

## 用法

    # 指定参照文档
    python scripts/zotero_extract_reference.py "C:\\path\\to\\文献插入参考.docx"

    # 不给路径时，自动在常见目录里搜索含 Zotero 字段的 docx
    python scripts/zotero_extract_reference.py

    # 输出 JSON 参数清单，便于程序读取
    python scripts/zotero_extract_reference.py --json reports/zotero_profile.json

输出内容即 docs/文献插入工作流.md 中"关键参数"一节的实测来源。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]

# 自动搜索参照文档的常见位置
SEARCH_ROOTS = [
    Path.home() / "Desktop",
    Path.home() / "Documents",
    Path.home() / "Downloads",
]
SEARCH_NAMES = ["文献插入参考.docx"]

ITEM_RE = re.compile(r"ADDIN ZOTERO_ITEM CSL_CITATION (\{.*?\})\s*</w:instrText>", re.S)
# 注意：ZOTERO_BIBL 的 JSON 之后还跟着 CSL_BIBLIOGRAPHY 关键字，
# 不能要求 JSON 紧跟 </w:instrText>，否则永远匹配不到。
BIBL_RE = re.compile(r"ADDIN ZOTERO_BIBL\s*(\{.*?\})[^<]*</w:instrText>", re.S)
URI_RE = re.compile(r"http://zotero\.org/users/(\d+)/items/([A-Z0-9]{8})")


def unescape_xml(s: str) -> str:
    return (
        s.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
        .replace("&amp;", "&")
    )


def find_reference_doc(explicit: str | None) -> Path | None:
    if explicit:
        p = Path(explicit)
        return p if p.exists() else None

    # 优先按已知文件名找
    for root in SEARCH_ROOTS:
        if not root.exists():
            continue
        for name in SEARCH_NAMES:
            for hit in root.rglob(name):
                return hit

    # 退而求其次：找任意含 Zotero 字段的 docx
    for root in SEARCH_ROOTS:
        if not root.exists():
            continue
        for f in root.rglob("*.docx"):
            try:
                with zipfile.ZipFile(f) as z:
                    if "word/document.xml" not in z.namelist():
                        continue
                    xml = z.read("word/document.xml").decode("utf-8", "ignore")
                if "ADDIN ZOTERO_ITEM" in xml:
                    return f
            except (zipfile.BadZipFile, OSError):
                continue
    return None


def extract(path: Path) -> dict:
    """提取参照文档的字段结构与关键参数。"""
    out: dict = {"source": str(path), "ok": False}

    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        doc = z.read("word/document.xml").decode("utf-8", "ignore")
        custom = (
            z.read("docProps/custom.xml").decode("utf-8", "ignore")
            if "docProps/custom.xml" in names
            else ""
        )

    # --- 引文字段 ---
    items = ITEM_RE.findall(doc)
    out["item_field_count"] = len(items)
    if items:
        try:
            cit = json.loads(items[0])
        except json.JSONDecodeError:
            cit = {}
        out["citation_top_keys"] = list(cit.keys())
        out["properties_example"] = cit.get("properties", {})

        cis = cit.get("citationItems", [])
        if cis:
            ci = cis[0]
            out["citation_item_keys"] = list(ci.keys())
            out["item_data_keys"] = list(ci.get("itemData", {}).keys())

            uris = ci.get("uris", [])
            out["uri_example"] = uris[0] if uris else ""
            m = URI_RE.search(uris[0]) if uris else None
            if m:
                out["user_id"] = m.group(1)

            idata = ci.get("itemData", {})
            # 记录几个易错字段的实际形态
            out["issued_shape"] = idata.get("issued")
            authors = idata.get("author", [])
            out["author_shape"] = authors[0] if authors else None
            out["type_example"] = idata.get("type")

    # --- 参考文献字段 ---
    bibls = BIBL_RE.findall(doc)
    out["bibl_field_count"] = len(bibls)
    if bibls:
        out["bibl_params"] = bibls[0]

    # --- 文档偏好 ---
    prefs_match = re.search(
        r'name="ZOTERO_PREF_1"[^>]*>(.*?)</vt:', custom, re.S
    )
    if prefs_match:
        raw = unescape_xml(prefs_match.group(1))
        raw = re.sub(r"^<vt:lpwstr>|</vt:lpwstr>$", "", raw).strip()
        out["pref_present"] = True
        out["pref_format"] = "xml" if raw.lstrip().startswith("<data") else "json"
        out["pref_raw_head"] = raw[:300]
        style = re.search(r'<style id="([^"]+)"([^/]*)/>', raw)
        if style:
            out["style_id"] = style.group(1)
            loc = re.search(r'locale="([^"]+)"', style.group(2))
            out["style_locale"] = loc.group(1) if loc else ""
        ver = re.search(r'zotero-version="([^"]+)"', raw)
        if ver:
            out["zotero_version"] = ver.group(1)
    else:
        out["pref_present"] = False

    # --- 字段计数一致性 ---
    out["fldChar_begin"] = doc.count('w:fldCharType="begin"')
    out["fldChar_end"] = doc.count('w:fldCharType="end"')
    out["ok"] = bool(items)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="从参照文档提取 Zotero 字段结构")
    ap.add_argument("docx", nargs="?", help="参照文档路径（省略则自动搜索）")
    ap.add_argument("--json", dest="json_out", help="把参数清单写入 JSON 文件")
    args = ap.parse_args()

    print("=" * 78)
    print("从参照文档提取 Zotero 字段结构")
    print("=" * 78)

    path = find_reference_doc(args.docx)
    if path is None:
        print("未找到参照文档。请先生成一个：")
        print("  Word 新建文档 → Zotero 工具栏 → Add/Edit Citation → 插入一条 → 保存")
        print("然后用本脚本指定该文件路径。")
        return 1

    print(f"参照文档：{path}\n")
    info = extract(path)

    if not info.get("ok"):
        print("该文档不含 ZOTERO_ITEM 字段，不能作为参照。")
        return 1

    def show(label: str, key: str, note: str = ""):
        v = info.get(key)
        s = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)
        if len(s) > 150:
            s = s[:150] + "…"
        print(f"  {label:<22} {s}")
        if note:
            print(f"  {'':<22} └─ {note}")

    print("【1】字段数量")
    show("引文字段", "item_field_count")
    show("参考文献字段", "bibl_field_count")
    show("fldChar begin/end", "fldChar_begin")
    print(f"  {'':<22} └─ end={info.get('fldChar_end')}（begin 应等于 end）")

    print("\n【2】条目 URI 与账户")
    show("URI 示例", "uri_example", "形如 http://zotero.org/users/<UID>/items/<KEY>")
    show("userID", "user_id", "★ 本地库常见为具体数字，不是 0")

    print("\n【3】CSL itemData 约定")
    show("顶层键", "citation_top_keys")
    show("citationItem 键", "citation_item_keys")
    show("itemData 键", "item_data_keys")
    show("type 取值", "type_example")
    show("issued 形态", "issued_shape", "★ 年份通常是字符串")
    show("author 形态", "author_shape", "★ 中文作者用 literal，不拆姓/名")
    show("properties 示例", "properties_example")

    print("\n【4】文档偏好（决定 Word 能否识别）")
    show("是否存在", "pref_present")
    show("格式", "pref_format", "★ 必须是 xml；写成 json 会导致 Word 报文件损坏")
    show("样式 ID", "style_id")
    show("语言区域", "style_locale")
    show("Zotero 版本", "zotero_version")
    if info.get("pref_raw_head"):
        print(f"  原文开头                {info['pref_raw_head'][:110]}…")

    print("\n【5】参考文献字段参数")
    show("ZOTERO_BIBL", "bibl_params")

    print("\n" + "=" * 78)
    print("以上参数可直接用于 scripts/zotero_fields.py 构造字段。")
    print("详细说明见 docs/文献插入工作流.md")
    print("=" * 78)

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n参数清单已写入：{out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
