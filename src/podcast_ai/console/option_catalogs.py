"""v6.2：Console 级联下拉的选项目录（纯数据 + 查询函数，无 I/O）。

改表即可增删模型/供应商/音色，不必动 pipeline。
"""
from __future__ import annotations

# 本轮仅 openrouter；展示用 base_url 与现网默认一致。
LLM_INTERFACES: dict[str, str] = {
    "openrouter": "https://openrouter.ai/api/v1",
}

# 模型预设（允许 UI 手输未列出的值）
LLM_MODELS: tuple[str, ...] = (
    "deepseek/deepseek-v3.2",
    "openai/gpt-5.6-luna",
    "deepseek/deepseek-v4-pro",
)

# 模型 → OpenRouter provider slug 候选；空列表 = 仅「自动路由」
LLM_PROVIDERS_BY_MODEL: dict[str, tuple[str, ...]] = {
    "openai/gpt-5.6-luna": ("azure/eu",),
}

TTS_MODELS_BY_PROVIDER: dict[str, tuple[str, ...]] = {
    "elevenlabs": ("eleven_v3", "eleven_multilingual_v2"),
    "minimax": ("speech-2.8-hd",),
    "edge": (),
}

TTS_VOICES_BY_PROVIDER: dict[str, tuple[str, ...]] = {
    "elevenlabs": ("Fc5CaIGWKvLHapoOSM2K",),
    "minimax": (
        "Chinese (Mandarin)_Crisp_Girl",
        "English_Sharp_Commentator",
    ),
    "edge": (),
}

AUTO_PROVIDER_LABEL = "（空=OpenRouter 自动路由）"


def llm_interface_ids() -> list[str]:
    return list(LLM_INTERFACES.keys())


def base_url_for_interface(interface: str) -> str:
    key = (interface or "").strip().lower()
    if key not in LLM_INTERFACES:
        raise ValueError(f"暂不支持的模型接口：{interface!r}（本轮仅支持 openrouter）")
    return LLM_INTERFACES[key]


def llm_model_choices(*extra: str) -> list[str]:
    """预设模型 + 可选当前值（保证下拉能显示 config / 手输值）。"""
    items: list[str] = list(LLM_MODELS)
    seen = set(items)
    for raw in extra:
        val = (raw or "").strip()
        if val and val not in seen:
            items.append(val)
            seen.add(val)
    return items


def providers_for_model(model: str) -> list[str]:
    key = (model or "").strip()
    if key in LLM_PROVIDERS_BY_MODEL:
        return list(LLM_PROVIDERS_BY_MODEL[key])
    if key.startswith("openai/"):
        return ["azure/eu"]
    return []


def provider_dropdown_choices(model: str, *extra: str) -> list[tuple[str, str]]:
    """(label, value)；首位为空串，表示 OpenRouter 自动路由。"""
    pairs: list[tuple[str, str]] = [(AUTO_PROVIDER_LABEL, "")]
    seen = {""}
    for slug in (*providers_for_model(model), *extra):
        val = (slug or "").strip()
        if val and val not in seen:
            pairs.append((val, val))
            seen.add(val)
    return pairs


def models_for_tts(provider: str, *extra: str) -> list[str]:
    key = (provider or "").strip().lower()
    items = list(TTS_MODELS_BY_PROVIDER.get(key, ()))
    seen = set(items)
    for raw in extra:
        val = (raw or "").strip()
        if val and val not in seen:
            items.append(val)
            seen.add(val)
    return items


def voices_for_tts(provider: str, *extra: str) -> list[str]:
    key = (provider or "").strip().lower()
    items = list(TTS_VOICES_BY_PROVIDER.get(key, ()))
    seen = set(items)
    for raw in extra:
        val = (raw or "").strip()
        if val and val not in seen:
            items.append(val)
            seen.add(val)
    return items
