"""Private skill repositories of charts/hermes/files/seed-config.py, against a local git server."""
import base64
import importlib.util
import os
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="hermes-seed-tests-")
TOKEN = "test-token-not-a-secret-0123456789"
os.environ.update({
    "AUTH_TMP": os.path.join(TMP, "auth-tmp"),
    "KNOWN_HOSTS": "github.com ssh-ed25519 AAAAchart\n",
    "SKILLS_DIR": os.path.join(TMP, "skills"),
    "HERMES_HOME": os.path.join(TMP, "home"),
})
# the image has ruamel.yaml; the tests do not touch config.yaml
if "ruamel.yaml" not in sys.modules:
    try:
        import ruamel.yaml  # noqa: F401
    except ImportError:
        ruamel = types.ModuleType("ruamel")
        ruamel.yaml = types.ModuleType("ruamel.yaml")
        ruamel.yaml.YAML = lambda: types.SimpleNamespace()
        sys.modules.update({"ruamel": ruamel, "ruamel.yaml": ruamel.yaml})
spec = importlib.util.spec_from_file_location("seed_config", os.path.join(ROOT, "charts/hermes/files/seed-config.py"))
sc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sc)


def secret(**keys) -> str:
    d = tempfile.mkdtemp(dir=TMP)
    for k, v in keys.items():
        with open(os.path.join(d, k.replace("_", "-") if k == "ssh_privatekey" else k), "w") as fh:
            fh.write(v)
    return d


def git(*args, cwd=None, env=None):
    subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True)


SEEN = []


class Auth(BaseHTTPRequestHandler):
    """Smart HTTP git server (git http-backend) that wants Basic x-access-token:TOKEN."""

    def _serve(self):
        SEEN.append(self.path)
        want = "Basic " + base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode()
        if self.headers.get("Authorization") != want:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="git"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        path, _, query = self.path.partition("?")
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        env = dict(os.environ, GIT_PROJECT_ROOT=os.path.join(TMP, "serve"), GIT_HTTP_EXPORT_ALL="1",
                   PATH_INFO=path, QUERY_STRING=query, REQUEST_METHOD=self.command,
                   CONTENT_TYPE=self.headers.get("Content-Type") or "", CONTENT_LENGTH=str(len(body)),
                   HTTP_GIT_PROTOCOL=self.headers.get("Git-Protocol") or "")
        out = subprocess.run(["git", "http-backend"], input=body, env=env, capture_output=True).stdout
        head, _, payload = out.partition(b"\r\n\r\n")
        status = 200
        headers = []
        for line in head.decode().split("\r\n"):
            k, _, v = line.partition(": ")
            if k.lower() == "status":
                status = int(v.split()[0])
            elif k:
                headers.append((k, v))
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = _serve

    def log_message(self, *a):
        pass


class SeedConfigAuth(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        work = os.path.join(TMP, "src")
        os.makedirs(os.path.join(work, "skills", "demo"))
        with open(os.path.join(work, "skills", "demo", "SKILL.md"), "w") as fh:
            fh.write("---\nname: demo\ndescription: demo\n---\n")
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
        git("init", "-q", "-b", "main", work)
        git("add", ".", cwd=work)
        git("commit", "-q", "-m", "demo", cwd=work, env=env)
        cls.bare = os.path.join(TMP, "serve", "repo.git")
        git("clone", "-q", "--bare", work, cls.bare)
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Auth)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.httpd.server_port}/repo.git"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def test_token_clone(self):
        work = os.path.join(TMP, "c1")
        self.assertTrue(sc.clone(self.url, "", work, secret(password=TOKEN, url=self.url, type="git")))
        self.assertTrue(os.path.isfile(os.path.join(work, "skills", "demo", "SKILL.md")))
        self.assertFalse(any(TOKEN in p for p in SEEN))
        self.assertNotIn(TOKEN, open(os.path.join(work, ".git", "config")).read())

    def test_token_key_alias(self):
        self.assertTrue(sc.clone(self.url, "main", os.path.join(TMP, "c2"), secret(token=TOKEN)))

    def test_no_credential_fails(self):
        self.assertFalse(sc.clone(self.url, "", os.path.join(TMP, "c3"), ""))

    def test_wrong_username_fails(self):
        self.assertFalse(sc.clone(self.url, "", os.path.join(TMP, "c4"), secret(password=TOKEN, username="other")))

    def test_token_not_in_environment(self):
        d = secret(password=TOKEN)
        env = sc.git_env(d, tempfile.mkdtemp(dir=TMP))
        self.assertFalse(any(TOKEN in v for v in env.values()))
        self.assertEqual(env["HERMES_GIT_TOKEN_FILE"], os.path.join(d, "password"))
        self.assertEqual(env["HERMES_GIT_USERNAME"], "x-access-token")
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")

    def test_ssh_key(self):
        scratch = tempfile.mkdtemp(dir=TMP)
        env = sc.git_env(secret(sshPrivateKey="KEY"), scratch)
        key = os.path.join(scratch, "id")
        self.assertEqual(os.stat(key).st_mode & 0o777, 0o600)
        self.assertEqual(open(key).read(), "KEY\n")
        self.assertEqual(open(os.path.join(scratch, "known_hosts")).read(), "github.com ssh-ed25519 AAAAchart\n")
        cmd = env["GIT_SSH_COMMAND"]
        for opt in (f"-i {key}", "StrictHostKeyChecking=yes", "BatchMode=yes", "IdentitiesOnly=yes"):
            self.assertIn(opt, cmd)
        self.assertNotIn("KEY", cmd.replace(key, ""))
        self.assertNotIn("HERMES_GIT_TOKEN_FILE", env)

    def test_ssh_auth_secret_and_own_known_hosts(self):
        scratch = tempfile.mkdtemp(dir=TMP)
        sc.git_env(secret(ssh_privatekey="KEY2\n", known_hosts="example.com ssh-ed25519 AAAAown\n"), scratch)
        self.assertEqual(open(os.path.join(scratch, "id")).read(), "KEY2\n")
        self.assertEqual(open(os.path.join(scratch, "known_hosts")).read(), "example.com ssh-ed25519 AAAAown\n")

    def test_ssh_key_from_vault_note(self):
        scratch = tempfile.mkdtemp(dir=TMP)
        sc.git_env(secret(sshPrivateKey="-----BEGIN-----\r\nAAA\r\n-----END-----", url="git@x:y", type="git"), scratch)
        self.assertEqual(open(os.path.join(scratch, "id")).read(), "-----BEGIN-----\nAAA\n-----END-----\n")

    def test_key_copy_removed_after_clone(self):
        sc.clone(self.url, "", os.path.join(TMP, "c5"), secret(sshPrivateKey="KEY", password=TOKEN))
        self.assertEqual(os.listdir(os.environ["AUTH_TMP"]), [])

    def test_fetch_skills_private(self):
        sc.fetch_skills([{"name": "private", "url": self.url, "path": "skills", "authDir": secret(password=TOKEN)}])
        self.assertEqual(os.listdir(os.environ["SKILLS_DIR"]), ["demo"])


if __name__ == "__main__":
    unittest.main()
