"""How charts/openhands/files/mcp_timeout_seconds.py converts MCP server timeouts."""
import importlib.util
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CALLS = []


def fake_to_fastmcp_mcp_config(mcp_config, *, cipher=None):
    CALLS.append((mcp_config, cipher))
    return {"mcpServers": {name: dict(server) for name, server in mcp_config.items()}}


# the module patches openhands.sdk.mcp.utils on import
for name in ("openhands", "openhands.sdk"):
    sys.modules.setdefault(name, types.ModuleType(name))
utils = types.ModuleType("openhands.sdk.mcp.utils")
utils.to_fastmcp_mcp_config = fake_to_fastmcp_mcp_config
mcp = types.ModuleType("openhands.sdk.mcp")
mcp.utils = utils
sys.modules["openhands.sdk.mcp"] = mcp
sys.modules["openhands.sdk.mcp.utils"] = utils


def load():
    spec = importlib.util.spec_from_file_location(
        "mcp_timeout_seconds", os.path.join(ROOT, "charts/openhands/files/mcp_timeout_seconds.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = load()


def timeouts(servers):
    out = utils.to_fastmcp_mcp_config(servers)["mcpServers"]
    return {name: server.get("timeout") for name, server in out.items()}


class ToMilliseconds(unittest.TestCase):
    def test_seconds_become_milliseconds(self):
        self.assertEqual(timeouts({"a": {"timeout": 60}, "b": {"timeout": 1.5}}), {"a": 60000, "b": 1500})

    def test_ui_maximum_is_seconds(self):
        self.assertEqual(timeouts({"a": {"timeout": 3600}}), {"a": 3600000})

    def test_old_milliseconds_stay(self):
        self.assertEqual(timeouts({"a": {"timeout": 60000.0}}), {"a": 60000.0})

    def test_zero_none_and_missing_stay(self):
        self.assertEqual(timeouts({"a": {"timeout": 0}, "b": {"timeout": None}, "c": {}}),
                         {"a": 0, "b": None, "c": None})

    def test_bool_and_text_stay(self):
        self.assertEqual(timeouts({"a": {"timeout": True}, "b": {"timeout": "60"}}), {"a": True, "b": "60"})

    def test_arguments_pass_through(self):
        CALLS.clear()
        utils.to_fastmcp_mcp_config({"a": {}}, cipher="c")
        self.assertEqual(CALLS, [({"a": {}}, "c")])

    def test_second_import_wraps_once(self):
        wrapped = utils.to_fastmcp_mcp_config
        load()
        self.assertIs(utils.to_fastmcp_mcp_config, wrapped)
        self.assertEqual(timeouts({"a": {"timeout": 60}}), {"a": 60000})


if __name__ == "__main__":
    unittest.main()
