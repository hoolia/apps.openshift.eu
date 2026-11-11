"""Tests of charts/chatwoot/files/route_host.py.

python3 -m unittest ci/test_chatwoot_route_host.py
"""
import importlib.util
import pathlib
import subprocess
import sys
import tempfile
import unittest

# the module lives in the chart: a __pycache__ there would be packaged with it
sys.dont_write_bytecode = True
SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "charts/chatwoot/files/route_host.py"
SPEC = importlib.util.spec_from_file_location("route_host", SCRIPT)
route_host = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(route_host)


class RouteHostTest(unittest.TestCase):
    def run_script(self, *args):
        with tempfile.TemporaryDirectory() as out:
            result = subprocess.run([sys.executable, "-I", "-B", str(SCRIPT), "chatwoot", out, *args],
                                    capture_output=True, text=True, timeout=30)
            files = {p.name: p.read_text() for p in pathlib.Path(out).iterdir()}
        return result, files

    def test_hostname_given(self):
        result, files = self.run_script("cw.example.com")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(files["frontend-url"], "https://cw.example.com\n")
        self.assertEqual(files["oauth2-proxy.cfg"],
                         'client_id = "cw.example.com"\nredirect_url = "https://cw.example.com/oauth2/callback"\n')

    def test_hostname_that_is_no_dns_name_is_refused(self):
        for bad in ('cw.example.com"\nclient_secret = "x', "cw.example.com/evil", "CW.example.com",
                    "cw..example.com", "-cw.example.com", "cw example.com", "a" * 64 + ".example.com"):
            result, files = self.run_script(bad)
            self.assertNotEqual(result.returncode, 0, bad)
            self.assertEqual(files, {}, bad)

    def test_host_pattern(self):
        for good in ("cw.example.com", "chatwoot-acme-chatwoot.apps.example.org", "localhost"):
            self.assertTrue(route_host.HOST.match(good), good)


if __name__ == "__main__":
    unittest.main()
