#!/usr/bin/env python3
"""Start a build when the source branch holds a commit that no build has seen."""
import json
import os
import ssl
import subprocess
import sys
import urllib.parse
import urllib.request

SA = "/var/run/secrets/kubernetes.io/serviceaccount"
API = "https://kubernetes.default.svc/apis/build.openshift.io/v1"
ACTIVE = ("New", "Pending", "Running")


def remote_url(repo, username, password):
    """Repository URL with the login in it; unchanged for a public repository."""
    if not password:
        return repo
    scheme, rest = repo.split("://", 1)
    user = urllib.parse.quote(username or "git", safe="")
    return f"{scheme}://{user}:{urllib.parse.quote(password, safe='')}@{rest}"


def remote_commit(output):
    """First commit id in `git ls-remote` output, or '' when the ref is missing."""
    for line in output.splitlines():
        parts = line.split()
        if len(parts) == 2:
            return parts[0]
    return ""


def should_build(commit, builds):
    """True when the commit is new and no build is in progress.

    A commit that already has a build, failed or not, is never built again:
    a broken commit gives one failed build, not one per poll.
    """
    if not commit:
        return False
    if any(b.get("status", {}).get("phase") in ACTIVE for b in builds):
        return False
    seen = {b.get("spec", {}).get("revision", {}).get("git", {}).get("commit") for b in builds}
    return commit not in seen


def call(path, body=None):
    with open(f"{SA}/token") as f:
        token = f.read()
    request = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode() if body else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST" if body else "GET")
    context = ssl.create_default_context(cafile=f"{SA}/ca.crt")
    with urllib.request.urlopen(request, context=context, timeout=30) as response:
        return json.load(response)


def main():
    with open(f"{SA}/namespace") as f:
        namespace = f.read().strip()
    name = os.environ["BUILDCONFIG"]
    url = remote_url(os.environ["GIT_REPO"], os.environ.get("GIT_USERNAME", ""),
                     os.environ.get("GIT_PASSWORD", ""))
    result = subprocess.run(["git", "ls-remote", url, os.environ["GIT_REF"]],
                            capture_output=True, text=True, timeout=60)
    if result.returncode:
        # stderr can repeat the URL with the password: do not print it
        print("git ls-remote failed")
        return 1
    commit = remote_commit(result.stdout)
    selector = urllib.parse.quote(f"openshift.io/build-config.name={name}")
    builds = call(f"/namespaces/{namespace}/builds?labelSelector={selector}")["items"]
    if not should_build(commit, builds):
        print(f"no build for {commit[:12] or 'missing ref'}")
        return 0
    call(f"/namespaces/{namespace}/buildconfigs/{name}/instantiate",
         {"kind": "BuildRequest", "apiVersion": "build.openshift.io/v1", "metadata": {"name": name}})
    print(f"build started for {commit[:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
