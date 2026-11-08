#!/usr/bin/env python3
"""A2A entry of the pod: checks the caller's bearer JWT, then streams to the gateway's A2A server.

The gateway serves A2A on loopback only. This proxy admits a ServiceAccount token from a
namespace <tenant> or <tenant>-*, or an OIDC token whose groups claim holds <tenant> or
<tenant>-*; kubelet and the identity provider issue and rotate those tokens, so no
credential is stored. Agent cards are public. Runs on the Hermes image (PyJWT).
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
        conn = http.client.HTTPConnection("127.0.0.1", UPSTREAM_PORT, timeout=3700)
        try:
            conn.request(self.command, self.path, body=body, headers=headers)
            resp = conn.getresponse()
        except OSError as exc:
            return self._deny(502, f"gateway: {exc}")
        self.send_response(resp.status, resp.reason)
        for k, v in resp.getheaders():
            if k.lower() not in DROP and k.lower() != "content-length":
                self.send_header(k, v)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        while True:
            chunk = resp.read1(65536)
            if not chunk:
                break
            self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
            self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n")
        conn.close()

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _proxy

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Proxy).serve_forever()
