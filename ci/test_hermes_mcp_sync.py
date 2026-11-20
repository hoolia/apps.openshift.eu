"""Public MCP server selection of charts/hermes/files/mcp-sync.py."""
import importlib.util
import os
import sys
import types
import unittest

# the image has ruamel.yaml; these tests do not read YAML
if "ruamel.yaml" not in sys.modules:
    try:
        import ruamel.yaml  # noqa: F401
    except ImportError:
        ruamel = types.ModuleType("ruamel")
        ruamel.yaml = types.ModuleType("ruamel.yaml")
        ruamel.yaml.YAML = type("YAML", (), {})
        sys.modules["ruamel"] = ruamel
        sys.modules["ruamel.yaml"] = ruamel.yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = {"LABEL_DOMAIN": "mcp.openshift.eu", "SERVERS": ""}
SAVED = {k: os.environ.get(k) for k in ENV}
os.environ.update(ENV)
spec = importlib.util.spec_from_file_location("hermes_mcp_sync", os.path.join(ROOT, "charts/hermes/files/mcp-sync.py"))
ms = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ms)
for k, v in SAVED.items():
    if v is None:
        os.environ.pop(k, None)
    else:
        os.environ[k] = v


def obj(name, labels):
    return {"metadata": {"name": name, "namespace": "acme", "labels": labels}}


class PublicServerSelection(unittest.TestCase):
    """The platform's public servers are always used, ticked or not."""

    def test_public_servers_without_a_tick(self):
        self.assertTrue(ms.selected(obj("public-playwright", {"mcp.openshift.eu/server": "playwright"})))
        self.assertTrue(ms.selected(obj("public-firecrawl", {"mcp.openshift.eu/server": "firecrawl"})))

    def test_team_server(self):
        self.assertTrue(ms.selected(obj("hermes-playwright", {})))

    def test_other_server(self):
        self.assertTrue(ms.selected(obj("github", {})))

    def test_tenant_server_of_a_catalog_name(self):
        self.assertTrue(ms.selected(obj("firecrawl", {"mcp.openshift.eu/server": "firecrawl"})))

    def test_addon_still_needs_its_name(self):
        self.assertFalse(ms.selected(obj("gmail", {"mcp.openshift.eu/addon": "true", "mcp.openshift.eu/server": "gmail"})))


if __name__ == "__main__":
    unittest.main()
