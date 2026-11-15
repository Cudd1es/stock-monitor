from dotenv import load_dotenv
import os
import time
from typing import List
from openai import OpenAI, APIConnectionError, APITimeoutError

load_dotenv()

# free models come and go (minimax-m3.1-flash:free was retired 2026-10-22), so fall back along a chain
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
DEFAULT_FALLBACKS = ["google/gemma-4-31b-it:free", "openrouter/free"]
ATTEMPTS_PER_MODEL = 2
BACKOFF_SECONDS = [5]           # wait before each retry on the same model
REQUEST_TIMEOUT_SECONDS = 240   # nemotron-ultra takes ~2 minutes for a daily brief
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


def _fallbacks_from_env() -> List[str]:
    """LLM_FALLBACK_MODELS is a comma-separated list; set it empty to disable fallbacks."""
    raw = os.getenv("LLM_FALLBACK_MODELS")
    if raw is None:
        return list(DEFAULT_FALLBACKS) if LLM_BASE_URL == OPENROUTER_BASE_URL else []
    return [m.strip() for m in raw.split(",") if m.strip()]


LLM_BASE_URL = (os.getenv("LLM_BASE_URL") or OPENROUTER_BASE_URL).strip().rstrip("/")
LLM_MODEL = os.getenv("LLM_MODEL", DEFAULT_MODEL if LLM_BASE_URL == OPENROUTER_BASE_URL else "").strip()
LLM_FALLBACK_MODELS = _fallbacks_from_env()

_client = None
_sleep = time.sleep


def model_client_kwargs() -> dict:
    """Shared configuration for report/parser and Agent clients; never log credentials."""
    if not LLM_MODEL:
        raise ValueError("Set LLM_MODEL to a model served by your LLM_BASE_URL endpoint.")
    api_key = os.getenv("LLM_API_KEY")
    if not api_key and LLM_BASE_URL == OPENROUTER_BASE_URL:
        api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key or not api_key.strip():
        raise ValueError(
            "Set LLM_API_KEY (or OPENROUTER_API_KEY for OpenRouter). "
            "For a server without authentication, set LLM_API_KEY=local."
        )
    return {"base_url": LLM_BASE_URL, "api_key": api_key.strip()}


def create_agent_model():
    """Use the same endpoint and credentials as report generation."""
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model=LLM_MODEL, **model_client_kwargs())


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        # retries are handled in ask_llm, per model, so a failing model hands over to the next one
        _client = OpenAI(**model_client_kwargs(),
                         timeout=REQUEST_TIMEOUT_SECONDS, max_retries=0)
    return _client


def _retryable(e: Exception) -> bool:
    if isinstance(e, APITimeoutError):
        return False
    if isinstance(e, APIConnectionError):
        return True
    return getattr(e, "status_code", None) in RETRYABLE_STATUS


def ask_llm(prompt: str, model: str | None = None) -> str:
    """
    send prompt to the model and return its output; on rate limits / server errors retry,
    then try the fallback models in order. Returns "" when every model failed.
    """
    models = list(dict.fromkeys([model or LLM_MODEL, LLM_MODEL, *LLM_FALLBACK_MODELS]))
    for name in models:
        for attempt in range(ATTEMPTS_PER_MODEL):
            try:
                response = _get_client().chat.completions.create(
                    model=name,
                    messages=[
                        {"role": "system", "content": "You are a professional stock-tracking assistant."},
                        {"role": "user", "content": prompt}
                    ],
                )
            except Exception as e:
                print(f"[LLM Error] {name} (attempt {attempt + 1}): {e}")
                if _retryable(e) and attempt + 1 < ATTEMPTS_PER_MODEL:
                    _sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])
                    continue
                break
            content = (response.choices[0].message.content or "").strip()
            if content:
                if name != models[0]:
                    print(f"[LLM] answered by fallback model {name}")
                return content
            print(f"[LLM Error] {name} returned an empty answer")
            break
    return ""
