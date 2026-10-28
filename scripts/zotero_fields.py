"""
Zotero 引文字段注入工具。

## 依据

字段结构取自本机已知可用的参照文档（`文献插入参考.docx`，由 Zotero Word 插件真实生成），
而非凭文档推测。提取到的关键事实：

1. 条目 URI 形如 `http://zotero.org/users/10340615/items/{KEY}`，
   其中 10340615 是本机 Zotero 账户的 userID，**不是** 0。
2. `ZOTERO_PREF_1` 自定义属性是 **XML**（`<data data-version="3">…</data>`），
   存放在 `<vt:lpwstr>` 中，**不是** JSON。此前按 JSON 写入导致 Word 判定文件损坏。
3. `issued.date-parts` 中的年份是**字符串**。
4. 中文作者写作 `{"literal": "姓名"}`。
5. 参考文献字段为 `ADDIN ZOTERO_BIBL {"uncited":[],"omitted":[],"custom":[]} CSL_BIBLIOGRAPHY`，
   begin/separate 位于首条文献之前，end 位于末条文献之后，跨越可见的文献段落。

## 设计要点

字段的**结果文本**写入已按 GB/T 7714 格式化好的引文串。
这样即便用户不点 Zotero 刷新，文档也是完整可读的；
点刷新后 Zotero 会重新排版并与库内条目建立活链接。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from docx.oxml import OxmlElement
from docx.oxml.ns import qn

XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

CUSTOM_PROPS_REL = (
    '<Relationship Id="rIdZoteroCustomProps" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties" '
    'Target="docProps/custom.xml"/>'
)
CUSTOM_PROPS_CT = (
    '<Override PartName="/docProps/custom.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.custom-properties+xml"/>'
)


# ---------------------------------------------------------------------------
# 条目 ID 映射
# ---------------------------------------------------------------------------

def load_item_ids(zotero_dir: Path | None = None) -> dict[str, int]:
    """
    从 Zotero SQLite 只读复制中读取 key → itemID 映射。

    Zotero 运行时会独占数据库，因此复制副本再读，绝不直接改库。
    """
    src = (zotero_dir or Path(os.path.expanduser("~")) / "Zotero") / "zotero.sqlite"
    if not src.exists():
        return {}

    tmp = Path(tempfile.gettempdir()) / "zotero_ro_readonly.sqlite"
    try:
        shutil.copy2(src, tmp)
        for ext in ("-wal", "-shm"):
            p = Path(str(src) + ext)
            if p.exists():
                shutil.copy2(p, Path(str(tmp) + ext))
        con = sqlite3.connect(str(tmp))
        try:
            cur = con.cursor()
            cur.execute(
                "SELECT i.key, i.itemID FROM items i "
                "JOIN itemTypes t ON i.itemTypeID = t.itemTypeID "
                "WHERE t.typeName NOT IN ('attachment','note','annotation')"
            )
            return {k: int(v) for k, v in cur.fetchall()}
        finally:
            con.close()
    except Exception:
        return {}
    finally:
        for f in (tmp, Path(str(tmp) + "-wal"), Path(str(tmp) + "-shm")):
            if f.exists():
                try:
                    f.unlink()
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# CSL itemData
# ---------------------------------------------------------------------------

def build_csl_item(item: dict, item_id: int, uri: str) -> dict:
    """把 Zotero 条目转成紧凑 CSL itemData（对齐参照文档的字段形态）。"""
    year = (item.get("date") or "")[:4]
    data: dict = {
        "id": item_id,
        "type": "article-journal"
        if item.get("itemType") == "journalArticle"
        else "article-newspaper",
        "title": item.get("title", ""),
        "container-title": item.get("publicationTitle", ""),
        "author": [{"literal": c} for c in item.get("creators", [])],
        "issued": {"date-parts": [[year]]} if year else {"date-parts": [[""]]},
        "language": "zh-CN",
        "source": "CNKI",
    }
    for src, dst in (
        ("volume", "volume"),
        ("issue", "issue"),
        ("pages", "page"),
        ("DOI", "DOI"),
        ("url", "URL"),
    ):
        if item.get(src):
            data[dst] = item[src]
    return {"id": item_id, "uris": [uri], "itemData": data}


def build_citation_json(
    csl_item: dict, display: str, citation_id: str
) -> dict:
    """构造单个引文字段的 CSL_CITATION JSON。"""
    return {
        "citationID": citation_id,
        "properties": {
            "formattedCitation": display,
            "plainCitation": display,
            "noteIndex": 0,
        },
        "citationItems": [csl_item],
        "schema": (
            "https://github.com/citation-style-language/schema/raw/master/"
            "csl-citation.json"
        ),
    }


# ---------------------------------------------------------------------------
# 字段 XML 注入
# ---------------------------------------------------------------------------

def _run_with(child) -> object:
    r = OxmlElement("w:r")
    r.append(child)
    return r


def _fld_char(kind: str) -> object:
    fc = OxmlElement("w:fldChar")
    fc.set(qn("w:fldCharType"), kind)
    return fc


def _instr(text: str) -> object:
    it = OxmlElement("w:instrText")
    it.set(XML_SPACE, "preserve")
    it.text = text
    return it


def add_citation_field(paragraph, citation_json: dict, display: str) -> None:
    """
    在段落末尾追加一个 Zotero 引文字段。

    结构：begin → instrText → separate → 结果文本 → end
    结果文本即格式化后的引文（如「[1]」），保证未刷新时也可读。
    """
    instr = f" ADDIN ZOTERO_ITEM CSL_CITATION {json.dumps(citation_json, ensure_ascii=False)} "

    paragraph._p.append(_run_with(_fld_char("begin")))
    paragraph._p.append(_run_with(_instr(instr)))
    paragraph._p.append(_run_with(_fld_char("separate")))

    # 结果文本：沿用段落正文字体
    r = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.set(XML_SPACE, "preserve")
    t.text = display
    r.append(t)
    paragraph._p.append(r)

    paragraph._p.append(_run_with(_fld_char("end")))


def add_bibl_field(paragraphs: list) -> bool:
    """
    把参考文献列表包进一个 ZOTERO_BIBL 字段。

    begin / instrText / separate 放在首条文献段落的开头，
    end 放在末条文献段落的结尾——让字段跨越可见的文献段落，
    而不是把整份文献压进一个结果 run。
    """
    if not paragraphs:
        return False

    first, last = paragraphs[0], paragraphs[-1]
    bibl = ' ADDIN ZOTERO_BIBL {"uncited":[],"omitted":[],"custom":[]} CSL_BIBLIOGRAPHY '

    # 首段开头插入 begin / instr / separate（逆序 append 到段首前，用 insert 保证顺序）
    for i, el in enumerate(
        (_run_with(_fld_char("begin")), _run_with(_instr(bibl)), _run_with(_fld_char("separate")))
    ):
        first._p.insert(i, el)

    last._p.append(_run_with(_fld_char("end")))
    return True


# ---------------------------------------------------------------------------
# 文档偏好注入
# ---------------------------------------------------------------------------

def zotero_prefs_xml(style_id: str, locale: str = "zh-CN", session_id: str = "ZoteroDraft") -> str:
    """构造 ZOTERO_PREF_1 的 XML 内容（与参照文档同构）。"""
    return (
        '<data data-version="3" zotero-version="7.0.31">'
        f'<session id="{session_id}"/>'
        f'<style id="{style_id}" locale="{locale}" '
        'hasBibliography="1" bibliographyStyleHasBeenSet="1"/>'
        '<prefs><pref name="fieldType" value="Field"/></prefs>'
        "</data>"
    )


def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def inject_zotero_prefs(docx_path: Path, style_id: str) -> None:
    """
    注入 Zotero 文档偏好。

    与首版实现的两处差异（首版导致 Word 报文件损坏）：
      1. 属性值必须是 XML 字符串（vt:lpwstr），不是 JSON；
      2. 若包内已存在 docProps/custom.xml，必须先跳过再写入，
         否则 zip 会出现同名重复条目，Word 直接判定损坏。
    """
    prefs = _xml_escape(zotero_prefs_xml(style_id))
    custom_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        '<property fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}" pid="2" name="ZOTERO_PREF_1">'
        f"<vt:lpwstr>{prefs}</vt:lpwstr>"
        "</property></Properties>"
    )

    tmp = docx_path.with_suffix(".ztmp.docx")
    with zipfile.ZipFile(docx_path) as zin, zipfile.ZipFile(
        tmp, "w", zipfile.ZIP_DEFLATED
    ) as zout:
        for item in zin.infolist():
            # 跳过已存在的 custom.xml，避免重复条目
            if item.filename == "docProps/custom.xml":
                continue
            data = zin.read(item.filename)
            if item.filename == "_rels/.rels":
                s = data.decode("utf-8")
                if "custom-properties" not in s:
                    s = s.replace("</Relationships>", CUSTOM_PROPS_REL + "</Relationships>")
                data = s.encode("utf-8")
            elif item.filename == "[Content_Types].xml":
                s = data.decode("utf-8")
                if "/docProps/custom.xml" not in s:
                    s = s.replace("</Types>", CUSTOM_PROPS_CT + "</Types>")
                data = s.encode("utf-8")
            zout.writestr(item, data)
        zout.writestr("docProps/custom.xml", custom_xml.encode("utf-8"))
    tmp.replace(docx_path)


# ---------------------------------------------------------------------------
# 结构自检
# ---------------------------------------------------------------------------

def inspect(docx_path: Path) -> dict:
    """统计字段数量与完整性，用于交付前自检。"""
    with zipfile.ZipFile(docx_path) as z:
        doc = z.read("word/document.xml").decode("utf-8")
        has_custom = "docProps/custom.xml" in z.namelist()
        prefs = ""
        if has_custom:
            prefs = z.read("docProps/custom.xml").decode("utf-8")

    items = re.findall(r"ADDIN ZOTERO_ITEM CSL_CITATION (\{.*?\})\s*</w:instrText>", doc, re.S)
    bibl = re.findall(r"ADDIN ZOTERO_BIBL", doc)
    begin = doc.count('w:fldCharType="begin"')
    end = doc.count('w:fldCharType="end"')
    sep = doc.count('w:fldCharType="separate"')

    parsed, bad_json, multi = 0, 0, 0
    keys: set[str] = set()
    for s in items:
        try:
            j = json.loads(s)
            parsed += 1
            ci = j.get("citationItems", [])
            if len(ci) > 1:
                multi += 1
            for c in ci:
                for u in c.get("uris", []):
                    keys.add(u.rsplit("/", 1)[-1])
        except json.JSONDecodeError:
            bad_json += 1

    return {
        "item_fields": len(items),
        "parsed_ok": parsed,
        "json_failures": bad_json,
        "multi_item_fields": multi,
        "bibl_fields": len(bibl),
        "fldChar_begin": begin,
        "fldChar_separate": sep,
        "fldChar_end": end,
        "unique_cited_items": len(keys),
        "has_custom_props": has_custom,
        "has_zotero_pref": "ZOTERO_PREF_1" in prefs,
        "pref_is_xml": "<data data-version" in prefs.replace("&lt;", "<"),
    }
