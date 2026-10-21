import llm_interaction


def test_client_is_created_once_and_reused(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(llm_interaction, "_client", None)
    assert llm_interaction._get_client() is llm_interaction._get_client()


def test_client_points_to_openrouter(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(llm_interaction, "_client", None)
    assert str(llm_interaction._get_client().base_url).startswith("https://openrouter.ai/api/v1")
