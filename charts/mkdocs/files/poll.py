#!/usr/bin/env python3
"""Start a build when the source branch holds a commit that no build has seen,
and restart the Deployment when a build has made a new image."""
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.parse
import urllib.request

SA = "/var/run/secrets/kubernetes.io/serviceaccount"
API = "https://kubernetes.default.svc/apis/build.openshift.io/v1"
APPS = "https://kubernetes.default.svc/apis/apps/v1"
ACTIVE = ("New", "Pending", "Running")
# pod template annotation: the image the Deployment was last restarted for
DIGEST = "mkdocs/image-digest"


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


def rollout_digest(builds, current):
    """Digest to restart the Deployment with, or None.

    It is the image of the newest build when that build completed and its image
    is not the one the Deployment was last restarted for.
    """
    newest = max(builds, key=lambda b: b.get("metadata", {}).get("creationTimestamp", ""),
                 default=None)
    if not newest or newest.get("status", {}).get("phase") != "Complete":
        return None
    digest = newest["status"].get("output", {}).get("to", {}).get("imageDigest")
    return digest if digest and digest != current else None


def call(url, body=None, method=None, content_type="application/json"):
    with open(f"{SA}/token") as f:
        token = f.read()
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": content_type},
        method=method or ("POST" if body else "GET"))
    context = ssl.create_default_context(cafile=f"{SA}/ca.crt")
    with urllib.request.urlopen(request, context=context, timeout=30) as response:
        return json.load(response)


def main():
    with open(f"{SA}/namespace") as f:
        namespace = f.read().strip()
    name = os.environ["BUILDCONFIG"]
    builds_url = (f"{API}/namespaces/{namespace}/builds?labelSelector="
                  + urllib.parse.quote(f"openshift.io/build-config.name={name}"))
    url = remote_url(os.environ["GIT_REPO"], os.environ.get("GIT_USERNAME", ""),
                     os.environ.get("GIT_PASSWORD", ""))
    result = subprocess.run(["git", "ls-remote", url, os.environ["GIT_REF"]],
                            capture_output=True, text=True, timeout=60)
    if result.returncode:
        # stderr can repeat the URL with the password: do not print it
        print("git ls-remote failed")
        return 1
    commit = remote_commit(result.stdout)
    builds = call(builds_url)["items"]
    if should_build(commit, builds):
        call(f"{API}/namespaces/{namespace}/buildconfigs/{name}/instantiate",
             {"kind": "BuildRequest", "apiVersion": "build.openshift.io/v1", "metadata": {"name": name}})
        print(f"build started for {commit[:12]}")
    else:
        print(f"no build for {commit[:12] or 'missing ref'}")

    # wait for a running build, then restart the Deployment when the image is new
    deadline = time.time() + int(os.environ.get("WAIT_SECONDS", "900"))
    builds = call(builds_url)["items"]
    while any(b.get("status", {}).get("phase") in ACTIVE for b in builds) and time.time() < deadline:
        time.sleep(10)
        builds = call(builds_url)["items"]
    deployment_url = (f"{APPS}/namespaces/{namespace}/deployments/"
                      + os.environ.get("DEPLOYMENT", name))
    annotations = call(deployment_url)["spec"]["template"]["metadata"].get("annotations") or {}
    digest = rollout_digest(builds, annotations.get(DIGEST, ""))
    if not digest:
        print("no restart")
        return 0
    call(deployment_url,
         {"spec": {"template": {"metadata": {"annotations": {DIGEST: digest}}}}},
         method="PATCH", content_type="application/strategic-merge-patch+json")
    print(f"restarted for {digest[:19]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
