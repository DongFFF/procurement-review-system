"""
后台定时任务：知识库自动更新。

使用 APScheduler 在后台按间隔检查法规更新，不阻塞 API。
失败静默重试，只记日志，绝不影响主流程。
"""

from __future__ import annotations

import logging
import time

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .config import settings
from .fetcher import _append_log, _load_state, _save_state, run_auto_update

log = logging.getLogger("prs.scheduler")

_scheduler: AsyncIOScheduler | None = None

JOB_ID = "kb_auto_update"


async def _job() -> None:
    """定时更新任务。"""
    try:
        state = _load_state()
        last = state.get("last_update", 0)
        interval_sec = max(1, settings.kb_update_interval_hours) * 3600

        # 未到间隔则跳过（避免频繁请求政府网站）
        if time.time() - last < interval_sec * 0.95:
            return

        log.info("开始知识库自动更新检查…")
        result = await run_auto_update(force=False)
        log.info("知识库自动更新：%s", result.get("message"))

        # 有更新则重建索引
        if result.get("has_update"):
            from ..api.kb import rebuild_index

            stats = await rebuild_index()
            log.info("索引已重建：%s", stats)

        state = _load_state()
        state["last_update"] = time.time()
        _save_state(state)

    except Exception as e:  # noqa: BLE001
        log.warning("知识库自动更新失败（不影响使用）：%s", e)
        _append_log({"action": "auto_update_error", "error": str(e)})


def start_scheduler() -> None:
    """启动调度器。"""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return

    _scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
    _scheduler.add_job(
        _job,
        trigger=IntervalTrigger(hours=max(1, settings.kb_update_interval_hours)),
        id=JOB_ID,
        name="知识库自动更新",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    log.info(
        "知识库自动更新已启用，间隔 %s 小时", settings.kb_update_interval_hours
    )


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        try:
            _scheduler.shutdown(wait=False)
        except Exception:
            pass
    _scheduler = None


def scheduler_status() -> dict:
    if _scheduler is None or not _scheduler.running:
        return {"running": False, "jobs": []}
    jobs = []
    for j in _scheduler.get_jobs():
        jobs.append(
            {
                "id": j.id,
                "name": j.name,
                "next_run": j.next_run_time.strftime("%Y-%m-%d %H:%M:%S")
                if j.next_run_time
                else None,
            }
        )
    return {"running": True, "jobs": jobs}
