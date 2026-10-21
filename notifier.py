import requests
from typing import List, Optional

DISCORD_LIMIT = 2000  # max characters per Discord message


def _split_message(content: str, limit: int = DISCORD_LIMIT) -> List[str]:
    """
    Split content into chunks of at most `limit` characters, breaking on line boundaries
    and hard-splitting any single line longer than `limit`.
    """
    chunks: List[str] = []
    current = ""
    for line in content.split("\n"):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def send_discord(webhook_url: str, content: str, mention_id: Optional[str] = None) -> bool:
    if mention_id:
        content = f"<@{mention_id}> " + content
    for chunk in _split_message(content):
        payload = {
            "content": chunk,
            "flags": 4,
            "username": "stock agent bot"
        }
        try:
            response = requests.post(webhook_url, json=payload, timeout=10)
        except Exception as e:
            print(f"Discord notification failed: {str(e)}")
            return False
        print(f"[DEBUG][discord] status code: {response.status_code}")
        if not response.ok:
            print(f"[DEBUG][discord] response: {response.text}")
            return False
    return True

def notify(method: str, message: str, discord_webhook: Optional[str], mention_id: Optional[str] = None) -> None:
    method = (method or "console").lower()
    if method == "discord" and discord_webhook:
        ok = send_discord(discord_webhook, message, mention_id)
        if not ok:
            print("[NOTIFY][discord][fail] Discord notify failed]", message)
    else:
        print("[NOTIFY][console]", message)