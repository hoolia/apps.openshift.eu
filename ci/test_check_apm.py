import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_apm  # noqa: E402


def skill(text):
    root = Path(tempfile.mkdtemp())
    path = root / "skills" / "demo" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(text)
    return path


class CheckApm(unittest.TestCase):
    def test_a_valid_skill_passes(self):
        self.assertEqual(check_apm.skill_errors(skill("---\nname: demo\ndescription: Use when x.\n---\n# Demo\n")), [])

    def test_a_wrong_name_or_description_fails(self):
        errors = check_apm.skill_errors(skill("---\nname: other\ndescription: Does x.\n---\n"))
        self.assertEqual(len(errors), 2)
        path = skill("# no front matter\n")
        self.assertEqual(check_apm.skill_errors(path), [f"{path}: no front matter"])

    def test_a_package_missing_from_a_manifest_fails(self):
        root = Path(tempfile.mkdtemp())
        manifest = root / "apm.yml"
        manifest.write_text("name: x\nversion: 1.0.0\ndependencies:\n  apm:\n  - ./skills/a\n")
        check_apm.ROOT = root
        try:
            self.assertEqual(check_apm.manifest_errors(manifest, "./{}", ["skills/a", "mcp/b"]),
                             ["apm.yml: does not list ./mcp/b"])
        finally:
            check_apm.ROOT = Path(check_apm.__file__).resolve().parent.parent

    def test_this_repository_passes(self):
        self.assertEqual(check_apm.main(), 0)


if __name__ == "__main__":
    unittest.main()
