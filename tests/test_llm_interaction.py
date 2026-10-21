import llm_interaction


def test_client_is_created_once_and_reused(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(llm_interaction, "_client", None)
    assert llm_interaction._get_client() is llm_interaction._get_client()