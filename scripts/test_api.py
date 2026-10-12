"""端到端接口测试：上传 → 审查（需模型）→ 修订 → 导出。

用法：
    python scripts/test_api.py              # 只测上传/解析/知识库接口
    python scripts/test_api.py --review     # 额外跑真实模型审查（需已配置 API Key）
"""

import argparse
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8756/api"
SAMPLE = ROOT / "knowledge" / "samples" / "XX大学高性能计算平台采购需求文件_含典型问题.docx"

PASS, FAIL = [], []


def check(name, fn):
    try:
        r = fn()
        PASS.append(f"{name}: {r}" if r else name)
        print(f"  [PASS] {name}" + (f" → {r}" if r else ""))
    except Exception as e:
        FAIL.append(f"{name}: {type(e).__name__}: {e}")
        print(f"  [FAIL] {name} → {type(e).__name__}: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--review", action="store_true", help="执行真实模型审查")
    ap.add_argument("--base", default=BASE)
    args = ap.parse_args()
    base = args.base

    # trust_env=False：避免读取 Windows 系统代理设置，
    # 否则本机回环请求可能被代理拦截返回 502。
    c = httpx.Client(timeout=120.0, trust_env=False)

    print("=" * 70)
    print("接口测试")
    print("=" * 70)

    # 1. 健康检查
    def t_health():
        r = c.get(f"{base}/health")
        r.raise_for_status()
        d = r.json()
        assert d["status"] == "ok", d
        return f"知识库 {d['kb_chunks']} 片段 / 规则 {d['rules']} 条"

    check("健康检查", t_health)

    # 2. 上传格式列表
    def t_formats():
        r = c.get(f"{base}/upload/formats")
        r.raise_for_status()
        return "、".join(r.json()["supported"])

    check("支持格式", t_formats)

    # 3. 上传并解析
    file_id = {}

    def t_upload():
        assert SAMPLE.exists(), f"测试文件不存在，请先运行 scripts/make_sample.py"
        with open(SAMPLE, "rb") as f:
            r = c.post(
                f"{base}/upload",
                files={"file": (SAMPLE.name, f, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            )
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
        d = r.json()
        file_id["id"] = d["file_id"]
        assert d["char_count"] > 500, f"解析内容过少: {d['char_count']}"
        assert len(d["sections"]) >= 3, f"章节识别过少: {d['sections']}"
        return f"{d['char_count']} 字 / {len(d['sections'])} 章节 / {d['block_count']} 块"

    check("上传解析 docx", t_upload)

    # 4. 章节识别质量
    def t_sections():
        r = c.get(f"{base}/upload/{file_id['id']}")
        r.raise_for_status()
        secs = r.json()["sections"]
        expect = ["技术参数", "资格条件", "商务要求", "评分办法"]
        missing = [e for e in expect if e not in secs]
        assert not missing, f"未识别到章节: {missing}（实际: {secs}）"
        return " | ".join(secs)

    check("章节识别准确性", t_sections)

    # 5. 知识库状态
    def t_kb():
        r = c.get(f"{base}/kb/status")
        r.raise_for_status()
        d = r.json()
        return f"{d['kb']['document_count']} 文档 / {d['kb']['chunk_count']} 片段 / 规则 {d['rules']['rule_count']}"

    check("知识库状态", t_kb)

    # 6. 知识库检索
    def t_search():
        r = c.post(f"{base}/kb/search", json={"query": "以不合理条件限制供应商", "top_k": 5})
        r.raise_for_status()
        hits = r.json()["hits"]
        assert hits, "检索无结果"
        return f"命中 {len(hits)} 条，首位：{hits[0]['citation']}"

    check("知识库检索", t_search)

    # 7. 规则列表
    def t_rules():
        r = c.get(f"{base}/kb/rules")
        r.raise_for_status()
        d = r.json()
        assert d["count"] > 0, "规则库为空"
        return f"{d['count']} 条规则"

    check("规则列表", t_rules)

    # 8. 配置读取
    def t_config():
        r = c.get(f"{base}/config")
        r.raise_for_status()
        d = r.json()
        return f"{len(d['settings']['providers'])} 个供应商档案，当前：{d['settings']['active_provider_id'] or '未设置'}"

    check("配置读取", t_config)

    # 9. 审查（可选）
    if args.review:
        def t_review():
            report = {}
            with c.stream(
                "POST",
                f"{base}/review/stream",
                json={"file_id": file_id["id"]},
                timeout=1800.0,
            ) as resp:
                if resp.status_code != 200:
                    raise RuntimeError(f"HTTP {resp.status_code}")
                buf = ""
                for line in resp.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = json.loads(line[5:].strip())
                    if payload["type"] == "done":
                        report.update(payload["report"])
                    elif payload["type"] == "error":
                        raise RuntimeError(payload["message"])

            assert report, "未收到审查结果"
            s = report["summary"]
            issues = [f for f in report["findings"] if f["violated"]]
            assert issues, "未检出任何问题（测试文件含大量违规点，应检出）"

            print()
            print(f"    ┌─ 总体判定：{s['verdict']}")
            print(f"    ├─ 问题数：{s['issue_count']}（严重 {s['blocker_count']} / 高 {s['high_count']} / 中 {s['medium_count']} / 低 {s['low_count']}）")
            print(f"    ├─ 通过：{s['pass_count']}    耗时：{report['elapsed_sec']}s")
            print(f"    └─ 待人工复核：{s['needs_human_review_count']}")
            print()
            print("    检出的问题（前 12 条）：")
            for f in issues[:12]:
                print(f"      [{f['severity']:<7}] {f['title'] or f['rule_name']}")
                if f.get("location"):
                    print(f"                 位置：{f['location']}")
            print()

            file_id["report"] = report
            file_id["selected"] = [f["id"] for f in issues if f["severity"] in ("blocker", "high")]
            return f"{s['verdict']}，{s['issue_count']} 项问题，耗时 {report['elapsed_sec']}s"

        check("大模型审查", t_review)

        # 10. 修订预览
        def t_preview():
            r = c.post(
                f"{base}/rewrite/preview",
                json={
                    "review_id": file_id["report"]["review_id"],
                    "selected_ids": file_id["selected"],
                },
                timeout=120.0,
            )
            r.raise_for_status()
            d = r.json()
            return f"将替换 {d['counts']['replace']} 处 / 新增 {d['counts']['insert']} 处 / 人工 {d['counts']['manual']} 处"

        check("修订预览", t_preview)

        # 11. 执行修订
        def t_apply():
            r = c.post(
                f"{base}/rewrite/apply",
                json={
                    "review_id": file_id["report"]["review_id"],
                    "selected_ids": file_id["selected"],
                },
                timeout=300.0,
            )
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
            d = r.json()
            assert d["total"] > 0, "未处理任何修订项"
            file_id["output"] = d["output_name"]
            return f"替换 {d['replaced']} / 新增 {d['inserted']} / 人工 {d['manual']} → {d['output_name']}"

        check("自动修订", t_apply)

        # 12. 导出报告
        def t_export():
            rid = file_id["report"]["review_id"]
            out = []
            for fmt in ("docx", "md"):
                r = c.post(f"{base}/review/reports/{rid}/export?fmt={fmt}", timeout=120.0)
                r.raise_for_status()
                out.append(r.json()["filename"])
            return " + ".join(out)

        check("导出报告", t_export)

    print()
    print("=" * 70)
    print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
    if FAIL:
        for f in FAIL:
            print(f"  [FAIL] {f}")
    print("=" * 70)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
