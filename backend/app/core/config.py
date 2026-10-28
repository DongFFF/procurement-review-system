"""
全局配置管理。

设计要点：
- 所有配置落地到用户数据目录下的 config.json，程序升级不丢失。
- 支持多模型供应商档案（profile），用户可在界面里增删改，运行时热切换。
- 密钥只存本地，绝不入库（.gitignore 已排除）。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# 路径解析：区分「开发态」和「打包态」
# ---------------------------------------------------------------------------
#
# 打包成 exe（PyInstaller onefile）后存在两类路径，必须分开处理：
#
#   1. 只读资源（随包分发）：knowledge/、frontend/dist/
#      → 位于 sys._MEIPASS 临时解压目录，程序退出即销毁
#
#   2. 可写数据（用户资产）：config.json、uploads/、exports/、index/
#      → 必须放在 exe 同级目录，否则用户配置和文件会在重启后丢失
#
# 知识库比较特殊：它随包分发，但用户会通过界面添加/更新法规，
# 因此首次运行时从只读资源复制一份到 exe 同级目录，之后读写都用副本。
# 这样既保证开箱即用，又保证用户的更新能持久保存。


def _is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def _bundle_root() -> Path:
    """只读资源根目录。打包态为 PyInstaller 解压目录，开发态为仓库根。"""
    if _is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[3]


def _app_root() -> Path:
    """可写根目录。打包态为 exe 所在目录，开发态为仓库根。"""
    if _is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[3]


BUNDLE_ROOT = _bundle_root()
APP_ROOT = _app_root()

BACKEND_ROOT = APP_ROOT / "backend"
DATA_ROOT = APP_ROOT / "data"


def _resolve_knowledge_root() -> Path:
    """
    确定知识库目录（可写）。

    - 开发态：直接用仓库里的 knowledge/
    - 打包态：优先用 exe 同级的 knowledge/；不存在则从只读资源复制一份出来
    """
    user_kb = APP_ROOT / "knowledge"

    if not _is_frozen():
        return user_kb

    bundled_kb = BUNDLE_ROOT / "knowledge"
    if user_kb.exists():
        return user_kb

    # 首次运行：把随包的法规与规则复制到 exe 同级，之后可写可更新
    if bundled_kb.is_dir():
        try:
            import shutil

            shutil.copytree(bundled_kb, user_kb)
        except Exception:
            # 复制失败（如只读介质）则退回只读资源，保证程序仍能启动
            return bundled_kb

    return user_kb if user_kb.exists() else bundled_kb


KNOWLEDGE_ROOT = _resolve_knowledge_root()


def _user_data_dir() -> Path:
    """用户数据目录（配置、上传、导出），放在 exe 同级，便于绿色迁移。"""
    d = DATA_ROOT
    d.mkdir(parents=True, exist_ok=True)
    return d


DATA_DIR = _user_data_dir()
CONFIG_PATH = DATA_DIR / "config.json"
RUNTIME_DIR = DATA_DIR / "runtime"
UPLOAD_DIR = DATA_DIR / "uploads"
EXPORT_DIR = DATA_DIR / "exports"
INDEX_DIR = DATA_DIR / "index"

for _d in (RUNTIME_DIR, UPLOAD_DIR, EXPORT_DIR, INDEX_DIR):
    _d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# 模型供应商档案
# ---------------------------------------------------------------------------

class ProviderProfile(BaseModel):
    """一个模型供应商接入档案。任何 OpenAI 兼容接口都能直接填。"""

    id: str = Field(description="唯一标识，如 deepseek / openai / qwen")
    name: str = Field(default="", description="显示名称")
    base_url: str = Field(
        default="https://api.deepseek.com/v1",
        description="OpenAI 兼容的 base_url，需包含 /v1 之类的前缀",
    )
    api_key: str = Field(default="", description="API Key")
    model: str = Field(default="deepseek-chat", description="模型名")
    # 高级参数
    temperature: float = 0.1
    max_tokens: int = 8192
    timeout: int = 300
    # 是否作为默认审查模型
    supports_json_mode: bool = Field(
        default=True, description="该模型是否支持 response_format=json_object"
    )
    extra_headers: dict[str, str] = Field(default_factory=dict)
    extra_body: dict[str, Any] = Field(default_factory=dict)

    def display(self) -> str:
        return self.name or self.id


# 预置常见供应商，用户填 key 即可用
PRESET_PROVIDERS: list[dict[str, Any]] = [
    {
        "id": "deepseek",
        "name": "DeepSeek 深度求索",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "supports_json_mode": True,
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o",
        "supports_json_mode": True,
    },
    {
        "id": "qwen",
        "name": "阿里通义千问",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "supports_json_mode": True,
    },
    {
        "id": "zhipu",
        "name": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-plus",
        "supports_json_mode": True,
    },
    {
        "id": "moonshot",
        "name": "月之暗面 Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-128k",
        "supports_json_mode": True,
    },
    {
        "id": "siliconflow",
        "name": "硅基流动 SiliconFlow",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "Qwen/Qwen2.5-72B-Instruct",
        "supports_json_mode": True,
    },
    {
        "id": "ollama",
        "name": "本地 Ollama",
        "base_url": "http://localhost:11434/v1",
        "model": "qwen2.5:14b",
        "api_key": "ollama",
        "supports_json_mode": True,
    },
    {
        "id": "custom",
        "name": "自定义（任意 OpenAI 兼容接口）",
        "base_url": "",
        "model": "",
        "supports_json_mode": True,
    },
]


# ---------------------------------------------------------------------------
# 应用配置
# ---------------------------------------------------------------------------

class AppSettings(BaseModel):
    """应用总配置。"""

    # --- 模型 ---
    providers: list[ProviderProfile] = Field(default_factory=list)
    active_provider_id: str = Field(default="", description="当前用于审查的供应商")

    # --- 审查行为 ---
    review_mode: Literal["single", "multi"] = Field(
        default="multi",
        description="审查模式：single=单智能体逐规则审查；multi=多智能体协同审查",
    )
    concurrency: int = Field(default=4, ge=1, le=16, description="并发审查的规则数")
    max_chunk_chars: int = Field(default=6000, description="送入模型单段最大字符数")
    retrieval_top_k: int = Field(default=8, description="每条规则检索的法规片段数")
    enable_rule_prefilter: bool = Field(
        default=True,
        description="先用关键词粗筛规则，减少无谓的模型调用（省钱提速）",
    )

    # --- 多智能体协同开关（仅 review_mode=multi 时生效）---
    enable_triage: bool = Field(default=True, description="启用分诊智能体")
    enable_validation: bool = Field(
        default=True, description="启用交叉验证智能体（对抗式复核，抑制误报）"
    )
    enable_arbitration: bool = Field(
        default=True, description="启用仲裁智能体（仅专项与复核结论分歧时触发）"
    )
    enable_synthesis: bool = Field(
        default=True, description="启用汇总智能体（生成总体评述与优先整改建议）"
    )
    enable_evidence_check: bool = Field(
        default=True, description="启用证据一致性校验（ECV），作为交叉验证的前置过滤器"
    )

    # --- 知识库 ---
    kb_auto_update: bool = Field(default=True, description="是否开启知识库自动更新检查")
    kb_update_interval_hours: int = Field(default=24, ge=1)
    kb_remote_sources: list[str] = Field(
        default_factory=list,
        description="远程法规来源 URL 列表（自动更新用）",
    )

    # --- 界面 ---
    theme: Literal["light", "dark", "auto"] = "light"

    # ------------------------------------------------------------------
    @classmethod
    def load(cls) -> "AppSettings":
        if CONFIG_PATH.exists():
            try:
                raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                s = cls.model_validate(raw)
                if not s.providers:
                    s.providers = [ProviderProfile(**p) for p in PRESET_PROVIDERS]
                return s
            except Exception:
                # 配置损坏时回退默认，避免程序起不来
                pass
        s = cls(providers=[ProviderProfile(**p) for p in PRESET_PROVIDERS])
        s.save()
        return s

    def save(self) -> None:
        CONFIG_PATH.write_text(
            self.model_dump_json(indent=2), encoding="utf-8"
        )

    def get_provider(self, provider_id: str | None = None) -> ProviderProfile | None:
        pid = provider_id or self.active_provider_id
        for p in self.providers:
            if p.id == pid:
                return p
        return None

    def public_dict(self) -> dict[str, Any]:
        """给前端的配置，API Key 做脱敏。"""
        d = self.model_dump()
        for p in d["providers"]:
            k = p.get("api_key") or ""
            p["api_key_set"] = bool(k)
            p["api_key"] = (k[:4] + "****" + k[-4:]) if len(k) > 12 else ("****" if k else "")
        return d


# 全局单例
settings = AppSettings.load()
