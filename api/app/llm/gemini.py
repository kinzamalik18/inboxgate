"""Gemini over plain HTTPS, with retries on rate limits and server errors."""

import logging
import time

import httpx

log = logging.getLogger(__name__)

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
RETRY_STATUSES = {429, 500, 502, 503, 504}


class GeminiError(RuntimeError):
    pass


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, model: str, timeout: float = 60):
        self.model = model
        self.client = httpx.Client(
            base_url=BASE_URL,
            headers={"x-goog-api-key": api_key, "content-type": "application/json"},
            timeout=timeout,
        )

    def generate_json(self, system: str, prompt: str, attempts: int = 4) -> str:
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
        }
        delay = 2.0
        for attempt in range(1, attempts + 1):
            response = self.client.post(f"/models/{self.model}:generateContent", json=payload)
            if response.status_code < 400:
                data = response.json()
                try:
                    parts = data["candidates"][0]["content"]["parts"]
                except (KeyError, IndexError) as exc:
                    raise GeminiError(f"Gemini returned no content: {str(data)[:300]}") from exc
                return "".join(p.get("text", "") for p in parts)
            if response.status_code in RETRY_STATUSES and attempt < attempts:
                wait = float(response.headers.get("retry-after", delay))
                log.warning("Gemini returned %s, retrying in %.0fs", response.status_code, wait)
                time.sleep(wait)
                delay = min(delay * 2, 30)
                continue
            raise GeminiError(f"Gemini API error {response.status_code}: {response.text[:300]}")
        raise GeminiError("Gemini API kept failing after retries")
