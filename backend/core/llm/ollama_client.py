"""Thin HTTP client for a locally-running Ollama instance.

Solvent uses a local LLM (default: qwen2.5:7b-instruct) as an OPTIONAL layer
above the deterministic explain builders. Two invariants:

1. The LLM never invents numbers. Every numeric token in its output must
   appear verbatim in the evidence chain, or we fall back to the canned
   answer. See `grounded_explain.validate_numeric_fidelity`.

2. The LLM is never on the money-moving path. It only rewrites text for
   the Explain drawer. The recommendation itself is always the HJB
   optimizer's output.

Ollama runs on http://localhost:11434 by default. We use the /api/generate
endpoint (single-turn completion) with `stream: false` for simplicity.
"""
from __future__ import annotations

import time
import urllib.request
import urllib.error
import json
from dataclasses import dataclass


OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5:7b-instruct"

# Reasonable defaults tuned for short, deterministic paraphrases
DEFAULT_OPTIONS = {
    "temperature": 0.2,        # low variance
    "top_p": 0.9,
    "num_predict": 220,        # ~150 words
    "repeat_penalty": 1.15,
}


@dataclass
class OllamaResult:
    """Container for an Ollama call's outcome."""
    ok: bool
    text: str
    model: str
    latency_ms: float
    eval_count: int = 0
    error: str | None = None


def is_available(timeout_s: float = 2.0) -> bool:
    """Ping the Ollama server. Cheap enough to call at request time."""
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/tags")
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            return r.status == 200
    except Exception:
        return False


def list_models(timeout_s: float = 3.0) -> list[str]:
    """Return the names of installed local models."""
    try:
        req = urllib.request.Request(f"{OLLAMA_URL}/api/tags")
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            data = json.loads(r.read())
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


def generate(
    prompt: str,
    system: str | None = None,
    model: str = DEFAULT_MODEL,
    options: dict | None = None,
    timeout_s: float = 45.0,
) -> OllamaResult:
    """Single-turn text completion via Ollama's /api/generate.

    Uses stream=false for simplicity; the whole response arrives in one JSON
    object. Timeout is critical — first invocation cold-loads the model
    (~10-30s), so callers should keep the timeout generous but bounded.
    """
    body = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {**DEFAULT_OPTIONS, **(options or {})},
    }
    if system:
        body["system"] = system

    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            data = json.loads(r.read())
        latency_ms = (time.time() - t0) * 1000
        return OllamaResult(
            ok=True,
            text=data.get("response", "").strip(),
            model=data.get("model", model),
            latency_ms=latency_ms,
            eval_count=int(data.get("eval_count", 0)),
        )
    except urllib.error.URLError as e:
        return OllamaResult(
            ok=False, text="", model=model,
            latency_ms=(time.time() - t0) * 1000,
            error=f"network: {e.reason}",
        )
    except Exception as e:
        return OllamaResult(
            ok=False, text="", model=model,
            latency_ms=(time.time() - t0) * 1000,
            error=f"{type(e).__name__}: {e}",
        )


def warm(model: str = DEFAULT_MODEL) -> OllamaResult:
    """Fire a tiny prompt to force Ollama to load the model into RAM.

    Called at server startup so the first real user request doesn't pay
    the 10-30 s cold-load penalty.
    """
    return generate(
        prompt="Reply with just: OK",
        model=model,
        options={"num_predict": 5, "temperature": 0.0},
        timeout_s=60.0,
    )
