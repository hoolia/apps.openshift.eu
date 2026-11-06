"""ci/renovate-release.sh on a scratch git repository."""
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHART = """apiVersion: v2
name: demo
version: 0.18.1
appVersion: 1.34.0
"""


def run(cwd, *args):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout


class RenovateRelease(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="renovate-release-")
        os.makedirs(os.path.join(self.repo, "ci"))
        shutil.copy(os.path.join(ROOT, "ci/renovate-release.sh"), os.path.join(self.repo, "ci"))
        os.makedirs(os.path.join(self.repo, "charts/demo/templates"))
        os.makedirs(os.path.join(self.repo, "release-notes/demo"))
        self.write("charts/demo/Chart.yaml", CHART)
        self.write("charts/demo/values.yaml", "image: {}\n")
        self.write("release-notes/demo/0.18.1.md", "# demo 0.18.1\n")
        run(self.repo, "git", "init", "-q")
        run(self.repo, "git", "add", ".")
        run(self.repo, "git", "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "base")

    def tearDown(self):
        shutil.rmtree(self.repo)

    def write(self, path, text):
        with open(os.path.join(self.repo, path), "w") as f:
            f.write(text)

    def read(self, path):
        with open(os.path.join(self.repo, path)) as f:
            return f.read()

    def release(self, dep, old, new, kind, path="charts/demo/values.yaml"):
        run(self.repo, "bash", "ci/renovate-release.sh", path, dep, old, new, kind)

    def test_minor_upstream_raises_minor_and_app_version(self):
        self.release("ghcr.io/openhands/agent-canvas", "1.34.0", "1.36.0", "minor")
        chart = self.read("charts/demo/Chart.yaml")
        self.assertIn("version: 0.19.0\n", chart)
        self.assertIn("appVersion: 1.36.0\n", chart)
        note = self.read("release-notes/demo/0.19.0.md")
        self.assertTrue(note.startswith("# demo 0.19.0\n\nReleased "))
        self.assertIn("### ghcr.io/openhands/agent-canvas 1.36.0\n\nThe chart moves", note)
        self.assertIn("## Upgrade\n\nThe pods that run", note)

    def test_patch_keeps_app_version_of_another_image(self):
        self.release("quay.io/oauth2-proxy/oauth2-proxy", "v7.15.5", "v7.15.6", "patch")
        chart = self.read("charts/demo/Chart.yaml")
        self.assertIn("version: 0.18.2\n", chart)
        self.assertIn("appVersion: 1.34.0\n", chart)

    def test_second_update_in_branch_bumps_once_and_moves_note(self):
        self.release("a/proxy", "1.0.0", "1.0.1", "patch")
        self.release("a/proxy", "1.0.0", "1.0.1", "patch")
        self.assertIn("version: 0.18.2\n", self.read("charts/demo/Chart.yaml"))
        self.assertEqual(self.read("release-notes/demo/0.18.2.md").count("### a/proxy 1.0.1"), 1)
        self.release("b/app", "2.0.0", "2.1.0", "minor")
        self.assertIn("version: 0.19.0\n", self.read("charts/demo/Chart.yaml"))
        self.assertFalse(os.path.exists(os.path.join(self.repo, "release-notes/demo/0.18.2.md")))
        note = self.read("release-notes/demo/0.19.0.md")
        self.assertTrue(note.startswith("# demo 0.19.0\n"))
        self.assertIn("### a/proxy 1.0.1", note)
        self.assertIn("### b/app 2.1.0", note)
        self.assertTrue(os.path.exists(os.path.join(self.repo, "release-notes/demo/0.18.1.md")))

    def test_build_chart_note_and_outside_charts(self):
        self.write("charts/demo/templates/buildconfig.yaml", "")
        self.release("x/y", "1", "2", "major")
        self.assertIn("built again under a new tag", self.read("release-notes/demo/0.19.0.md"))
        self.release("x/y", "1", "2", "major", path=".github/workflows/renovate.yaml")
        self.assertIn("version: 0.19.0\n", self.read("charts/demo/Chart.yaml"))


if __name__ == "__main__":
    unittest.main()
