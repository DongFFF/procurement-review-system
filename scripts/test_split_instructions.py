"""回归测试：inspect 必须能解析被拆分成多个 run 的字段指令。

Word 打开并保存文档后，会把一条长 instrText 拆进多个 run。
修复前的正则要求 JSON 与 </w:instrText> 相邻，于是全部字段解析失败
（表现为 json_failures == item_fields）。
"""

from __future__ import annotations

import re
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import zotero_fields as Z  # noqa: E402

SRC = ROOT / "docs" / "论文_多智能体协同的采购需求合规审查框架_初稿_Zotero字段版.docx"

INSTR = re.compile(r"(<w:instrText[^>]*>)(.*?)(</w:instrText>)", re.S)


def split_instructions(xml: str, parts: int = 3) -> str:
    """把每条 instrText 的内容切成长度相近的若干段，模拟 Word 的拆分。"""

    def repl(m: re.Match) -> str:
        open_tag, body, close_tag = m.group(1), m.group(2), m.group(3)
        if len(body) < parts * 2:
            return m.group(0)
        size = len(body) // parts
        pieces = [body[i : i + size] for i in range(0, len(body), size)]
        return "".join(
            f"{open_tag}{piece}{close_tag}" for piece in pieces if piece
        )

    return INSTR.sub(repl, xml)


def main() -> int:
    if not SRC.exists():
        print(f"缺少样本：{SRC}")
        return 1

    base = Z.inspect(SRC)
    print("原文件：")
    print(f"  item_fields={base['item_fields']} parsed={base['parsed_ok']} "
          f"failures={base['json_failures']}")

    with zipfile.ZipFile(SRC) as z:
        entries = {n: z.read(n) for n in z.namelist()}

    xml = entries["word/document.xml"].decode("utf-8")
    before = len(INSTR.findall(xml))
    xml = split_instructions(xml)
    after = len(INSTR.findall(xml))
    entries["word/document.xml"] = xml.encode("utf-8")
    print(f"\n拆分指令 run：{before} -> {after}")

    tmp = Path(tempfile.mkdtemp(prefix="zot_split_")) / "split.docx"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)

    split = Z.inspect(tmp)
    print("拆分后：")
    print(f"  item_fields={split['item_fields']} parsed={split['parsed_ok']} "
          f"failures={split['json_failures']} unique={split['unique_cited_items']}")

    checks = [
        ("拆分后仍识别出全部字段", split["item_fields"] == base["item_fields"]),
        ("拆分后 JSON 全部可解析", split["json_failures"] == 0),
        ("拆分后唯一条目数一致", split["unique_cited_items"] == base["unique_cited_items"]),
        ("参考文献字段仍在", split["bibl_fields"] == 1),
        ("字段标记数一致", split["fldChar_begin"] == base["fldChar_begin"]),
    ]
    print()
    failed = 0
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        failed += 0 if ok else 1

    print(f"\n临时文件：{tmp}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
