"""Create a Secret with a random value once; leave it alone when it exists.

Runs as an install hook with the namespace's ServiceAccount token, so a GitOps tool that
renders without cluster access never sees or rotates the value.
"""
import base64
import json
import os
import secrets
import ssl
import sys
import urllib.error
import urllib.request

SA = "/var/run/secrets/kubernetes.io/serviceaccount"
name = os.environ["SECRET_NAME"]
fixed = json.loads(os.environ.get("FIXED_KEYS", "{}"))
random_keys = os.environ.get("RANDOM_KEYS", "").split()
namespace = open(f"{SA}/namespace").read().strip()
token = open(f"{SA}/token").read().strip()
api = f"https://{os.environ['KUBERNETES_SERVICE_HOST']}:{os.environ['KUBERNETES_SERVICE_PORT']}"
ctx = ssl.create_default_context(cafile=f"{SA}/ca.crt")


def call(method, path, body=None):
    req = urllib.request.Request(api + path, method=method,
                                 data=None if body is None else json.dumps(body).encode())
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
        return json.loads(resp.read() or b"{}")


try:
    call("GET", f"/api/v1/namespaces/{namespace}/secrets/{name}")
    print(f"secret {name} exists")
    sys.exit(0)
except urllib.error.HTTPError as err:
    if err.code != 404:
        raise

data = dict(fixed)
data.update({k: secrets.token_urlsafe(30) for k in random_keys})
labels = json.loads(os.environ.get("LABELS", "{}"))
call("POST", f"/api/v1/namespaces/{namespace}/secrets", {
    "apiVersion": "v1", "kind": "Secret", "type": "Opaque",
    "metadata": {"name": name, "labels": labels},
    "data": {k: base64.b64encode(v.encode()).decode() for k, v in data.items()},
})
print(f"secret {name} created")
