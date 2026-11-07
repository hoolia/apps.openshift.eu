"""Editor chat config of charts/openhands/files/vscode-sync.py."""
import importlib.util
import json
import os
import stat
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="vscode-sync-tests-")
os.environ.update({
    "TOKEN_PROXY_PORT": "8099",
    "CONFIG": os.path.join(TMP, ".continue", "config.yaml"),
    "MACHINE_SETTINGS": os.path.join(TMP, "Machine", "settings.json"),
    "PREFERRED": json.dumps(["glm-5.3-flash", "qwen3-coder-next"]),
    "SETTINGS": json.dumps({"continue.telemetryEnabled": False}),
})
spec = importlib.util.spec_from_file_location("vscode_sync", os.path.join(ROOT, "charts/openhands/files/vscode-sync.py"))
vs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vs)

LISTED = [{"id": "claude/claude-opus-5-5", "context_length": 200000},
          {"id": "z-ai/glm-5.3-flash", "context_length": 131072},
          {"id": "qwen/qwen3-coder-next"},
          {"id": "other/glm-5.3-flash"},
          {"id": "openai/text-embedding-3-small"}]


def config_of(text):
    assert text.startswith(vs.MARKER + "\n")
    return json.loads(text.split("\n", 1)[1])


class Models(unittest.TestCase):
    def setUp(self):
        vs.MODELS, vs.GATEWAY_MODELS = [], True

    def test_preferred_first_with_tools(self):
        out = vs.models(LISTED)
        self.assertEqual(out[0]["model"], "z-ai/glm-5.3-flash")
        self.assertEqual(out[0]["name"], "glm-5.3-flash")
        self.assertEqual(out[0]["capabilities"], ["tool_use"])
        self.assertEqual(out[0]["defaultCompletionOptions"], {"contextLength": 131072})
        self.assertEqual(out[0]["apiBase"], "http://127.0.0.1:8099/v1")
        self.assertNotIn("capabilities", out[1])

    def test_duplicate_name_keeps_prefix_and_embeddings_skipped(self):
        names = [m["name"] for m in vs.models(LISTED)]
        self.assertIn("other/glm-5.3-flash", names)
        self.assertEqual(len(names), len(set(names)))
        self.assertFalse(any("embedding" in n for n in names))

    def test_explicit_models_win(self):
        vs.MODELS = ["vllm/glm-5.3-flash"]
        out = vs.models(LISTED)
        self.assertEqual(out[0]["model"], "vllm/glm-5.3-flash")
        self.assertEqual(out[0]["capabilities"], ["tool_use"])

    def test_without_gateway_models(self):
        vs.GATEWAY_MODELS = False
        self.assertEqual([m["model"] for m in vs.models(LISTED)], ["z-ai/glm-5.3-flash"])


class Mcp(unittest.TestCase):
    def test_entries(self):
        out = {s["name"]: s for s in vs.mcp_servers({
            "kubernetes": {"transport": "stdio", "command": "/tools/k", "args": ["--x"],
                           "env": {"A": 1}, "enabled": True},
            "github": {"url": "http://127.0.0.1:8099/mcp/a/github", "transport": "streamable-http"},
            "events": {"url": "http://e/sse", "transport": "sse"},
            "atlassian": {"url": "http://127.0.0.1:8099/mcp/a/atl", "transport": "http",
                          "auth": {"strategy": "api_key", "header_name": "X-Key", "value": "v"}},
            "cloud": {"url": "https://mcp.example.com", "auth": {"strategy": "oauth2"}},
            "off": {"url": "http://off", "enabled": False},
        })}
        self.assertEqual(sorted(out), ["atlassian", "events", "github", "kubernetes"])
        self.assertEqual(out["kubernetes"], {"name": "kubernetes", "command": "/tools/k",
                                             "args": ["--x"], "env": {"A": "1"}})
        self.assertEqual(out["github"]["type"], "streamable-http")
        self.assertNotIn("requestOptions", out["github"])
        self.assertEqual(out["events"]["type"], "sse")
        self.assertEqual(out["atlassian"]["requestOptions"], {"headers": {"X-Key": "v"}})


class Files(unittest.TestCase):
    def setUp(self):
        for p in (vs.CONFIG, vs.MACHINE):
            if os.path.exists(p):
                os.remove(p)

    def test_write_once_private_and_user_file_kept(self):
        text = vs.render(LISTED, {})
        self.assertEqual(config_of(text)["schema"], "v1")
        self.assertTrue(vs.write_config(text))
        self.assertFalse(vs.write_config(text))
        self.assertEqual(stat.S_IMODE(os.stat(vs.CONFIG).st_mode), 0o600)
        with open(vs.CONFIG, "w") as f:
            f.write("name: mine\n")
        self.assertFalse(vs.write_config(text))
        with open(vs.CONFIG) as f:
            self.assertEqual(f.read(), "name: mine\n")

    def test_settings_merge(self):
        os.makedirs(os.path.dirname(vs.MACHINE), exist_ok=True)
        with open(vs.MACHINE, "w") as f:
            json.dump({"editor.fontSize": 16}, f)
        vs.write_settings()
        with open(vs.MACHINE) as f:
            self.assertEqual(json.load(f), {"editor.fontSize": 16, "continue.telemetryEnabled": False})


if __name__ == "__main__":
    unittest.main()
