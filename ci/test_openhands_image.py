"""Image reference rendered by charts/openhands.

python3 -m unittest ci/test_openhands_image.py
"""
import pathlib
import subprocess
import unittest

import yaml

CHART = pathlib.Path(__file__).resolve().parent.parent / "charts/openhands"
VALUES = yaml.safe_load((CHART / "values.yaml").read_text())
DEFAULT_REPOSITORY = VALUES["image"]["repository"]
DEFAULT_TAG = VALUES["image"]["tag"]
DEFAULT_DIGEST = VALUES["image"]["digest"]


def image(*sets):
    args = ["helm", "template", "oh", str(CHART), "-n", "acme-agents", "--show-only", "templates/statefulset.yaml",
            "--set", "tenant=acme", "--set", "build.enabled=false"]
    for s in sets:
        args += ["--set", s]
    out = subprocess.run(args, capture_output=True, text=True, timeout=60, check=True).stdout
    sts = yaml.safe_load(out)
    return {c["name"]: c["image"] for c in sts["spec"]["template"]["spec"]["containers"]}["agent-canvas"]


class ImageTest(unittest.TestCase):
    def test_default_repository_is_pulled_by_digest(self):
        self.assertEqual(image(), f"{DEFAULT_REPOSITORY}@{DEFAULT_DIGEST}")

    def test_other_repository_is_pulled_by_its_tag(self):
        # the default digest belongs to the upstream repository only
        self.assertEqual(image("image.repository=registry.example.com/team/openhands", "image.tag=1.42.0-0.24.0"),
                         "registry.example.com/team/openhands:1.42.0-0.24.0")

    def test_empty_digest_pulls_the_tag(self):
        self.assertEqual(image("image.digest="), f"{DEFAULT_REPOSITORY}:{DEFAULT_TAG}")


if __name__ == "__main__":
    unittest.main()
