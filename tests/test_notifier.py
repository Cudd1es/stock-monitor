from types import SimpleNamespace
import notifier


def capture_posts(monkeypatch, status_code=204, text=""):
    sent = []

    def fake_post(url, json, timeout):
        sent.append(json["content"])
        return SimpleNamespace(status_code=status_code, ok=200 <= status_code < 300, text=text, content=text.encode())

    monkeypatch.setattr(notifier.requests, "post", fake_post)
    return sent


def test_long_message_is_split_into_chunks_within_limit(monkeypatch):
    sent = capture_posts(monkeypatch)
    message = "\n".join(f"line {i} " + "x" * 90 for i in range(60))  # ~6000 chars
    assert notifier.send_discord("https://hook", message, mention_id="42") is True
    assert len(sent) > 1
    assert all(len(chunk) <= 2000 for chunk in sent)
    assert sent[0].startswith("<@42> ")
    assert "".join(sent).replace("<@42> ", "", 1).replace("\n", "") == message.replace("\n", "")


def test_single_line_longer_than_limit_is_hard_split(monkeypatch):
    sent = capture_posts(monkeypatch)
    assert notifier.send_discord("https://hook", "y" * 4500) is True
    assert [len(chunk) for chunk in sent] == [2000, 2000, 500]


def test_no_mention_prefix_when_mention_id_missing(monkeypatch):
    sent = capture_posts(monkeypatch)
    notifier.send_discord("https://hook", "hello")
    assert sent == ["hello"]


def test_error_status_returns_false(monkeypatch):
    capture_posts(monkeypatch, status_code=400, text='{"content": ["Must be 2000 or fewer in length."]}')
    assert notifier.send_discord("https://hook", "hello") is False


def test_notify_reports_discord_failure(monkeypatch):
    capture_posts(monkeypatch, status_code=500, text="boom")
    assert notifier.notify("discord", "hi", discord_webhook="https://hook") is False


def test_notify_reports_success(monkeypatch):
    capture_posts(monkeypatch)
    assert notifier.notify("discord", "hi", discord_webhook="https://hook") is True
    assert notifier.notify("console", "hi", discord_webhook=None) is True
