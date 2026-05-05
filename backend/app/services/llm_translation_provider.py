"""
LLM Translation Provider abstraction for tag localization.
Supports OpenAI-compatible APIs. Defaults to disabled when no API key is configured.
"""
import json
import logging
from abc import ABC, abstractmethod
from typing import Dict, List

logger = logging.getLogger(__name__)


class TranslationResult:
    def __init__(self, canonical_name: str, display_name_zh: str,
                 aliases_zh: List[str] = None, notes: str = "",
                 needs_review: bool = True, category: str = "general"):
        self.canonical_name = canonical_name
        self.display_name_zh = display_name_zh
        self.aliases_zh = aliases_zh or []
        self.notes = notes
        self.needs_review = needs_review
        self.category = category


class BaseLLMProvider(ABC):
    @abstractmethod
    def is_available(self) -> bool:
        ...

    @abstractmethod
    def get_provider_name(self) -> str:
        ...

    @abstractmethod
    async def translate_tags(self, tags: List[Dict[str, str]]) -> List[TranslationResult]:
        """Translate a batch of tags. Each tag dict has 'name' and 'category'."""
        ...


class DisabledProvider(BaseLLMProvider):
    def is_available(self) -> bool:
        return False

    def get_provider_name(self) -> str:
        return "disabled"

    async def translate_tags(self, tags: List[Dict[str, str]]) -> List[TranslationResult]:
        return []


class OpenAICompatibleProvider(BaseLLMProvider):
    def __init__(self, api_key: str, model: str, base_url: str):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")

    def is_available(self) -> bool:
        return bool(self.api_key and self.model and self.base_url)

    def get_provider_name(self) -> str:
        return "openai_compatible"

    async def translate_tags(self, tags: List[Dict[str, str]]) -> List[TranslationResult]:
        if not self.is_available():
            return []

        import httpx

        tags_text = json.dumps(tags, ensure_ascii=False)
        system_prompt = (
            "You are a Danbooru anime tag translator. Translate the given tags to Chinese (zh-CN).\n"
            "Rules:\n"
            "- general tags: translate to natural Chinese (e.g. blue_eyes \u2192 \u84dd\u773c\u775b)\n"
            "- character tags: use the commonly known Chinese name if exists; if unsure, keep original and set needs_review=true\n"
            "- copyright tags: use the Chinese title if exists; if unsure, keep original and set needs_review=true\n"
            "- artist tags: usually keep original name, set needs_review=true\n"
            "- meta/rating tags: translate descriptively\n"
            "\nRespond with a JSON array. Each element:\n"
            '{"canonical_name": "...", "display_name_zh": "...", "aliases_zh": ["..."], "notes": "...", "needs_review": true/false}\n'
            "ONLY output valid JSON array, no markdown, no explanation."
        )
        user_prompt = f"Translate these Danbooru tags to Chinese:\n{tags_text}"

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt}
                        ],
                        "temperature": 0.3,
                        "max_tokens": 4096,
                    }
                )
                resp.raise_for_status()
                data = resp.json()

            content = data["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

            results_raw = json.loads(content)
            if not isinstance(results_raw, list):
                logger.error("LLM returned non-array response")
                return []

            results = []
            for item in results_raw:
                if not isinstance(item, dict):
                    continue
                cn = item.get("canonical_name", "")
                dn = item.get("display_name_zh", "")
                if not cn or not dn:
                    continue
                cat = "general"
                for t in tags:
                    if t["name"] == cn:
                        cat = t.get("category", "general")
                        break
                results.append(TranslationResult(
                    canonical_name=cn,
                    display_name_zh=dn,
                    aliases_zh=item.get("aliases_zh", []),
                    notes=item.get("notes", ""),
                    needs_review=item.get("needs_review", True),
                    category=cat,
                ))
            return results

        except Exception as e:
            logger.error(f"LLM translation failed: {e}")
            return []


def get_llm_provider() -> BaseLLMProvider:
    from ..config import settings
    if not settings.TAG_TRANSLATION_LLM_ENABLED:
        return DisabledProvider()

    provider_type = settings.TAG_TRANSLATION_LLM_PROVIDER
    if provider_type == "openai_compatible":
        return OpenAICompatibleProvider(
            api_key=settings.TAG_TRANSLATION_LLM_API_KEY,
            model=settings.TAG_TRANSLATION_LLM_MODEL,
            base_url=settings.TAG_TRANSLATION_LLM_BASE_URL,
        )

    logger.warning(f"Unknown LLM provider: {provider_type}, using disabled")
    return DisabledProvider()
