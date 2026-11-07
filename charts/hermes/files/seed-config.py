"""Init step: make the Hermes home, merge the chart's keys into config.yaml, fetch skills.

The home must be owned by the running user with no group or other write, or Hermes refuses
its control socket. Keys of CHART_CONFIG overwrite the same keys of config.yaml at every
start; every other key, the dashboard's own, is kept.
"""

import json
import os
import shutil
import subprocess
import sys

from ruamel.yaml import YAML

HOME = os.environ.get("HERMES_HOME", "/opt/data/home")
CONFIG = os.path.join(HOME, "config.yaml")
SKILLS = os.environ.get("SKILLS_DIR", "/opt/data/skills-external")

yaml = YAML()
yaml.preserve_quotes = True
yaml.width = 4096


def merge(base, extra):
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merge(base[key], value)
        else:
            base[key] = value


def fetch_skills(repos: list[dict]) -> None:
    os.makedirs(SKILLS, exist_ok=True)
    keep, failed = set(), False
    for repo in repos:
        name, url = repo["name"], repo["url"]
        ref, path = repo.get("ref") or "", (repo.get("path") or "").strip("/")
        work = os.path.join("/tmp", "skills-" + name)
        shutil.rmtree(work, ignore_errors=True)
        cmd = ["git", "clone", "--quiet", "--depth", "1"] + (["--branch", ref] if ref else []) + [url, work]
        if subprocess.run(cmd, check=False).returncode != 0:
            print(f"seed-config: clone of {url} failed; keeping the skills already there", file=sys.stderr)
            failed = True
            continue
        source = os.path.join(work, path) if path else work
        for skill in sorted(os.listdir(source)):
            src = os.path.join(source, skill)
            if not os.path.isfile(os.path.join(src, "SKILL.md")):
                continue
            dest = os.path.join(SKILLS, skill)
            shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(src, dest)
            keep.add(skill)
        shutil.rmtree(work, ignore_errors=True)
    for stale in set() if failed else set(os.listdir(SKILLS)) - keep:
        shutil.rmtree(os.path.join(SKILLS, stale), ignore_errors=True)
    print(f"seed-config: skills {sorted(keep)}", file=sys.stderr)


def main() -> None:
    os.makedirs(HOME, exist_ok=True)
    os.chmod(HOME, 0o700)
    try:
        with open(CONFIG) as fh:
            cfg = yaml.load(fh) or {}
    except FileNotFoundError:
        cfg = {}
    merge(cfg, json.loads(os.environ.get("CHART_CONFIG") or "{}"))
    tmp = CONFIG + ".seed"
    with open(tmp, "w") as fh:
        yaml.dump(cfg, fh)
    os.chmod(tmp, 0o600)
    os.replace(tmp, CONFIG)
    repos = json.loads(os.environ.get("SKILL_REPOSITORIES") or "[]")
    if repos:
        fetch_skills(repos)


if __name__ == "__main__":
    main()
