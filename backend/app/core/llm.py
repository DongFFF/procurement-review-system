"""
大模型统一网关。

目标：任何 OpenAI 兼容接口都能直接调用，包括：
  - 官方 API（DeepSeek / OpenAI / 通义 / 智谱 / Kimi / 硅基流动 ...）
  - 本地推理（Ollama / vLLM / LM Studio / Xinference）
  - 中转/聚合网关（one-api / new-api 等）
  - 高校自建私有化部署

关键能力：
  1. 纯 HTTP 实现，不依赖各家 SDK，避免版本地狱。
  2. 自动重试 + 指数退避，网络抖动不影响审查。
  3. JSON 模式自动降级：模型不支持 response_format 时改用提示词约束 + 鲁棒解析。
  4. 并发调用，审查规则时可并行提速。
  5. 流式输出，界面可实时看到进展。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, AsyncIterator

import httpx

from .config import ProviderProfile


class LLMError(RuntimeError):
    """模型调用失败。"""


# ---------------------------------------------------------------------------
# 鲁棒 JSON 解析：大模型输出常带 markdown 围栏或前后废话
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> Any:
    """从模型输出里尽力抠出 JSON。失败抛 LLMError。"""
    if not text or not text.strip():
        raise LLMError("模型返回内容为空")

    raw = text.strip()

    # 1) 直接就是 JSON
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 2) markdown 围栏里的 JSON
    for m in _FENCE_RE.finditer(raw):
        candidate = m.group(1).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue

    # 3) 截取第一个 { 或 [ 到最后一个 } 或 ]
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = raw.find(open_ch)
        end = raw.rfind(close_ch)
        if start != -1 and end > start:
            candidate = raw[start : end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                # 尝试修复常见问题：尾随逗号
                fixed = re.sub(r",\s*([}\]])", r"\1", candidate)
                try:
                    return json.loads(fixed)
                except json.JSONDecodeError:
                    continue

    raise LLMError(f"无法从模型输出中解析 JSON：{raw[:300]}")


# ---------------------------------------------------------------------------
# 网关
# ---------------------------------------------------------------------------

class LLMGateway:
    """按供应商档案调用 OpenAI 兼容的 /chat/completions。"""

    def __init__(self, profile: ProviderProfile):
        if not profile.base_url:
            raise LLMError("未配置 base_url（模型接口地址）")
        if not profile.model:
            raise LLMError("未配置模型名称")
        self.profile = profile
        self.base_url = profile.base_url.rstrip("/")
        self.endpoint = f"{self.base_url}/chat/completions"

        # 代理策略：
        #   本机模型（Ollama/vLLM 等）必须绕过系统代理，否则会被代理拦截；
        #   远程接口（含需要科学上网访问的 OpenAI）则允许使用系统代理。
        host = self.base_url.lower()
        self._trust_env = not any(
            h in host for h in ("localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker")
        )

    def _client(self, **kwargs) -> httpx.AsyncClient:
        """创建 HTTP 客户端，统一应用代理策略。"""
        return httpx.AsyncClient(trust_env=self._trust_env, **kwargs)

    # -- 请求头 ------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.profile.api_key:
            h["Authorization"] = f"Bearer {self.profile.api_key}"
        h.update(self.profile.extra_headers or {})
        return h

    # -- 请求体 ------------------------------------------------------------
    def _payload(
        self,
        messages: list[dict[str, str]],
        *,
        json_mode: bool,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stream: bool = False,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.profile.model,
            "messages": messages,
            "temperature": (
                self.profile.temperature if temperature is None else temperature
            ),
            "max_tokens": self.profile.max_tokens if max_tokens is None else max_tokens,
            "stream": stream,
        }
        if json_mode and self.profile.supports_json_mode:
            body["response_format"] = {"type": "json_object"}
        body.update(self.profile.extra_body or {})
        return body

    # -- 非流式 ------------------------------------------------------------
    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        json_mode: bool = False,
        temperature: float | None = None,
        max_tokens: int | None = None,
        retries: int = 3,
    ) -> str:
        """发一次对话请求，返回文本。带重试。"""
        last_err: Exception | None = None

        for attempt in range(retries + 1):
            try:
                async with self._client(
                    timeout=httpx.Timeout(self.profile.timeout, connect=30.0)
                ) as client:
                    resp = await client.post(
                        self.endpoint,
                        headers=self._headers(),
                        json=self._payload(
                            messages,
                            json_mode=json_mode,
                            temperature=temperature,
                            max_tokens=max_tokens,
                        ),
                    )

                    # 有些网关不支持 response_format，降级重试
                    if resp.status_code == 400 and json_mode:
                        txt = resp.text
                        if "response_format" in txt or "json" in txt.lower():
                            self.profile.supports_json_mode = False
                            continue

                    if resp.status_code == 401:
                        raise LLMError(
                            "认证失败（401）：API Key 无效或已过期，请到「设置」检查"
                        )
                    if resp.status_code == 404:
                        raise LLMError(
                            f"接口不存在（404）：请检查 base_url 是否正确。当前为 {self.endpoint}"
                        )
                    if resp.status_code == 429:
                        raise LLMError("触发限流（429）：请降低并发数或稍后重试")

                    resp.raise_for_status()
                    data = resp.json()

                choices = data.get("choices") or []
                if not choices:
                    raise LLMError(f"模型返回结构异常，无 choices：{str(data)[:300]}")
                msg = choices[0].get("message") or {}
                content = msg.get("content")
                if content is None:
                    # 部分模型把内容放 reasoning_content
                    content = msg.get("reasoning_content") or ""
                return content

            except LLMError:
                raise
            except httpx.HTTPStatusError as e:
                last_err = e
                body = ""
                try:
                    body = e.response.text[:500]
                except Exception:
                    pass
                if attempt < retries:
                    await asyncio.sleep(1.5 * (2**attempt))
                    continue
                raise LLMError(
                    f"HTTP {e.response.status_code}：{body or str(e)}"
                ) from e
            except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                last_err = e
                if attempt < retries:
                    await asyncio.sleep(1.5 * (2**attempt))
                    continue
                raise LLMError(
                    f"无法连接模型服务：{self.endpoint}。请检查网络、base_url，"
                    f"若为本地模型请确认服务已启动。原始错误：{e}"
                ) from e
            except Exception as e:
                last_err = e
                if attempt < retries:
                    await asyncio.sleep(1.5 * (2**attempt))
                    continue
                raise LLMError(f"调用模型失败：{type(e).__name__}: {e}") from e

        raise LLMError(f"调用模型失败（已重试 {retries} 次）：{last_err}")

    # -- JSON 便捷方法 -----------------------------------------------------
    async def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Any:
        """要求模型返回 JSON，并鲁棒解析。"""
        text = await self.chat(
            messages,
            json_mode=True,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return extract_json(text)

    # -- 流式 --------------------------------------------------------------
    async def stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """流式输出增量文本，用于界面实时显示。"""
        async with self._client(
            timeout=httpx.Timeout(self.profile.timeout, connect=30.0)
        ) as client:
            async with client.stream(
                "POST",
                self.endpoint,
                headers=self._headers(),
                json=self._payload(
                    messages,
                    json_mode=False,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=True,
                ),
            ) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", "ignore")[:500]
                    raise LLMError(f"HTTP {resp.status_code}：{body}")
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if chunk == "[DONE]":
                        break
                    try:
                        obj = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    for ch in obj.get("choices") or []:
                        delta = ch.get("delta") or {}
                        piece = delta.get("content")
                        if piece:
                            yield piece

    # -- 连通性测试 --------------------------------------------------------
    async def test(self) -> dict[str, Any]:
        """测试连通性，返回耗时与模型回显。"""
        t0 = time.perf_counter()
        try:
            text = await self.chat(
                [
                    {"role": "system", "content": "你是一个测试助手，只回复要求的内容。"},
                    {"role": "user", "content": "请只回复两个字：正常"},
                ],
                max_tokens=32,
                retries=1,
            )
            dt = time.perf_counter() - t0
            return {
                "ok": True,
                "latency_ms": int(dt * 1000),
                "model": self.profile.model,
                "endpoint": self.endpoint,
                "reply": (text or "").strip()[:100],
            }
        except Exception as e:
            return {
                "ok": False,
                "latency_ms": int((time.perf_counter() - t0) * 1000),
                "model": self.profile.model,
                "endpoint": self.endpoint,
                "error": str(e),
            }

    # -- 列出可用模型 ------------------------------------------------------
    async def list_models(self) -> list[str]:
        """尝试从 /models 拉取可用模型列表（部分网关支持）。"""
        url = f"{self.base_url}/models"
        try:
            async with self._client(timeout=20.0) as client:
                r = await client.get(url, headers=self._headers())
                r.raise_for_status()
                data = r.json()
            items = data.get("data") or data.get("models") or []
            out: list[str] = []
            for it in items:
                if isinstance(it, dict):
                    mid = it.get("id") or it.get("name")
                    if mid:
                        out.append(str(mid))
                elif isinstance(it, str):
                    out.append(it)
            return sorted(set(out))
        except Exception:
            return []


# ---------------------------------------------------------------------------
# 并发执行助手
# ---------------------------------------------------------------------------

async def gather_limited(
    coros: list,
    limit: int = 4,
) -> list[Any]:
    """限制并发数地执行协程列表，单个失败不拖垮整体（返回异常对象）。"""
    sem = asyncio.Semaphore(max(1, limit))

    async def _run(c):
        async with sem:
            try:
                return await c
            except Exception as e:  # noqa: BLE001
                return e

    return await asyncio.gather(*[_run(c) for c in coros])
