from dotenv import load_dotenv
import os
from openai import OpenAI

load_dotenv()

_client = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _client


def ask_llm(prompt: str, model: str = "gpt-4o") -> str:
    """
    send prompt to openai model and return its output
    """
    try:
        response = _get_client().chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a professional stock-tracking assistant."},
                {"role": "user", "content": prompt}
            ],
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"[LLM Error] {e}")
        return ""

