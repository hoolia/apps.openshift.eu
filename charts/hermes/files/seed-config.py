"""Init step: make the Hermes home, merge the chart's keys into config.yaml, fetch skills.

The home must be owned by the running user with no group or other write, or Hermes refuses
its control socket. Keys of CHART_CONFIG overwrite the same keys of config.yaml at every
start; every other key, the dashboard's own, is kept.

A skill repository with an authDir clones with the Secret mounted there, in the shape of an
Argo CD repository Secret: sshPrivateKey (or ssh-privatekey) for an SSH URL, else password
(or token) with username (default x-access-token) for an HTTPS URL. The key is copied with
mode 0600 into AUTH_TMP, which no other container mounts, and removed after the clone. A
token is handed to git by a credential helper that reads the file: it is never in the URL,
in an argument or in the log.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

from ruamel.yaml import YAML

HOME = os.environ.get("HERMES_HOME", "/opt/data/home")
CONFIG = os.path.join(HOME, "config.yaml")
SKILLS = os.environ.get("SKILLS_DIR", "/opt/data/skills-external")
AUTH_TMP = os.environ.get("AUTH_TMP", "/run/skills-auth")
KNOWN_HOSTS = os.environ.get("KNOWN_HOSTS", "")
# git runs the helper with "get", "store" or "erase"; it answers "get" only
CREDENTIAL_HELPER = (
    '!f() { [ "$1" = get ] || return 0; '
    'printf "username=%s\\npassword=%s\\n" "$HERMES_GIT_USERNAME" "$(cat "$HERMES_GIT_TOKEN_FILE")"; }; f'
)

yaml = YAML()
yaml.preserve_quotes = True
yaml.width = 4096


def merge(base, extra):
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merge(base[key], value)
        else:
            base[key] = value


def _read(directory: str, *keys: str) -> str:
    for key in keys:
        try:
            with open(os.path.join(directory, key)) as fh:
                value = fh.read()
        except FileNotFoundError:
            continue
        if value.strip():
            return value
    return ""


def _path(directory: str, *keys: str) -> str:
    for key in keys:
        p = os.path.join(directory, key)
        if os.path.isfile(p) and os.path.getsize(p):
            return p
    return ""


def git_env(auth_dir: str, scratch: str) -> dict:
    """Environment of one git clone: no prompt, and the repository's own credential."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    if not auth_dir:
        return env
    key = _read(auth_dir, "sshPrivateKey", "ssh-privatekey")
    if key:
        key_file = os.path.join(scratch, "id")
        # a key from a vault note may carry CRLF line ends or no final newline
        key = key.replace("\r\n", "\n").strip() + "\n"
        with open(os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
            fh.write(key)
        hosts_file = os.path.join(scratch, "known_hosts")
        with open(hosts_file, "w") as fh:
            fh.write(_read(auth_dir, "known_hosts") or KNOWN_HOSTS)
        env["GIT_SSH_COMMAND"] = (
            f"ssh -i {key_file} -o IdentitiesOnly=yes -o IdentityAgent=none -o BatchMode=yes "
            f"-o StrictHostKeyChecking=yes -o UserKnownHostsFile={hosts_file} -o GlobalKnownHostsFile=/dev/null"
        )
    token_file = _path(auth_dir, "password", "token")
    if token_file:
        env.update(
            HERMES_GIT_TOKEN_FILE=token_file,
            HERMES_GIT_USERNAME=_read(auth_dir, "username").strip() or "x-access-token",
            # an empty helper first drops any helper of the system or user git config
            GIT_CONFIG_COUNT="2",
            GIT_CONFIG_KEY_0="credential.helper",
            GIT_CONFIG_VALUE_0="",
            GIT_CONFIG_KEY_1="credential.helper",
            GIT_CONFIG_VALUE_1=CREDENTIAL_HELPER,
        )
    return env


def clone(url: str, ref: str, work: str, auth_dir: str) -> bool:
    cmd = ["git", "clone", "--quiet", "--depth", "1"] + (["--branch", ref] if ref else []) + [url, work]
    scratch = ""
    if auth_dir:
        os.makedirs(AUTH_TMP, exist_ok=True)
        scratch = tempfile.mkdtemp(dir=AUTH_TMP)
    try:
        return subprocess.run(cmd, check=False, env=git_env(auth_dir, scratch)).returncode == 0
    finally:
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)


def fetch_skills(repos: list[dict]) -> None:
    os.makedirs(SKILLS, exist_ok=True)
    keep, failed = set(), False
    for repo in repos:
        name, url = repo["name"], repo["url"]
        ref, path = repo.get("ref") or "", (repo.get("path") or "").strip("/")
        work = os.path.join("/tmp", "skills-" + name)
        shutil.rmtree(work, ignore_errors=True)
        if not clone(url, ref, work, repo.get("authDir") or ""):
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
