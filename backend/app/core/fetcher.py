"""
法规文件抓取与知识库自动更新。

策略：
  1. 内置一批权威法规来源（政府官网），记录标题、URL、文号。
  2. 定时检查：抓取页面 → 提取正文 → 与本地版本比对哈希 → 有变化才更新。
  3. 更新留痕：写入 update_log，便于追溯「知识库何时变了什么」。
  4. 抓取失败不影响主流程，只记日志。

注意：政府网站结构各异且可能改版，解析采用「通用正文提取 + 多策略兜底」，
无法保证 100% 准确，因此更新后的文档会标记来源 URL，供人工核验。
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx
import yaml

from .config import DATA_DIR, settings
from .rules import REGULATIONS_DIR

LOG_PATH = DATA_DIR / "runtime" / "kb_update_log.json"
STATE_PATH = DATA_DIR / "runtime" / "kb_state.json"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# 内置权威来源清单
# ---------------------------------------------------------------------------

BUILTIN_SOURCES: list[dict[str, str]] = [
    {
        "title": "中华人民共和国政府采购法",
        "doc_number": "主席令第68号",
        "category": "law",
        "url": "https://www.gov.cn/zhengce/2020-12/26/content_5574078.htm",
    },
    {
        "title": "中华人民共和国政府采购法实施条例",
        "doc_number": "国务院令第658号",
        "category": "regulation",
        "url": "https://www.gov.cn/zhengce/content/2015-02/27/content_9507.htm",
    },
    {
        "title": "政府采购需求管理办法",
        "doc_number": "财库〔2021〕22号",
        "category": "rule",
        "url": "https://www.gov.cn/zhengce/zhengceku/2021-05/10/content_5605752.htm",
    },
    {
        "title": "政府采购货物和服务招标投标管理办法",
        "doc_number": "财政部令第87号",
        "category": "rule",
        "url": "https://www.gov.cn/gongbao/content/2017/content_5231671.htm",
    },
    {
        "title": "政府采购非招标采购方式管理办法",
        "doc_number": "财政部令第74号",
        "category": "rule",
        "url": "https://www.gov.cn/gongbao/content/2014/content_2600197.htm",
    },
    {
        "title": "政府采购促进中小企业发展管理办法",
        "doc_number": "财库〔2020〕46号",
        "category": "rule",
        "url": "https://www.gov.cn/zhengce/zhengceku/2020-12/29/content_5574671.htm",
    },
    {
        "title": "政府采购框架协议采购方式管理暂行办法",
        "doc_number": "财政部令第110号",
        "category": "rule",
        "url": "https://www.gov.cn/zhengce/zhengceku/2022-02/07/content_5672171.htm",
    },
    {
        "title": "政府采购进口产品管理办法",
        "doc_number": "财库〔2007〕119号",
        "category": "rule",
        "url": "https://www.gov.cn/zwgk/2008-01/15/content_858181.htm",
    },
    {
        "title": "必须招标的工程项目规定",
        "doc_number": "发改委令第16号",
        "category": "rule",
        "url": "https://www.gov.cn/gongbao/content/2018/content_5312702.htm",
    },
]


# ---------------------------------------------------------------------------
# 正文提取
# ---------------------------------------------------------------------------

_SCRIPT_RE = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_BLANK_RE = re.compile(r"\n{3,}")


def html_to_text(html: str) -> str:
    """从 HTML 提取正文纯文本。多策略兜底。"""
    if not html:
        return ""

    # 优先找常见正文容器
    for pattern in (
        r'<div[^>]+(?:id|class)="[^"]*(?:content|article|TRS_Editor|zoom|detail|body)[^"]*"[^>]*>(.*?)</div>\s*(?:</div>|<div[^>]+class="(?:share|foot))',
        r'<div[^>]+class="[^"]*TRS_Editor[^"]*"[^>]*>(.*?)</div>',
        r"<article[^>]*>(.*?)</article>",
    ):
        m = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
        if m and len(m.group(1)) > 500:
            html = m.group(1)
            break

    text = _SCRIPT_RE.sub("", html)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|tr|h[1-6]|li)>", "\n", text, flags=re.IGNORECASE)
    text = _TAG_RE.sub("", text)

    # HTML 实体
    import html as html_mod

    text = html_mod.unescape(text)

    # 清理
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln and len(ln) > 1]
    # 去掉导航类噪声行
    noise = ("上一篇", "下一篇", "打印", "关闭", "分享到", "扫一扫", "版权所有", "网站地图")
    lines = [ln for ln in lines if not any(n in ln for n in noise)]
    text = "\n".join(lines)
    return _BLANK_RE.sub("\n\n", text).strip()


async def fetch_text(url: str, timeout: int = 40) -> str:
    """抓取 URL 并返回纯文本。"""
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"},
    ) as client:
        r = await client.get(url)
        r.raise_for_status()
        # 编码探测
        raw = r.content
        enc = r.encoding or "utf-8"
        if enc.lower() in ("iso-8859-1", "ascii"):
            # 政府网站多为 utf-8 或 gb2312
            for candidate in ("utf-8", "gb18030"):
                try:
                    raw.decode(candidate)
                    enc = candidate
                    break
                except UnicodeDecodeError:
                    continue
        try:
            html = raw.decode(enc, errors="ignore")
        except LookupError:
            html = raw.decode("utf-8", errors="ignore")

    if "<html" in html.lower() or "<div" in html.lower():
        return html_to_text(html)
    return html.strip()


# ---------------------------------------------------------------------------
# 入库
# ---------------------------------------------------------------------------

def _safe_name(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "", s).strip()[:80] or "regulation"


async def fetch_and_ingest(
    url: str,
    *,
    title: str = "",
    doc_number: str = "",
    category: str = "regulation",
    force: bool = False,
) -> dict[str, Any]:
    """抓取一个 URL 的法规正文并写入知识库目录。"""
    text = await fetch_text(url)
    if len(text) < 200:
        raise RuntimeError(
            f"抓取到的正文过短（{len(text)} 字），可能是动态页面或反爬。"
            f"请手动下载后通过「上传法规」入库。"
        )

    if not title:
        # 用正文首行当标题
        first = text.splitlines()[0][:60]
        title = first or url.rsplit("/", 1)[-1]

    stem = _safe_name(title)
    md_path = REGULATIONS_DIR / f"{stem}.md"

    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()

    # 检查是否已是最新
    if md_path.exists() and not force:
        old = md_path.read_text(encoding="utf-8", errors="ignore")
        old_digest = hashlib.sha256(
            re.sub(r"^#.*\n+", "", old).encode("utf-8")
        ).hexdigest()
        if old_digest == digest:
            return {
                "updated": False,
                "title": title,
                "message": "内容无变化，跳过",
                "url": url,
            }

    md_path.write_text(f"# {title}\n\n{text}", encoding="utf-8")

    # 记录元数据
    meta_path = REGULATIONS_DIR / "_metadata.yaml"
    items: list[dict[str, Any]] = []
    if meta_path.exists():
        try:
            data = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
            items = (data if isinstance(data, list) else (data or {}).get("regulations", [])) or []
        except Exception:
            items = []

    entry = {
        "title": title,
        "doc_number": doc_number,
        "category": category,
        "source_url": url,
        "issuing_body": "",
        "effective_date": "",
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    items = [i for i in items if i.get("title") != title]
    items.append(entry)
    meta_path.write_text(
        yaml.safe_dump({"regulations": items}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    return {
        "updated": True,
        "title": title,
        "url": url,
        "char_count": len(text),
        "saved_as": md_path.name,
    }


# ---------------------------------------------------------------------------
# 状态与日志
# ---------------------------------------------------------------------------

def _load_state() -> dict[str, Any]:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_check": 0, "last_update": 0, "sources": {}}


def _save_state(state: dict[str, Any]) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _append_log(entry: dict[str, Any]) -> None:
    logs: list[dict[str, Any]] = []
    if LOG_PATH.exists():
        try:
            logs = json.loads(LOG_PATH.read_text(encoding="utf-8"))
        except Exception:
            logs = []
    logs.append({"at": time.strftime("%Y-%m-%d %H:%M:%S"), **entry})
    logs = logs[-200:]  # 只留最近 200 条
    LOG_PATH.write_text(json.dumps(logs, ensure_ascii=False, indent=2), encoding="utf-8")


def get_sources() -> list[dict[str, str]]:
    """获取全部法规来源（内置 + 用户自定义）。"""
    sources = list(BUILTIN_SOURCES)
    for u in settings.kb_remote_sources or []:
        if u and not any(s["url"] == u for s in sources):
            sources.append({"title": "", "doc_number": "", "category": "regulation", "url": u})
    return sources


async def check_updates() -> dict[str, Any]:
    """检查是否有可更新的法规（不写入，只报告）。"""
    sources = get_sources()
    results: list[dict[str, Any]] = []

    async with httpx.AsyncClient(
        timeout=30,
        follow_redirects=True,
        headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"},
    ) as client:
        for s in sources:
            item = {"title": s.get("title") or s["url"], "url": s["url"], "status": "unknown"}
            try:
                r = await client.head(s["url"])
                item["status"] = "reachable" if r.status_code < 400 else f"HTTP {r.status_code}"
            except Exception as e:  # noqa: BLE001
                item["status"] = f"无法访问：{type(e).__name__}"
            results.append(item)

    state = _load_state()
    state["last_check"] = time.time()
    _save_state(state)

    return {
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source_count": len(sources),
        "reachable": sum(1 for r in results if r["status"] == "reachable"),
        "results": results,
        "note": "该检查仅验证来源可访问性。执行「立即更新」会抓取正文并比对内容变化。",
    }


async def run_auto_update(force: bool = False) -> dict[str, Any]:
    """执行自动更新：抓取所有来源，内容有变化才写入。"""
    sources = get_sources()
    updated: list[dict[str, Any]] = []
    skipped: list[str] = []
    failed: list[dict[str, str]] = []

    for s in sources:
        try:
            r = await fetch_and_ingest(
                s["url"],
                title=s.get("title", ""),
                doc_number=s.get("doc_number", ""),
                category=s.get("category", "regulation"),
                force=force,
            )
            if r.get("updated"):
                updated.append(r)
            else:
                skipped.append(r.get("title", s["url"]))
        except Exception as e:  # noqa: BLE001
            failed.append({"url": s["url"], "title": s.get("title", ""), "error": str(e)})

    state = _load_state()
    state["last_update"] = time.time()
    _save_state(state)

    _append_log(
        {
            "action": "auto_update",
            "force": force,
            "updated": len(updated),
            "skipped": len(skipped),
            "failed": len(failed),
        }
    )

    return {
        "ok": True,
        "has_update": bool(updated),
        "updated_count": len(updated),
        "updated_files": updated,
        "skipped_count": len(skipped),
        "failed_count": len(failed),
        "failed": failed[:10],
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "message": (
            f"更新完成：{len(updated)} 个文件有更新，{len(skipped)} 个无变化，{len(failed)} 个抓取失败"
            if sources
            else "未配置任何法规来源"
        ),
    }


def get_update_log(limit: int = 50) -> list[dict[str, Any]]:
    if not LOG_PATH.exists():
        return []
    try:
        logs = json.loads(LOG_PATH.read_text(encoding="utf-8"))
        return logs[-limit:][::-1]
    except Exception:
        return []
