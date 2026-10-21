from dotenv import load_dotenv
import os
from openai import OpenAI

load_dotenv()

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "minimax/minimax-m3.1-flash:free")

_client = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(base_url=LLM_BASE_URL, api_key=os.getenv("OPENROUTER_API_KEY"))
    return _client


def ask_llm(prompt: str, model: str | None = None) -> str:
    """
    send prompt to openai model and return its output
    """
    try:
        response = _get_client().chat.completions.create(
            model=model or LLM_MODEL,
            messages=[
                {"role": "system", "content": "You are a professional stock-tracking assistant."},
                {"role": "user", "content": prompt}
            ],
        )
        return (response.choices[0].message.content or "").strip()
    except Exception as e:
        print(f"[LLM Error] {e}")
        return ""

