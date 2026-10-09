import importlib.util
import pathlib
import unittest

SPEC = importlib.util.spec_from_file_location(
    "poll", pathlib.Path(__file__).parent.parent / "charts/mkdocs/files/poll.py")
poll = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(poll)


def build(phase, commit=None, digest=None, created="2026-01-01T00:00:00Z"):
    b = {"status": {"phase": phase}, "spec": {}, "metadata": {"creationTimestamp": created}}
    if commit:
        b["spec"]["revision"] = {"git": {"commit": commit}}
    if digest:
        b["status"]["output"] = {"to": {"imageDigest": digest}}
    return b


class RolloutDigest(unittest.TestCase):
    def test_new_image_rolls(self):
        builds = [build("Complete", digest="sha256:new")]
        self.assertEqual(poll.rollout_digest(builds, "sha256:old"), "sha256:new")

    def test_same_image_does_not_roll(self):
        builds = [build("Complete", digest="sha256:same")]
        self.assertIsNone(poll.rollout_digest(builds, "sha256:same"))

    def test_newest_complete_build_wins(self):
        builds = [build("Complete", digest="sha256:b", created="2026-01-02T00:00:00Z"),
                  build("Complete", digest="sha256:a", created="2026-01-01T00:00:00Z")]
        self.assertEqual(poll.rollout_digest(builds, "sha256:a"), "sha256:b")

    def test_failed_build_does_not_roll(self):
        builds = [build("Failed", created="2026-01-02T00:00:00Z"),
                  build("Complete", digest="sha256:a", created="2026-01-01T00:00:00Z")]
        self.assertIsNone(poll.rollout_digest(builds, "sha256:a"))

    def test_no_complete_build_does_not_roll(self):
        self.assertIsNone(poll.rollout_digest([build("Running")], ""))


class RemoteCommit(unittest.TestCase):
    def test_branch(self):
        self.assertEqual(poll.remote_commit("aaa\trefs/heads/main\n", "main"), "aaa")

    def test_other_branch_with_the_same_tail_is_ignored(self):
        out = "bbb\trefs/heads/feature/main\naaa\trefs/heads/main\n"
        self.assertEqual(poll.remote_commit(out, "main"), "aaa")

    def test_annotated_tag_gives_the_commit_not_the_tag_object(self):
        out = "ttt\trefs/tags/v1\nccc\trefs/tags/v1^{}\n"
        self.assertEqual(poll.remote_commit(out, "v1"), "ccc")

    def test_lightweight_tag(self):
        self.assertEqual(poll.remote_commit("ccc\trefs/tags/v1\n", "v1"), "ccc")

    def test_missing_ref_gives_empty(self):
        self.assertEqual(poll.remote_commit("", "main"), "")
        self.assertEqual(poll.remote_commit("bbb\trefs/heads/feature/main\n", "main"), "")


class LsRemote(unittest.TestCase):
    URL = "https://u:s3cr3t@h/r.git"

    def run_with(self, fake):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            result = poll.ls_remote(self.URL, "main", run=fake)
        return result, out.getvalue()

    def test_timeout_gives_none_and_prints_no_password(self):
        def fake(cmd, **kwargs):
            raise poll.subprocess.TimeoutExpired(cmd, 60)
        result, printed = self.run_with(fake)
        self.assertIsNone(result)
        self.assertNotIn("s3cr3t", printed)

    def test_failure_gives_none_and_prints_no_password(self):
        def fake(cmd, **kwargs):
            return poll.subprocess.CompletedProcess(cmd, 128, "", f"fatal: {self.URL} not found")
        result, printed = self.run_with(fake)
        self.assertIsNone(result)
        self.assertNotIn("s3cr3t", printed)

    def test_asks_for_exact_refs(self):
        seen = {}

        def fake(cmd, **kwargs):
            seen["cmd"] = cmd
            return poll.subprocess.CompletedProcess(cmd, 0, "aaa\trefs/heads/main\n", "")
        result, _ = self.run_with(fake)
        self.assertEqual(result, "aaa")
        self.assertEqual(seen["cmd"][3:], ["refs/heads/main", "refs/tags/main", "refs/tags/main^{}"])


class BuildRequest(unittest.TestCase):
    def test_names_the_commit_so_a_failed_build_still_records_it(self):
        request = poll.build_request("mkdocs", "abc123")
        self.assertEqual(request["revision"], {"git": {"commit": "abc123"}})
        self.assertEqual(request["metadata"], {"name": "mkdocs"})


class ShouldBuild(unittest.TestCase):
    def test_new_commit_builds(self):
        self.assertTrue(poll.should_build("new", [build("Complete", "old")]))

    def test_same_commit_does_not_build(self):
        self.assertFalse(poll.should_build("old", [build("Complete", "old")]))

    def test_no_builds_yet_builds(self):
        self.assertTrue(poll.should_build("new", []))

    def test_missing_ref_does_not_build(self):
        self.assertFalse(poll.should_build("", [build("Complete", "old")]))

    def test_active_build_blocks(self):
        for phase in ("New", "Pending", "Running"):
            self.assertFalse(poll.should_build("new", [build(phase)]), phase)

    def test_failed_commit_is_not_retried(self):
        builds = [build("Complete", "old"), build("Failed", "bad")]
        self.assertFalse(poll.should_build("bad", builds))


class RemoteUrl(unittest.TestCase):
    def test_public_repository_is_unchanged(self):
        self.assertEqual(poll.remote_url("https://h/r.git", "", ""), "https://h/r.git")

    def test_password_is_url_quoted(self):
        url = poll.remote_url("https://h/r.git", "u", "p@s/s:w")
        self.assertEqual(url, "https://u:p%40s%2Fs%3Aw@h/r.git")

    def test_user_defaults_to_git(self):
        self.assertEqual(poll.remote_url("https://h/r.git", "", "pw"), "https://git:pw@h/r.git")


if __name__ == "__main__":
    unittest.main()
