import llm_interaction


def test_client_is_created_once_and_reused(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(llm_interaction, "_client", None)
    assert llm_interaction._get_client() is llm_interaction._get_client()


def test_client_points_to_openrouter(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(llm_interaction, "_client", None)
    assert str(llm_interaction._get_client().base_url).startswith("https://openrouter.ai/api/v1")


class StatusError(Exception):
    """Stands in for openai.APIStatusError, which carries the HTTP status as `status_code`."""

    def __init__(self, status_code):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class FakeClient:
    """Answers per model from a script: an exception to raise, or the text to return."""

    def __init__(self, script):
        self.script = {m: list(v) for m, v in script.items()}
        self.calls = []
        self.chat = self
        self.completions = self

    def create(self, model, messages):
        self.calls.append(model)
        outcome = self.script[model].pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        message = type("M", (), {"content": outcome})()
        return type("R", (), {"choices": [type("C", (), {"message": message})()]})()


def _fake(monkeypatch, script):
    client = FakeClient(script)
    slept = []
    monkeypatch.setattr(llm_interaction, "_client", client)
    monkeypatch.setattr(llm_interaction, "_sleep", slept.append)
    monkeypatch.setattr(llm_interaction, "LLM_MODEL", "main")
    monkeypatch.setattr(llm_interaction, "LLM_FALLBACK_MODELS", ["backup1", "backup2"])
    return client, slept


def test_default_model_is_nemotron_ultra_with_free_fallbacks():
    assert llm_interaction.DEFAULT_MODEL == "nvidia/nemotron-3-ultra-550b-a55b:free"
    assert llm_interaction.DEFAULT_FALLBACKS == ["google/gemma-4-31b-it:free", "openrouter/free"]


def test_fallback_models_come_from_env(monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_MODELS", " a:free , b ,")
    assert llm_interaction._fallbacks_from_env() == ["a:free", "b"]
    monkeypatch.setenv("LLM_FALLBACK_MODELS", "")
    assert llm_interaction._fallbacks_from_env() == []
    monkeypatch.delenv("LLM_FALLBACK_MODELS")
    assert llm_interaction._fallbacks_from_env() == llm_interaction.DEFAULT_FALLBACKS


def test_first_model_answers(monkeypatch):
    client, slept = _fake(monkeypatch, {"main": ["hi"]})
    assert llm_interaction.ask_llm("p") == "hi"
    assert client.calls == ["main"] and slept == []


def test_rate_limit_is_retried_on_same_model_with_backoff(monkeypatch):
    client, slept = _fake(monkeypatch, {"main": [StatusError(429), "hi"]})
    assert llm_interaction.ask_llm("p") == "hi"
    assert client.calls == ["main", "main"]
    assert slept == [llm_interaction.BACKOFF_SECONDS[0]]


def test_persistent_server_errors_fall_back_to_next_model(monkeypatch):
    client, slept = _fake(monkeypatch, {"main": [StatusError(502)] * llm_interaction.ATTEMPTS_PER_MODEL,
                                        "backup1": ["from backup"]})
    assert llm_interaction.ask_llm("p") == "from backup"
    assert client.calls == ["main"] * llm_interaction.ATTEMPTS_PER_MODEL + ["backup1"]


def test_non_retryable_error_skips_straight_to_next_model(monkeypatch):
    client, slept = _fake(monkeypatch, {"main": [StatusError(404)], "backup1": [StatusError(403)], "backup2": ["ok"]})
    assert llm_interaction.ask_llm("p") == "ok"
    assert client.calls == ["main", "backup1", "backup2"] and slept == []


def test_connection_error_is_retried(monkeypatch):
    client, _ = _fake(monkeypatch, {"main": [llm_interaction.APIConnectionError(request=None), "ok"]})
    assert llm_interaction.ask_llm("p") == "ok"
    assert client.calls == ["main", "main"]


def test_timeout_moves_on_without_waiting_again(monkeypatch):
    # a timed-out request already waited REQUEST_TIMEOUT_SECONDS; retrying the same slow model doubles that
    client, slept = _fake(monkeypatch, {"main": [llm_interaction.APITimeoutError(request=None)], "backup1": ["ok"]})
    assert llm_interaction.ask_llm("p") == "ok"
    assert client.calls == ["main", "backup1"] and slept == []


def test_empty_answer_falls_back(monkeypatch):
    client, _ = _fake(monkeypatch, {"main": ["  "], "backup1": ["real"]})
    assert llm_interaction.ask_llm("p") == "real"
    assert client.calls == ["main", "backup1"]


def test_all_models_failing_returns_empty(monkeypatch):
    n = llm_interaction.ATTEMPTS_PER_MODEL
    _fake(monkeypatch, {"main": [StatusError(429)] * n, "backup1": [StatusError(500)] * n, "backup2": [StatusError(404)]})
    assert llm_interaction.ask_llm("p") == ""


def test_explicit_model_goes_first_and_is_not_repeated(monkeypatch):
    client, _ = _fake(monkeypatch, {"backup1": [StatusError(404)], "main": ["ok"]})
    assert llm_interaction.ask_llm("p", model="backup1") == "ok"
    assert client.calls == ["backup1", "main"]


def test_client_does_its_own_retries_off_and_has_timeout(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(llm_interaction, "_client", None)
    client = llm_interaction._get_client()
    assert client.max_retries == 0
    assert client.timeout == llm_interaction.REQUEST_TIMEOUT_SECONDS
