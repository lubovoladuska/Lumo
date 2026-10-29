import logging

from lumo.config import Settings
from lumo.logging_setup import SecretMaskingFilter


def test_defaults_load_without_env_file(monkeypatch):
    for key in ("LUMO_TOKEN", "LUMO_DEEPSEEK_API_KEY", "LUMO_ALLOW_NETWORK"):
        monkeypatch.delenv(key, raising=False)
    settings = Settings(_env_file=None)
    assert settings.port == 8765
    assert settings.llm_model == "deepseek-chat"
    assert settings.bind_host == "127.0.0.1"
    assert settings.disabled_skills == []


def test_network_binding_requires_flag():
    assert Settings(_env_file=None, host="0.0.0.0").bind_host == "127.0.0.1"
    assert Settings(_env_file=None, host="0.0.0.0", allow_network=True).bind_host == "0.0.0.0"


def test_disabled_skills_from_csv(monkeypatch):
    monkeypatch.setenv("LUMO_DISABLED_SKILLS", "set_timer, forget_fact")
    assert Settings(_env_file=None).disabled_skills == ["set_timer", "forget_fact"]


def test_secrets_are_masked_in_logs():
    masking = SecretMaskingFilter(["my-super-token"])
    record = logging.LogRecord(
        "x", logging.INFO, "", 0, "key=%s token=%s", ("sk-abcdef1234567890", "my-super-token"), None
    )
    masking.filter(record)
    assert "sk-abcdef" not in record.msg
    assert "my-super-token" not in record.msg
