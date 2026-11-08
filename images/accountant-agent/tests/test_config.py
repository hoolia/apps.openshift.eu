import json

import pytest

from accountant_agent import config


def test_parse_enabled_roles_sorted(cfg):
    assert [r.name for r in cfg.roles] == ["bookkeeper", "cfo"]
    assert cfg.role("bookkeeper").tools == ("ledger__*",)
    assert cfg.model.base_url.endswith("/v1")
    assert (cfg.approve_command, cfg.reject_command) == ("boek", "afkeuren")


def test_approval_commands_from_config(raw_config):
    raw_config["approval"] = {"approveCommand": "approve", "rejectCommand": "reject"}
    c = config.parse(raw_config)
    assert (c.approve_command, c.reject_command) == ("approve", "reject")


def test_shared_port_is_refused(raw_config):
    raw_config["roles"]["cfo"]["port"] = 8081
    with pytest.raises(ValueError, match="port"):
        config.parse(raw_config)


def test_needs_a_role_not_a_model(raw_config):
    assert config.parse({**raw_config, "model": {}}).model.base_url == ""
    with pytest.raises(ValueError, match="role"):
        config.parse({**raw_config, "roles": {"cfo": {"enabled": False, "port": 1}}})


def test_load_from_env(tmp_path, monkeypatch, raw_config):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(raw_config))
    monkeypatch.setenv("ACCOUNTANT_CONFIG", str(p))
    assert config.load().agent_name == "acct"


def test_bearer_rereads_the_token_file(tmp_path, monkeypatch):
    f = tmp_path / "t"
    f.write_text("one\n")
    assert config.bearer(str(f)) == "one"
    f.write_text("two")
    assert config.bearer(str(f)) == "two"
    monkeypatch.setenv("KEY", "k")
    assert config.bearer("", "KEY") == "k"
    assert config.bearer("", "") == ""
