"""Keep mcp_servers of the Hermes config.yaml in step with the RemoteMCPServers the
ServiceAccount may list.

Entries this script wrote are listed in STATE; it adds, changes and removes only those, so a
server the user adds in the dashboard stays. A server on UPSTREAM goes through the token
proxy, which adds the pod's ServiceAccount token. A server annotated <auth>: oauth2 is one
each user authorizes in the dashboard; its callback is the dashboard behind the Route.
"""

import base64
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from ruamel.yaml import YAML

SA = "/var/run/secrets/kubernetes.io/serviceaccount"
KUBE = "https://{}:{}".format(os.environ.get("KUBERNETES_SERVICE_HOST", ""),
                              os.environ.get("KUBERNETES_SERVICE_PORT", "443"))
HOME = os.environ.get("HERMES_HOME", "/opt/data/home")
CONFIG = os.path.join(HOME, "config.yaml")
STATE = os.path.join(HOME, ".mcp-sync.json")
UPSTREAM = os.environ.get("UPSTREAM", "").rstrip("/")
PROXY = "http://127.0.0.1:{}".format(os.environ.get("TOKEN_PROXY_PORT", "8099"))
CALLBACK = os.environ.get("OAUTH_CALLBACK_BASE", "").rstrip("/")
INTERVAL = int(os.environ.get("INTERVAL", "60"))
SELECTOR = os.environ.get("LABEL_SELECTOR", "")
OTHER_PROJECTS = os.environ.get("OTHER_PROJECTS", "true") == "true"
LABEL_DOMAIN = os.environ.get("LABEL_DOMAIN", "").strip("/")
BUNDLES = set(filter(None, os.environ.get("BUNDLES", "").split(",")))
SERVERS = set(filter(None, os.environ.get("SERVERS", "").split(",")))
EXCLUDE = set(filter(None, os.environ.get("EXCLUDE", "").split(",")))
AUTH_ANNOTATION = "openhands.dev/auth"
SCOPES_ANNOTATION = "openhands.dev/oauth-scopes"
CLIENT_ANNOTATION = "openhands.dev/oauth-client"

yaml = YAML()
yaml.preserve_quotes = True
yaml.width = 4096


def log(msg: str) -> None:
    print(f"mcp-sync: {msg}", file=sys.stderr, flush=True)


def kube(path: str) -> tuple[int, dict]:
    with open(f"{SA}/token") as token:
        req = urllib.request.Request(KUBE + path, headers={"Authorization": "Bearer " + token.read().strip()})
    ctx = ssl.create_default_context(cafile=f"{SA}/ca.crt")
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as err:
        return err.code, {}
    except (OSError, ValueError) as err:
        log(f"GET {path}: {err}")
        return 0, {}


def list_servers(namespace: str) -> list[dict] | None:
    query = "?labelSelector=" + urllib.parse.quote(SELECTOR) if SELECTOR else ""
    status, body = kube(f"/apis/kagent.dev/v1alpha2/namespaces/{namespace}/remotemcpservers{query}")
    return body.get("items", []) if status == 200 else None


def discovered(namespace: str) -> list[dict] | None:
    """Own namespace first; None when the own namespace cannot be listed (keep the config)."""
    own = list_servers(namespace)
    if own is None:
        return None
    found = list(own)
    if OTHER_PROJECTS:
        status, projects = kube("/apis/project.openshift.io/v1/projects")
        for project in projects.get("items", []) if status == 200 else []:
            ns = project["metadata"]["name"]
            if ns != namespace:
                found.extend(list_servers(ns) or [])
    return found


def selected(obj: dict) -> bool:
    meta = obj["metadata"]
    if meta["name"] in EXCLUDE or f"{meta['namespace']}/{meta['name']}" in EXCLUDE:
        return False
    labels = meta.get("labels") or {}
    if not LABEL_DOMAIN or labels.get(f"{LABEL_DOMAIN}/addon") != "true":
        return True
    server = labels.get(f"{LABEL_DOMAIN}/server") or obj["metadata"]["name"]
    return server in SERVERS or labels.get(f"{LABEL_DOMAIN}/bundle", "") in BUNDLES


def oauth_block(obj: dict, name: str) -> tuple[dict | None, str]:
    meta = obj["metadata"]
    annotations = meta.get("annotations") or {}
    block = {}
    if CALLBACK:
        block["redirect_uri"] = f"{CALLBACK}/api/mcp/oauth/callback/{urllib.parse.quote(name, safe='')}"
    scopes = " ".join(annotations.get(SCOPES_ANNOTATION, "").split())
    if scopes:
        block["scope"] = scopes
    client = annotations.get(CLIENT_ANNOTATION, "")
    if client:
        status, secret = kube(f"/api/v1/namespaces/{meta['namespace']}/secrets/{client}")
        if status != 200:
            return None, f"client Secret {client}: HTTP {status}"
        data = secret.get("data") or {}
        try:
            client_id, client_secret = (base64.b64decode(data.get(k, "")).decode()
                                        for k in ("client_id", "client_secret"))
        except ValueError:
            return None, f"client Secret {client} is not readable"
        if not client_id:
            return None, f"client Secret {client} has no client_id"
        block["client_id"] = client_id
        if client_secret:
            block["client_secret"] = client_secret
    return block, ""


def to_entry(obj: dict, name: str) -> tuple[dict | None, str]:
    spec = obj.get("spec", {})
    url = spec.get("url", "")
    annotations = obj["metadata"].get("annotations") or {}
    if not url:
        return None, "has no URL"
    entry = {"url": url}
    if annotations.get(AUTH_ANNOTATION) == "oauth2":
        block, reason = oauth_block(obj, name)
        if block is None:
            return None, reason
        entry["auth"] = "oauth"
        entry["oauth"] = block
    elif UPSTREAM and url.startswith(UPSTREAM + "/"):
        entry["url"] = PROXY + url[len(UPSTREAM):]
    elif spec.get("headersFrom"):
        return None, "needs headers from a Secret"
    entry["timeout"] = 120
    return entry, ""


def wanted(namespace: str, items: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for obj in items:
        if not selected(obj):
            continue
        meta = obj["metadata"]
        name = meta["name"]
        if name in out:
            if meta["namespace"] == namespace:
                continue
            name = f"{meta['namespace']}-{name}"
        entry, reason = to_entry(obj, name)
        if entry is None:
            log(f"skipped {meta['namespace']}/{meta['name']}: {reason}")
            continue
        out[name] = entry
    return out


def plain(value):
    return json.loads(json.dumps(value))


def sync(namespace: str) -> None:
    items = discovered(namespace)
    if items is None:
        log("RemoteMCPServers not listable in the own namespace; config left as is")
        return
    want = wanted(namespace, items)
    try:
        with open(STATE) as fh:
            owned = set(json.load(fh))
    except (OSError, ValueError):
        owned = set()
    try:
        with open(CONFIG) as fh:
            cfg = yaml.load(fh) or {}
    except FileNotFoundError:
        cfg = {}
    servers = cfg.get("mcp_servers") or {}
    changed = False
    skipped = set()
    for name in list(servers):
        if name in owned and name not in want:
            del servers[name]
            changed = True
            log(f"removed {name}")
    for name, entry in want.items():
        have = servers.get(name)
        if name not in owned and have is not None:
            skipped.add(name)  # the user's own entry of that name wins
            continue
        merged = dict(plain(have) if have else {})
        merged.update(entry)
        if have is not None and merged.get("auth") != "oauth":
            merged.pop("oauth", None)
        if plain(have) != merged:
            servers[name] = merged
            changed = True
            log(f"{'updated' if have else 'added'} {name} {entry['url']}")
    if changed:
        cfg["mcp_servers"] = servers
        tmp = CONFIG + ".mcp-sync"
        with open(tmp, "w") as fh:
            yaml.dump(cfg, fh)
        os.chmod(tmp, 0o600)
        os.replace(tmp, CONFIG)
    mine = sorted(n for n in want if n in owned or n not in skipped)
    if set(mine) != owned:
        with open(STATE + ".tmp", "w") as fh:
            json.dump(mine, fh)
        os.replace(STATE + ".tmp", STATE)


def main() -> None:
    with open(f"{SA}/namespace") as fh:
        namespace = fh.read().strip()
    while True:
        try:
            sync(namespace)
        except Exception as err:  # keep the loop alive; the next pass retries
            log(f"pass failed: {err!r}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
