#!/usr/bin/env python3
"""Turn a caller's verified token into a SonarQube user token of the caller's own.

An MCP gateway sends every request's Authorization header here (external authorization).
The broker verifies the JWT itself against the configured issuers, maps it to one SonarQube
login per agent, creates that user on first use, mints a user token for it and answers 200
with `Authorization: Bearer <token>`, which the gateway sends on to the MCP server.
Standard library only.
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import json
import logging
import os
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

LOGIN_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")
SERVICE_ACCOUNT_PREFIX = "service-account-"
TOKEN_PREFIX = "mcp-broker-"
# DigestInfo of SHA-256 in an RSASSA-PKCS1-v1_5 signature
SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")
LEEWAY = 60


class Denied(Exception):
    """The caller gets no credential. Carries the HTTP status to answer."""

    def __init__(self, message: str, status: int = 401) -> None:
        super().__init__(message)
        self.status = status


class UpstreamError(Exception):
    """SonarQube or an issuer failed."""


def b64url(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def b64int(data: str) -> int:
    return int.from_bytes(b64url(data), "big")


def rsa_verify(n: int, e: int, message: bytes, signature: bytes) -> bool:
    """RSASSA-PKCS1-v1_5 with SHA-256."""
    k = (n.bit_length() + 7) // 8
    if len(signature) != k:
        return False
    em = pow(int.from_bytes(signature, "big"), e, n).to_bytes(k, "big")
    t = SHA256_DIGEST_INFO + hashlib.sha256(message).digest()
    if k < len(t) + 11:
        return False
    return em == b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t


def _context(ca_file: str | None) -> ssl.SSLContext:
    return ssl.create_default_context(cafile=ca_file) if ca_file else ssl.create_default_context()


@dataclass
class Issuer:
    issuer: str
    jwks_uri: str
    kind: str = "oidc"
    audiences: list[str] = field(default_factory=list)
    ca_file: str | None = None
    keys: dict[str, tuple[int, int]] = field(default_factory=dict)
    fetched: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def key(self, kid: str | None) -> tuple[int, int]:
        with self.lock:
            stale = time.time() - self.fetched > 300
            missing = kid not in self.keys if kid else not self.keys
            if stale or (missing and time.time() - self.fetched > 10):
                self.keys = self._fetch()
                self.fetched = time.time()
            if kid:
                if kid not in self.keys:
                    raise Denied("unknown signing key")
                return self.keys[kid]
            if len(self.keys) != 1:
                raise Denied("token names no signing key")
            return next(iter(self.keys.values()))

    def _fetch(self) -> dict[str, tuple[int, int]]:
        try:
            with urllib.request.urlopen(self.jwks_uri, timeout=10, context=_context(self.ca_file)) as r:
                jwks = json.load(r)
        except (OSError, ValueError) as exc:
            raise UpstreamError("JWKS of %s: %s" % (self.issuer, exc)) from exc
        return {k.get("kid", ""): (b64int(k["n"]), b64int(k["e"]))
                for k in jwks.get("keys", []) if k.get("kty") == "RSA" and k.get("use", "sig") == "sig"}


def verify(token: str, issuers: dict[str, Issuer], now: float | None = None) -> tuple[Issuer, dict[str, Any]]:
    parts = token.split(".")
    if len(parts) != 3:
        raise Denied("not a JWT")
    try:
        header = json.loads(b64url(parts[0]))
        claims = json.loads(b64url(parts[1]))
        signature = b64url(parts[2])
    except (ValueError, TypeError) as exc:
        raise Denied("malformed JWT") from exc
    if header.get("alg") != "RS256":
        raise Denied("unsupported algorithm")
    issuer = issuers.get(claims.get("iss", ""))
    if issuer is None:
        raise Denied("unknown issuer")
    n, e = issuer.key(header.get("kid"))
    if not rsa_verify(n, e, (parts[0] + "." + parts[1]).encode(), signature):
        raise Denied("bad signature")
    now = time.time() if now is None else now
    if not isinstance(claims.get("exp"), (int, float)) or claims["exp"] < now - LEEWAY:
        raise Denied("expired")
    if isinstance(claims.get("nbf"), (int, float)) and claims["nbf"] > now + LEEWAY:
        raise Denied("not yet valid")
    if issuer.audiences:
        aud = claims.get("aud")
        auds = aud if isinstance(aud, list) else [aud]
        if not set(auds) & set(issuer.audiences):
            raise Denied("wrong audience")
    return issuer, claims


def identity(issuer: Issuer, claims: dict[str, Any], cluster: str) -> str:
    """One login per agent: <serviceaccount>.<namespace>.<cluster>, whichever token it uses."""
    if issuer.kind == "kubernetes":
        sa = (claims.get("kubernetes.io") or {}).get("serviceaccount") or {}
        ns = (claims.get("kubernetes.io") or {}).get("namespace", "")
        login = "%s.%s.%s" % (sa.get("name", ""), ns, cluster)
    else:
        # client credentials of an agent client: the token names the client itself, as
        # preferred_username or Keycloak's service-account-<client>; people keep their own login
        user = str(claims.get("preferred_username", ""))
        client = str(claims.get("azp") or claims.get("client_id") or "")
        if not client or user not in (client, SERVICE_ACCOUNT_PREFIX + client):
            raise Denied("only agent identities are served (preferred_username %r)" % user[:100], 403)
        login = client
    login = login.lower()
    if not LOGIN_RE.match(login) or login.startswith(".") or ".." in login:
        raise Denied("identity is no valid login", 403)
    return login


class SonarQube:
    def __init__(self, url: str, token: str, ca_file: str | None = None) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.context = _context(ca_file)

    def call(self, method: str, path: str, params: dict[str, str] | None = None,
             token: str | None = None) -> Any:
        query = urllib.parse.urlencode(params or {})
        url = self.url + path + ("?" + query if query and method == "GET" else "")
        data = query.encode() if method == "POST" else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": "Bearer " + (token or self.token),
            "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=20, context=self.context) as r:
                body = r.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:300].decode(errors="replace")
            raise UpstreamError("%s %s: HTTP %d %s" % (method, path, exc.code, detail)) from exc
        except OSError as exc:
            raise UpstreamError("%s %s: %s" % (method, path, exc)) from exc
        return json.loads(body) if body.strip() else {}

    def valid(self, token: str) -> bool:
        try:
            return bool(self.call("GET", "/api/authentication/validate", token=token).get("valid"))
        except UpstreamError:
            return False

    def ensure_user(self, login: str, groups: list[str]) -> None:
        found = [u for u in self.call("GET", "/api/users/search", {"q": login}).get("users", [])
                 if u.get("login") == login]
        if found:
            user = found[0]
            # never mint for an account the broker did not create: a local or SSO login
            if user.get("local", True) or user.get("externalProvider") not in (None, "sonarqube"):
                raise Denied("login %s belongs to another account" % login, 403)
        else:
            self.call("POST", "/api/users/create", {"login": login, "name": login, "local": "false"})
            logging.info("created SonarQube user %s", login)
            for group in groups:
                try:
                    self.call("POST", "/api/user_groups/add_user", {"name": group, "login": login})
                except UpstreamError as exc:
                    logging.warning("adding %s to group %s: %s", login, group, exc)

    def mint(self, login: str, days: int) -> str:
        for t in self.call("GET", "/api/user_tokens/search", {"login": login}).get("userTokens", []):
            if t.get("name", "").startswith(TOKEN_PREFIX):
                self.call("POST", "/api/user_tokens/revoke", {"login": login, "name": t["name"]})
        expires = (datetime.date.today() + datetime.timedelta(days=days)).isoformat()
        result = self.call("POST", "/api/user_tokens/generate", {
            "login": login, "name": "%s%d" % (TOKEN_PREFIX, int(time.time())), "expirationDate": expires})
        return result["token"]


@dataclass
class Entry:
    token: str
    expires: float
    checked: float


class Broker:
    def __init__(self, sonar: SonarQube, issuers: dict[str, Issuer], cluster: str,
                 allowed: str = "", groups: list[str] | None = None, days: int = 30) -> None:
        self.sonar = sonar
        self.issuers = issuers
        self.cluster = cluster
        self.allowed = re.compile(allowed) if allowed else None
        self.groups = groups or []
        self.days = days
        self.cache: dict[str, Entry] = {}
        self.locks: dict[str, threading.Lock] = {}
        self.guard = threading.Lock()

    def credential(self, authorization: str) -> tuple[str, str]:
        if not authorization.lower().startswith("bearer "):
            raise Denied("no bearer token")
        issuer, claims = verify(authorization[7:].strip(), self.issuers)
        login = identity(issuer, claims, self.cluster)
        if self.allowed and not self.allowed.fullmatch(login):
            raise Denied("identity %s is not allowed" % login, 403)
        with self.guard:
            lock = self.locks.setdefault(login, threading.Lock())
        with lock:
            now = time.time()
            entry = self.cache.get(login)
            if entry and entry.expires > now:
                if now - entry.checked < 900:
                    return login, entry.token
                # a token revoked or a user removed in SonarQube is minted again
                if self.sonar.valid(entry.token):
                    entry.checked = now
                    return login, entry.token
            self.sonar.ensure_user(login, self.groups)
            token = self.sonar.mint(login, self.days)
            self.cache[login] = Entry(token, now + (self.days - 1) * 86400, now)
            logging.info("minted a token for %s", login)
            return login, token


class Handler(BaseHTTPRequestHandler):
    broker: Broker

    def log_message(self, fmt: str, *args: Any) -> None:
        pass

    def _answer(self, status: int, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _handle(self) -> None:
        if self.path == "/healthz":
            self._answer(200)
            return
        try:
            login, token = self.broker.credential(self.headers.get("Authorization", ""))
        except Denied as exc:
            logging.info("denied: %s", exc)
            self._answer(exc.status)
            return
        except UpstreamError as exc:
            logging.error("upstream: %s", exc)
            self._answer(502)
            return
        self._answer(200, {"Authorization": "Bearer " + token, "X-SonarQube-Login": login})

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _handle


def from_env() -> Broker:
    def need(name: str) -> str:
        value = os.environ.get(name, "").strip()
        if not value:
            sys.exit("%s is required" % name)
        return value

    issuers = {}
    for spec in json.loads(need("ISSUERS")):
        issuers[spec["issuer"]] = Issuer(issuer=spec["issuer"], jwks_uri=spec["jwksUri"],
                                         kind=spec.get("kind", "oidc"), audiences=spec.get("audiences") or [],
                                         ca_file=spec.get("caFile") or None)
    sonar = SonarQube(need("SONARQUBE_URL"), need("SONARQUBE_ADMIN_TOKEN"),
                      os.environ.get("SONARQUBE_CA_FILE") or None)
    groups = [g.strip() for g in os.environ.get("SONARQUBE_GROUPS", "").split(",") if g.strip()]
    return Broker(sonar, issuers, need("CLUSTER"), os.environ.get("ALLOWED_IDENTITY", ""),
                  groups, int(os.environ.get("TOKEN_DAYS", "30")))


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    Handler.broker = from_env()
    port = int(os.environ.get("PORT", "9090"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    logging.info("listening on %d", port)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
