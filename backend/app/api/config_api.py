"""配置与模型管理接口。"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..core.config import PRESET_PROVIDERS, ProviderProfile, settings
from ..core.llm import LLMGateway, LLMError

router = APIRouter()


class ProviderIn(BaseModel):
    id: str
    name: str = ""
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    temperature: float = 0.1
    max_tokens: int = 8192
    timeout: int = 300
    supports_json_mode: bool = True
    extra_headers: dict[str, str] = {}
    extra_body: dict[str, Any] = {}


@router.get("")
async def get_config() -> dict:
    """读取当前配置（API Key 脱敏）。"""
    return {
        "settings": settings.public_dict(),
        "presets": PRESET_PROVIDERS,
    }


@router.put("")
async def update_config(payload: dict) -> dict:
    """更新配置。api_key 传空字符串表示不修改。"""
    # 处理供应商列表
    providers_in = payload.get("providers")
    if providers_in is not None:
        old_keys = {p.id: p.api_key for p in settings.providers}
        new_providers: list[ProviderProfile] = []
        for p in providers_in:
            pid = p.get("id")
            if not pid:
                continue
            key = p.get("api_key", "")
            # 前端回传的是脱敏值或空 → 保留原 key
            if not key or "****" in key:
                key = old_keys.get(pid, "")
            new_providers.append(
                ProviderProfile(
                    id=pid,
                    name=p.get("name", ""),
                    base_url=p.get("base_url", ""),
                    api_key=key,
                    model=p.get("model", ""),
                    temperature=float(p.get("temperature", 0.1)),
                    max_tokens=int(p.get("max_tokens", 8192)),
                    timeout=int(p.get("timeout", 300)),
                    supports_json_mode=bool(p.get("supports_json_mode", True)),
                    extra_headers=p.get("extra_headers") or {},
                    extra_body=p.get("extra_body") or {},
                )
            )
        settings.providers = new_providers

    # 其他标量字段
    for field in (
        "active_provider_id",
        "concurrency",
        "max_chunk_chars",
        "retrieval_top_k",
        "enable_rule_prefilter",
        "kb_auto_update",
        "kb_update_interval_hours",
        "kb_remote_sources",
        "theme",
    ):
        if field in payload:
            setattr(settings, field, payload[field])

    settings.save()
    return {"ok": True, "settings": settings.public_dict()}


@router.post("/provider/{provider_id}/test")
async def test_provider(provider_id: str, payload: dict | None = None) -> dict:
    """测试某个供应商的连通性。可传入临时 base_url/api_key/model 覆盖。"""
    profile = settings.get_provider(provider_id)
    if profile is None:
        raise HTTPException(404, f"未找到供应商档案：{provider_id}")

    # 允许用临时值测试（用户还没保存时）
    if payload:
        if payload.get("base_url"):
            profile = profile.model_copy(update={"base_url": payload["base_url"]})
        if payload.get("api_key") and "****" not in payload["api_key"]:
            profile = profile.model_copy(update={"api_key": payload["api_key"]})
        if payload.get("model"):
            profile = profile.model_copy(update={"model": payload["model"]})

    try:
        gw = LLMGateway(profile)
    except LLMError as e:
        return {"ok": False, "error": str(e)}

    result = await gw.test()
    # 顺便探测可用模型列表
    try:
        models = await asyncio.wait_for(gw.list_models(), timeout=12)
        result["available_models"] = models[:100]
    except Exception:
        result["available_models"] = []
    return result


@router.get("/provider/{provider_id}/models")
async def list_provider_models(provider_id: str) -> dict:
    """拉取某供应商的可用模型列表。"""
    profile = settings.get_provider(provider_id)
    if profile is None:
        raise HTTPException(404, f"未找到供应商档案：{provider_id}")
    try:
        gw = LLMGateway(profile)
    except LLMError as e:
        return {"ok": False, "models": [], "error": str(e)}
    models = await gw.list_models()
    return {"ok": bool(models), "models": models, "count": len(models)}


@router.post("/reset")
async def reset_config() -> dict:
    """恢复默认配置（保留已填的 API Key）。"""
    old_keys = {p.id: p.api_key for p in settings.providers}
    settings.providers = []
    for preset in PRESET_PROVIDERS:
        p = ProviderProfile(**preset)
        p.api_key = old_keys.get(p.id, p.api_key)
        settings.providers.append(p)
    settings.active_provider_id = ""
    settings.save()
    return {"ok": True, "settings": settings.public_dict()}
