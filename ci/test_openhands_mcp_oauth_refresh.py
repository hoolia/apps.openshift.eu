"""When charts/openhands/files/mcp_oauth_refresh.py refreshes, and the reload before a refresh."""
import importlib.util
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["MCP_OAUTH_REFRESH_INTERVAL"] = "0"


class FakeContext:
    def __init__(self, valid):
        self.valid = valid

    def is_token_valid(self):
        return self.valid


class FakeOAuth:
    def __init__(self, valid, initialized=True):
        self.context = FakeContext(valid)
        self._initialized = initialized

    def async_auth_flow(self, request):
        return ("flow", request, self._initialized)


# the module patches openhands.sdk.mcp.oauth.MCPOAuth on import
for name in ("openhands", "openhands.sdk", "openhands.sdk.mcp"):
    sys.modules.setdefault(name, types.ModuleType(name))
sys.modules["openhands.sdk.mcp.oauth"] = types.SimpleNamespace(MCPOAuth=FakeOAuth)
spec = importlib.util.spec_from_file_location(
    "mcp_oauth_refresh", os.path.join(ROOT, "charts/openhands/files/mcp_oauth_refresh.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

NOW = 1_800_000_000.0


def state(left, lifetime=28800, refresh_token="r", access_token="a"):
    tokens = types.SimpleNamespace(access_token=access_token, refresh_token=refresh_token,
                                   expires_in=lifetime)
    return types.SimpleNamespace(tokens=tokens, token_expires_at=None if left is None else NOW + left)


class Due(unittest.TestCase):
    def test_fresh_token_waits(self):
        self.assertFalse(mod.due(state(28000), NOW))

    def test_last_quarter_of_lifetime_refreshes(self):
        self.assertTrue(mod.due(state(7000), NOW))

    def test_short_token_refreshes_within_lead(self):
        self.assertFalse(mod.due(state(1800, lifetime=3600), NOW))
        self.assertTrue(mod.due(state(800, lifetime=3600), NOW))

    def test_expired_token_refreshes(self):
        self.assertTrue(mod.due(state(-100), NOW))

    def test_long_token_refreshes_daily(self):
        week = 7 * 86400
        self.assertFalse(mod.due(state(week - 3600, lifetime=week), NOW))
        self.assertTrue(mod.due(state(week - 90000, lifetime=week), NOW))

    def test_unknown_expiry_refreshes(self):
        self.assertTrue(mod.due(state(None), NOW))

    def test_without_refresh_token_never(self):
        self.assertFalse(mod.due(state(-100, refresh_token=None), NOW))
        self.assertFalse(mod.due(types.SimpleNamespace(tokens=None, token_expires_at=None), NOW))


class Reload(unittest.TestCase):
    def test_expired_token_reads_storage_again(self):
        self.assertEqual(FakeOAuth(valid=False).async_auth_flow("req"), ("flow", "req", False))

    def test_valid_token_keeps_memory(self):
        self.assertEqual(FakeOAuth(valid=True).async_auth_flow("req"), ("flow", "req", True))

    def test_first_call_initializes_as_before(self):
        self.assertEqual(FakeOAuth(valid=False, initialized=False).async_auth_flow("req"),
                         ("flow", "req", False))


if __name__ == "__main__":
    unittest.main()
