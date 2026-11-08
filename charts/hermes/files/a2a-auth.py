#!/usr/bin/env python3
"""A2A entry of the pod: checks the caller's bearer JWT, then streams to the gateway's A2A server.

The gateway serves A2A on loopback only. This proxy admits a ServiceAccount token from a
namespace <tenant> or <tenant>-*, or an OIDC token whose groups claim holds <tenant> or
<tenant>-*; kubelet and the identity provider issue and rotate those tokens, so no
credential is stored. Agent cards are public. A caller of A2A v0.3 (header A2A-Version 0.x,
such as kagent) gets the gateway's v1.0 answers in v0.3 shape. Runs on the Hermes image (PyJWT).
"""
import http.client
import json
import os
import ssl
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import jwt

PORT = int(os.environ.get("PORT", "8090"))
UPSTREAM_PORT = int(os.environ.get("UPSTREAM_PORT", "9900"))
TENANT = os.environ["TENANT"]
ISSUERS = json.loads(os.environ.get("JWT_ISSUERS", "[]") or "[]")
ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "PS256"]
CARDS = {"/.well-known/agent-card.json", "/.well-known/agent.json"}
# hop headers, and browser headers the gateway's loopback mode refuses
DROP = {"connection", "keep-alive", "proxy-connection", "transfer-encoding", "te", "trailer", "upgrade", "host",
        "authorization", "origin", "referer", "sec-fetch-site", "sec-fetch-mode", "sec-fetch-dest"}


class TokenJWKClient(jwt.PyJWKClient):
    """Sends the pod's ServiceAccount token, re-read on every fetch since it rotates."""

    def __init__(self, uri, token_file, **kwargs):
        super().__init__(uri, **kwargs)
        self._token_file = token_file

    def fetch_data(self):
        self.headers = {"Authorization": "Bearer " + Path(self._token_file).read_text().strip()}
        return super().fetch_data()


def issuers():
    out = []
    for e in ISSUERS:
        kind = e.get("kind", "oidc")
        uri = e.get("jwks_uri") or e["issuer"].rstrip("/") + (
            "/openid/v1/jwks" if kind == "kubernetes" else "/protocol/openid-connect/certs")
        kw = {"cache_jwk_set": True, "lifespan": 300}
        if e.get("ca_file"):
            kw["ssl_context"] = ssl.create_default_context(cafile=e["ca_file"])
        client = TokenJWKClient(uri, e["token_file"], **kw) if e.get("token_file") else jwt.PyJWKClient(uri, **kw)
        out.append((e["issuer"], kind, tuple(e.get("audiences") or ()), client))
    return out


TRUSTED = issuers()


def member(value: str) -> bool:
    return value == TENANT or value.startswith(TENANT + "-")


def identity(header: str) -> str | None:
    if not header.lower().startswith("bearer "):
        return None
    token = header[7:].strip()
    if token.count(".") != 2:
        return None
    try:
        iss = jwt.decode(token, options={"verify_signature": False}).get("iss")
    except jwt.PyJWTError:
        return None
    for issuer, kind, audiences, client in TRUSTED:
        if issuer != iss:
            continue
        try:
            claims = jwt.decode(token, client.get_signing_key_from_jwt(token).key, algorithms=ALGORITHMS,
                                issuer=issuer, audience=list(audiences) or None,
                                options={"verify_aud": bool(audiences), "require": ["exp", "iss"]})
        except (jwt.PyJWTError, OSError):
            return None
        if kind == "kubernetes":
            k8s = claims.get("kubernetes.io") or {}
            ns = k8s.get("namespace") or ""
            sa = (k8s.get("serviceaccount") or {}).get("name")
            return f"system:serviceaccount:{ns}:{sa}" if member(ns) else None
        groups = claims.get("groups") or []
        groups = [groups] if isinstance(groups, str) else groups
        if any(member(str(g).lstrip("/")) for g in groups):
            return str(claims.get("email") or claims.get("preferred_username") or claims.get("sub"))
        return None
    return None


# --- A2A v0.3 callers (kagent): the gateway speaks v1.0 only ---------------------------

TERMINAL = {"completed", "failed", "canceled", "rejected", "input-required", "auth-required"}


def v03_state(state: str) -> str:
    return state.removeprefix("TASK_STATE_").lower().replace("_", "-") if state.startswith("TASK_STATE_") else state


def v03_part(part: dict) -> dict:
    if "kind" in part:
        return part
    kind = "text" if "text" in part else "data" if "data" in part else "file"
    return {"kind": kind, **part}


def v03_message(msg: dict) -> dict:
    out = {**msg, "kind": "message", "role": {"ROLE_AGENT": "agent", "ROLE_USER": "user"}.get(msg.get("role"), msg.get("role"))}
    out["parts"] = [v03_part(p) for p in msg.get("parts") or []]
    return out


def v03_status(status: dict) -> dict:
    out = {**status, "state": v03_state(status.get("state", ""))}
    if isinstance(status.get("message"), dict):
        out["message"] = v03_message(status["message"])
    return out


def v03_artifact(art: dict) -> dict:
    return {**art, "parts": [v03_part(p) for p in art.get("parts") or []]}


def v03_task(task: dict) -> dict:
    out = {**task, "kind": "task", "status": v03_status(task.get("status") or {})}
    if "artifacts" in task:
        out["artifacts"] = [v03_artifact(a) for a in task["artifacts"] or []]
    if "history" in task:
        out["history"] = [v03_message(m) for m in task["history"] or []]
    return out


def v03_result(result):
    if not isinstance(result, dict):
        return result
    if "statusUpdate" in result:
        ev = result["statusUpdate"]
        status = v03_status(ev.get("status") or {})
        return {**ev, "kind": "status-update", "status": status, "final": status.get("state") in TERMINAL}
    if "artifactUpdate" in result:
        ev = result["artifactUpdate"]
        return {**ev, "kind": "artifact-update", "artifact": v03_artifact(ev.get("artifact") or {})}
    if set(result) == {"task"}:
        return v03_task(result["task"])
    if set(result) == {"message"}:
        return v03_message(result["message"])
    if "status" in result and "id" in result:
        return v03_task(result)
    if "messageId" in result and "parts" in result:
        return v03_message(result)
    if isinstance(result.get("tasks"), list):
        return {**result, "tasks": [v03_task(t) for t in result["tasks"]]}
    return result


def v03_envelope(raw: bytes) -> bytes:
    try:
        env = json.loads(raw)
    except ValueError:
        return raw
    if isinstance(env, dict) and "result" in env:
        env["result"] = v03_result(env["result"])
    return json.dumps(env).encode()


class Proxy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _deny(self, code, text):
        body = json.dumps({"error": text}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if code == 401:
            self.send_header("WWW-Authenticate", "Bearer")
        self.end_headers()
        self.wfile.write(body)

    def _proxy(self):
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            return self._deny(200, "ok")
        if not (self.command == "GET" and path in CARDS):
            who = identity(self.headers.get("Authorization", ""))
            if not who:
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                return self._deny(401, "a bearer token of the tenant is required")
            sys.stderr.write(f"a2a-auth: {self.command} {path} by {who}\n")
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        headers = {k: v for k, v in self.headers.items() if k.lower() not in DROP}
        headers["Host"] = f"127.0.0.1:{UPSTREAM_PORT}"
        # a v0.3 caller: the gateway refuses the header and answers in v1.0 shape
        legacy = self.headers.get("A2A-Version", "").startswith("0.") and self.command == "POST"
        if legacy:
            headers = {k: v for k, v in headers.items() if k.lower() != "a2a-version"}
        conn = http.client.HTTPConnection("127.0.0.1", UPSTREAM_PORT, timeout=3700)
        try:
            conn.request(self.command, self.path, body=body, headers=headers)
            resp = conn.getresponse()
        except OSError as exc:
            return self._deny(502, f"gateway: {exc}")
        stream = "text/event-stream" in (resp.getheader("Content-Type") or "")
        self.send_response(resp.status, resp.reason)
        for k, v in resp.getheaders():
            if k.lower() not in DROP and k.lower() != "content-length":
                self.send_header(k, v)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        def write(data: bytes):
            if data:
                self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
                self.wfile.flush()

        if legacy and stream:
            while line := resp.readline():
                if line.startswith(b"data:"):
                    line = b"data: " + v03_envelope(line[5:].strip()) + b"\n"
                write(line)
        elif legacy:
            write(v03_envelope(resp.read()))
        else:
            while chunk := resp.read1(65536):
                write(chunk)
        self.wfile.write(b"0\r\n\r\n")
        conn.close()

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _proxy

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Proxy).serve_forever()
