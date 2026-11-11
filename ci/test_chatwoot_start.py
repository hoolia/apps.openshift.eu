"""Tests of charts/chatwoot/files/start.sh: the S3 endpoint and the S3 trust bundle.

python3 -m unittest ci/test_chatwoot_start.py
"""
import os
import pathlib
import subprocess
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "charts/chatwoot/files/start.sh"
SYSTEM = "-----BEGIN CERTIFICATE-----\nsystem roots\n-----END CERTIFICATE-----\n"
SERVICE = "-----BEGIN CERTIFICATE-----\ncluster service CA\n-----END CERTIFICATE-----\n"


class StartTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        (self.dir / "system.pem").write_text(SYSTEM)
        (self.dir / "service-ca.crt").write_text(SERVICE)

    def tearDown(self):
        self.tmp.cleanup()

    def run_start(self, **env):
        base = {"PATH": os.environ["PATH"], "FRONTEND_URL": "https://cw.example.com",
                "CHART_SYSTEM_CA": str(self.dir / "system.pem"), "CHART_S3_BUNDLE": str(self.dir / "bundle.pem"),
                "CHART_TMP_PARENT": str(self.dir)}
        base.update(env)
        result = subprocess.run(["sh", str(SCRIPT), "env"], env=base, capture_output=True, text=True, timeout=30)
        out = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        return result, out

    def test_private_temporary_directory(self):
        result, out = self.run_start()
        self.assertEqual(result.returncode, 0, result.stderr)

        tmpdir = pathlib.Path(out["TMPDIR"])
        self.assertEqual(tmpdir.parent, self.dir)
        self.assertEqual(tmpdir.stat().st_mode & 0o777, 0o700)
        self.assertTrue(tmpdir.is_dir())

    def test_https_bucket_with_cluster_ca_keeps_the_system_roots(self):
        result, out = self.run_start(BUCKET_HOST="s3.noobaa-operator.svc", BUCKET_PORT="443",
                                     CHART_S3_EXTRA_CA=str(self.dir / "service-ca.crt"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(out["STORAGE_ENDPOINT"], "https://s3.noobaa-operator.svc:443")
        self.assertEqual(out["AWS_CA_BUNDLE"], str(self.dir / "bundle.pem"))
        self.assertEqual((self.dir / "bundle.pem").read_text(), SYSTEM + SERVICE)

    def test_public_certificate_bucket(self):
        result, out = self.run_start(BUCKET_HOST="s3.example.com", BUCKET_PORT="443")
        self.assertEqual(out["STORAGE_ENDPOINT"], "https://s3.example.com:443")
        self.assertNotIn("AWS_CA_BUNDLE", out)

    def test_plain_http_on_port_80(self):
        result, out = self.run_start(BUCKET_HOST="rook-ceph-rgw.rook-ceph.svc", BUCKET_PORT="80")
        self.assertEqual(out["STORAGE_ENDPOINT"], "http://rook-ceph-rgw.rook-ceph.svc:80")

    def test_no_bucket_no_endpoint(self):
        result, out = self.run_start()
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("STORAGE_ENDPOINT", out)
        self.assertEqual(out["FRONTEND_URL"], "https://cw.example.com")

    def test_missing_cluster_ca_stops_the_start(self):
        result, out = self.run_start(BUCKET_HOST="s3.example.com", CHART_S3_EXTRA_CA=str(self.dir / "absent.crt"))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(out, {})

    def test_missing_system_roots_stop_the_start(self):
        result, out = self.run_start(BUCKET_HOST="s3.example.com", CHART_SYSTEM_CA=str(self.dir / "absent.pem"),
                                     CHART_S3_EXTRA_CA=str(self.dir / "service-ca.crt"))
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
