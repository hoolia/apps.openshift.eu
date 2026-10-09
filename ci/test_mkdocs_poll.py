import importlib.util
import pathlib
import unittest

SPEC = importlib.util.spec_from_file_location(
    "poll", pathlib.Path(__file__).parent.parent / "charts/mkdocs/files/poll.py")
poll = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(poll)


def build(phase, commit=None):
    b = {"status": {"phase": phase}, "spec": {}}
    if commit:
        b["spec"]["revision"] = {"git": {"commit": commit}}
    return b


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
