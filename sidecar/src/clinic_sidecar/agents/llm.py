"""Local language model through Ollama, with JSON-schema constrained output.

Ollama runs on 127.0.0.1 only. Every call asks for output matching a Pydantic
schema, at temperature 0, so the same transcript gives the same answer.
"""

import json
import os
import time
from dataclasses import dataclass, field
from typing import Protocol, TypeVar

import httpx
from pydantic import BaseModel

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
DEFAULT_MODEL = os.environ.get("CLINIC_LLM_MODEL", "qwen3:8b")
CONTEXT_TOKENS = 16_384  # a 15-minute consultation is roughly 3-4k tokens

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


@dataclass
class CallLog:
    """Every model call, so evaluation can report time and token counts."""

    calls: int = 0
    seconds: float = 0.0
    prompt_tokens: int = 0
    output_tokens: int = 0
    names: list[str] = field(default_factory=list)


class LLM(Protocol):
    model: str
    log: CallLog

    def structured(self, name: str, system: str, user: str, schema: type[T], max_tokens: int = 4000) -> T: ...


class OllamaLLM:
    def __init__(self, model: str = DEFAULT_MODEL, url: str = OLLAMA_URL, timeout_s: float = 600) -> None:
        self.model = model
        self.url = url
        self.log = CallLog()
        self._client = httpx.Client(timeout=timeout_s)

    def structured(self, name: str, system: str, user: str, schema: type[T], max_tokens: int = 4000) -> T:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "format": schema.model_json_schema(),
            "stream": False,
            # Qwen3 would otherwise spend thousands of tokens "thinking" before the JSON.
            "think": False,
            # num_predict caps the answer: a call that would run on is cut off and
            # reported, instead of hanging the whole pipeline.
            "options": {"temperature": 0, "seed": 0, "num_ctx": CONTEXT_TOKENS, "num_predict": max_tokens},
        }
        start = time.monotonic()
        try:
            res = self._client.post(f"{self.url}/api/chat", json=body)
        except httpx.ConnectError as exc:
            raise LLMError(f"Ollama is not running at {self.url}. Start it with `ollama serve`.") from exc
        if res.status_code == 400 and "think" in res.text:
            # Models without a thinking mode reject the flag; retry without it.
            body.pop("think")
            res = self._client.post(f"{self.url}/api/chat", json=body)
        if res.status_code != 200:
            raise LLMError(f"Ollama {res.status_code}: {res.text[:300]}")
        data = res.json()
        self.log.calls += 1
        self.log.seconds += time.monotonic() - start
        self.log.prompt_tokens += int(data.get("prompt_eval_count", 0))
        self.log.output_tokens += int(data.get("eval_count", 0))
        self.log.names.append(name)
        content = data["message"]["content"]
        if data.get("done_reason") == "length":
            raise LLMError(f"{name}: answer cut off after {max_tokens} tokens (model kept generating)")
        try:
            return schema.model_validate(json.loads(content))
        except (json.JSONDecodeError, ValueError) as exc:
            raise LLMError(f"{name}: model output did not match the schema: {content[:300]}") from exc
