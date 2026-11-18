"""LLM provider routing: local Ollama first, Gemini REST only if GEMINI_API_KEY is set.

`LLMRouter.generate` returns `None` when no provider answers; the caller then composes an
extractive answer from retrieved passages instead (never a canned answer). Temperature is 0 on
both providers so explanations are as reproducible as the backends allow.

Environment:
  OLLAMA_BASE_URL      default http://localhost:11434
  OLLAMA_MODEL         default qwen2.5:7b-instruct (Q4_K_M, ~4.7 GB; fits an 8 GB GPU or runs on CPU.
                       On 8 GB *RAM* CPU-only machines use qwen2.5:3b-instruct.)
  OLLAMA_TIMEOUT_S     default 120 (first call also loads the model)
  GEMINI_API_KEY       unset -> Gemini disabled
  GEMINI_MODEL         default gemini-2.5-flash
  GEMINI_TIMEOUT_S     default 60
  RAG_LLM_DISABLED     "1" -> skip all providers (extractive answers only)
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "qwen2.5:7b-instruct"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str  # "system" | "user"
    content: str


@dataclass(frozen=True, slots=True)
class LLMResponse:
    text: str
    provider: str  # "ollama" | "gemini"
    model: str
    latency_ms: int


@dataclass(slots=True)
class LLMRouter:
    ollama_base_url: str = field(
        default_factory=lambda: os.getenv("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL)
    )
    ollama_model: str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL))
    ollama_timeout: float = field(default_factory=lambda: float(os.getenv("OLLAMA_TIMEOUT_S", "120")))
    gemini_api_key: str | None = field(default_factory=lambda: os.getenv("GEMINI_API_KEY") or None)
    gemini_model: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL))
    gemini_timeout: float = field(default_factory=lambda: float(os.getenv("GEMINI_TIMEOUT_S", "60")))
    disabled: bool = field(default_factory=lambda: os.getenv("RAG_LLM_DISABLED", "0") == "1")
    transport: httpx.BaseTransport | None = None  # injectable for tests (httpx.MockTransport)
    errors: list[str] = field(default_factory=list)  # provider failures of the last call, for diagnostics

    def _client(self, timeout: float) -> httpx.Client:
        return httpx.Client(timeout=httpx.Timeout(timeout, connect=5.0), transport=self.transport)

    def generate(self, messages: list[ChatMessage]) -> LLMResponse | None:
        self.errors = []
        if self.disabled:
            self.errors.append("LLM disabled by RAG_LLM_DISABLED")
            return None
        for provider in (self._ollama, self._gemini):
            try:
                response = provider(messages)
            except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
                self.errors.append(f"{provider.__name__.lstrip('_')}: {type(exc).__name__}: {exc}")
                logger.warning("LLM provider %s failed: %s", provider.__name__, exc)
                continue
            if response is not None:
                return response
        return None

    def _ollama(self, messages: list[ChatMessage]) -> LLMResponse | None:
        if not self.ollama_base_url:
            return None
        payload = {
            "model": self.ollama_model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            "options": {"temperature": 0, "seed": 0, "num_ctx": 8192},
        }
        start = time.perf_counter()
        with self._client(self.ollama_timeout) as client:
            response = client.post(f"{self.ollama_base_url.rstrip('/')}/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()
        text = data["message"]["content"].strip()
        if not text:
            raise ValueError("empty response")
        return LLMResponse(text, "ollama", self.ollama_model, int((time.perf_counter() - start) * 1000))

    def _gemini(self, messages: list[ChatMessage]) -> LLMResponse | None:
        if not self.gemini_api_key:
            return None
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        contents = [
            {"role": "user" if m.role == "user" else "model", "parts": [{"text": m.content}]}
            for m in messages
            if m.role != "system"
        ]
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": contents,
            "generationConfig": {"temperature": 0},
        }
        start = time.perf_counter()
        with self._client(self.gemini_timeout) as client:
            response = client.post(
                f"{GEMINI_BASE_URL}/models/{self.gemini_model}:generateContent",
                json=payload,
                headers={"x-goog-api-key": self.gemini_api_key},
            )
            response.raise_for_status()
            data = response.json()
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(part.get("text", "") for part in parts).strip()
        if not text:
            raise ValueError("empty response")
        return LLMResponse(text, "gemini", self.gemini_model, int((time.perf_counter() - start) * 1000))
