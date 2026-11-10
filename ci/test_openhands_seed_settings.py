"""Profile defaults of charts/openhands/files/seed-settings.py."""
import importlib.util
import os
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["HOME"] = tempfile.mkdtemp(prefix="seed-settings-tests-")
spec = importlib.util.spec_from_file_location("seed_settings", os.path.join(ROOT, "charts/openhands/files/seed-settings.py"))
ss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ss)

CONN = {"display_name": "Gateway", "provider": "openai"}


class FakeApi:
    def __init__(self, profiles):
        self.profiles = profiles
        self.posted = {}

    def request(self, method, path, body=None):
        if path == "/api/llm/provider-connections":
            return 200, [{"id": "c1", "display_name": "Gateway"}]
        if path == "/api/profiles":
            return 200, {"profiles": [{"name": n, "provider_connection_id": "c1"} for n in self.profiles]}
        if method == "GET" and path.startswith("/api/profiles/"):
            return 200, {"config": dict(self.profiles[path.rsplit("/", 1)[-1]])}
        if method == "POST" and path.startswith("/api/profiles/"):
            name = path.rsplit("/", 1)[-1]
            self.posted[name] = body["llm"]
            self.profiles[name] = {k: v for k, v in body["llm"].items() if k != "provider_connection_id"}
            return 200, None
        return 404, None


class ProfileDefaults(unittest.TestCase):
    def setUp(self):
        ss.STATE = tempfile.mkdtemp(prefix="state-")
        ss.DEFAULTS.clear()
        ss.MODEL_DEFAULTS.clear()
        ss.DEFAULTS.update({"stream": True})
        self.api = FakeApi({
            "glm-5.3-flash": {"model": "openai/vllm/glm-5.3-flash", "reasoning_effort": "high", "stream": True},
            "claude-opus-5-5": {"model": "openai/claude-proxy/claude-opus-5-5", "reasoning_effort": "high", "stream": True},
        })
        ss.request = self.api.request
        ss.call = lambda method, path, body=None: self.api.request(method, path, body)[0]

    def test_model_defaults_reach_only_that_model(self):
        ss.MODEL_DEFAULTS.update({"glm-5.3-flash": {"reasoning_effort": "low"}})
        ss.seed_profile_defaults(CONN)
        self.assertEqual(self.api.profiles["glm-5.3-flash"]["reasoning_effort"], "low")
        self.assertEqual(self.api.profiles["claude-opus-5-5"]["reasoning_effort"], "high")
        self.assertNotIn("claude-opus-5-5", self.api.posted)

    def test_once_per_value(self):
        ss.MODEL_DEFAULTS.update({"glm-5.3-flash": {"reasoning_effort": "low"}})
        ss.seed_profile_defaults(CONN)
        # an edit in the UI stays until the value changes
        self.api.profiles["glm-5.3-flash"]["reasoning_effort"] = "high"
        ss.seed_profile_defaults(CONN)
        self.assertEqual(self.api.profiles["glm-5.3-flash"]["reasoning_effort"], "high")
        ss.MODEL_DEFAULTS["glm-5.3-flash"] = {"reasoning_effort": "low", "max_output_tokens": 32000}
        ss.seed_profile_defaults(CONN)
        self.assertEqual(self.api.profiles["glm-5.3-flash"]["reasoning_effort"], "low")

    def test_model_value_wins_over_profile_defaults(self):
        ss.DEFAULTS["reasoning_effort"] = "high"
        ss.MODEL_DEFAULTS.update({"glm-5.3-flash": {"reasoning_effort": "low"}})
        self.assertEqual(ss.defaults_for("openai/vllm/glm-5.3-flash"),
                         {"stream": True, "reasoning_effort": "low"})
        self.assertEqual(ss.defaults_for("openai/claude-proxy/claude-opus-5-5"),
                         {"stream": True, "reasoning_effort": "high"})
        self.assertEqual(ss.defaults_for(None), {"stream": True, "reasoning_effort": "high"})

    def test_marker_unchanged_without_model_defaults(self):
        # releases before modelProfileDefaults wrote this marker; it must still count
        import hashlib
        import json
        old = hashlib.sha256(json.dumps({"stream": True}, sort_keys=True).encode()).hexdigest()[:8]
        open(os.path.join(ss.STATE, ".seeded-profile-defaults-" + old), "w").close()
        self.api.profiles["glm-5.3-flash"]["stream"] = False
        ss.seed_profile_defaults(CONN)
        self.assertFalse(self.api.profiles["glm-5.3-flash"]["stream"])


class FakeAgentApi:
    def __init__(self, llm_profiles, active, agents):
        self.llm_profiles, self.active, self.agents = llm_profiles, active, agents
        self.posted = []

    def request(self, method, path, body=None):
        if path == "/api/profiles":
            return 200, {"profiles": [{"name": n} for n in self.llm_profiles], "active_profile": self.active}
        if path == "/api/agent-profiles":
            return 200, {"profiles": [dict(a) for a in self.agents.values()]}
        name = path.rsplit("/", 1)[-1]
        if method == "GET" and path.startswith("/api/agent-profiles/"):
            return 200, {"name": name, "profile": dict(self.agents[name])}
        if method == "POST" and path.startswith("/api/agent-profiles/"):
            self.posted.append(name)
            self.agents[name] = dict(body)
            return 200, None
        return 404, None


class AgentLlmRefs(unittest.TestCase):
    def use(self, api):
        ss.request = api.request
        ss.call = lambda method, path, body=None: api.request(method, path, body)[0]

    def test_dangling_ref_moves_to_active(self):
        api = FakeAgentApi(["glm-5.3-flash"], "glm-5.3-flash", {
            "default": {"name": "default", "agent_kind": "openhands", "llm_profile_ref": "default", "revision": 1},
            "claude-code": {"name": "claude-code", "agent_kind": "acp", "llm_profile_ref": None},
        })
        self.use(api)
        ss.repair_agent_llm_refs()
        self.assertEqual(api.agents["default"]["llm_profile_ref"], "glm-5.3-flash")
        self.assertEqual(api.agents["default"]["revision"], 1)
        self.assertEqual(api.posted, ["default"])

    def test_valid_ref_stays(self):
        api = FakeAgentApi(["glm-5.3-flash", "claude-opus-5-5"], "claude-opus-5-5", {
            "default": {"name": "default", "agent_kind": "openhands", "llm_profile_ref": "glm-5.3-flash"},
        })
        self.use(api)
        ss.repair_agent_llm_refs()
        self.assertEqual(api.agents["default"]["llm_profile_ref"], "glm-5.3-flash")
        self.assertEqual(api.posted, [])

    def test_no_active_profile_changes_nothing(self):
        api = FakeAgentApi([], None, {
            "default": {"name": "default", "agent_kind": "openhands", "llm_profile_ref": "default"},
        })
        self.use(api)
        ss.repair_agent_llm_refs()
        self.assertEqual(api.posted, [])


if __name__ == "__main__":
    unittest.main()
