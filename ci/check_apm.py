"""The APM packages of this repository: skill front matter and the manifests that list them."""

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
MANIFESTS = {ROOT / "apm.yml": "./{}", ROOT / "images/coding-agent/apm.yml": "/opt/apm/{}"}
FRONT = re.compile(r"\A---\n(.*?)\n---\n", re.S)


def packages(root=ROOT):
    skills = sorted(f"skills/{p.parent.name}" for p in (root / "skills").glob("*/SKILL.md"))
    servers = sorted(f"mcp/{p.parent.name}" for p in (root / "mcp").glob("*/apm.yml"))
    return skills + servers


def skill_errors(path):
    match = FRONT.match(path.read_text())
    if not match:
        return [f"{path}: no front matter"]
    meta = yaml.safe_load(match.group(1)) or {}
    errors = []
    if meta.get("name") != path.parent.name:
        errors.append(f"{path}: name is not {path.parent.name}")
    if not str(meta.get("description") or "").startswith("Use when"):
        errors.append(f"{path}: description does not start with 'Use when'")
    return errors


def manifest_errors(manifest, form, wanted):
    listed = (yaml.safe_load(manifest.read_text()).get("dependencies") or {}).get("apm") or []
    expected = [form.format(p) for p in wanted]
    missing = [e for e in expected if e not in listed]
    return [f"{manifest.relative_to(ROOT)}: does not list {m}" for m in missing]


def main():
    errors = []
    for path in sorted((ROOT / "skills").glob("*/SKILL.md")):
        errors += skill_errors(path)
    for path in sorted((ROOT / "mcp").glob("*/apm.yml")):
        if not (yaml.safe_load(path.read_text()).get("dependencies") or {}).get("mcp"):
            errors.append(f"{path}: declares no MCP server")
    wanted = packages()
    for manifest, form in MANIFESTS.items():
        errors += manifest_errors(manifest, form, wanted)
    for e in errors:
        print("FAIL", e)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
