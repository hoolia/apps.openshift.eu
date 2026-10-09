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
    def test_first_line_wins(self):
        out = "aaa111\trefs/heads/main\nbbb222\trefs/remotes/x/main\n"
        self.assertEqual(poll.remote_commit(out), "aaa111")

    def test_missing_ref_gives_empty(self):
        self.assertEqual(poll.remote_commit(""), "")


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
