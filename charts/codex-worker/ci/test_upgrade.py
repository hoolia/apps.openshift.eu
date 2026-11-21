from pathlib import Path
from shutil import copytree
import subprocess
from tempfile import TemporaryDirectory
import unittest

import yaml

from test_render import CHART, EXTERNAL, FIXTURE, by_kind, render


class UpgradeTests(unittest.TestCase):
    def test_reporting_version_change_does_not_restart_the_auth_owner(self) -> None:
        with TemporaryDirectory(prefix="codex-worker-upgrade-") as directory:
            chart = Path(directory) / "codex-worker"
            copytree(CHART, chart)
            metadata_path = chart / "Chart.yaml"
            metadata = yaml.safe_load(metadata_path.read_text())
            metadata["appVersion"] = "99.0.0"
            metadata_path.write_text(yaml.safe_dump(metadata))
            for args in ((), EXTERNAL):
                with self.subTest(args=args):
                    before = by_kind(render(*args), "Deployment")
                    result = subprocess.run(
                        [
                            "helm", "template", "worker", str(chart), "--namespace", "test",
                            "-f", str(FIXTURE), *args,
                        ],
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    after = by_kind(list(yaml.safe_load_all(result.stdout)), "Deployment")
                    self.assertEqual(
                        after["metadata"]["labels"]["app.kubernetes.io/version"], "99.0.0"
                    )
                    self.assertNotEqual(before["metadata"]["labels"], after["metadata"]["labels"])
                    self.assertEqual(
                        before["spec"]["template"]["metadata"],
                        after["spec"]["template"]["metadata"],
                    )


if __name__ == "__main__":
    unittest.main()
