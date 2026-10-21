from typing import Protocol

from app.config import Settings


class LLMProvider(Protocol):
    name: str
    model: str

    def generate_json(self, system: str, prompt: str) -> str: ...


def get_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "fake":
        from app.llm.fake import FakeTriageProvider

        return FakeTriageProvider()
    if settings.llm_provider == "gemini":
        from app.llm.gemini import GeminiProvider

        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not set. Set it, or use LLM_PROVIDER=fake for a demo.")
        return GeminiProvider(api_key=settings.gemini_api_key, model=settings.gemini_chat_model)
    raise RuntimeError(f"Unknown LLM_PROVIDER: {settings.llm_provider}")
