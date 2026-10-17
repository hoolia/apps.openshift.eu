#!/usr/bin/env python3
"""Forward every request to the gateway with this agent's Keycloak token.

OpenCode talks to 127.0.0.1:PORT for models (/v1) and A2A calls (/a2a/...).
The proxy fetches a client-credentials token, caches it until shortly before
it expires, and replaces any incoming Authorization header with it. Response
bodies are relayed as they arrive so model streams (SSE) stay live.
"""
from __future__ import annotations

import http.client
import json
import logging
import os
import sys
import threading
import time
import urllib.parse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

TOKEN_TIMEOUT = 5
UPSTREAM_TIMEOUT = 600
CHUNK_SIZE = 8192
HOP_BY_HOP = frozenset({
    "connection", "transfer-encoding", "keep-alive", "te", "trailer", "upgrade",
    "proxy-authenticate", "proxy-authorization", "proxy-connection",
})


class ConfigError(Exception):
    """Startup configuration is unusable."""


class TokenError(Exception):
    """The Keycloak token endpoint did not hand out a token."""


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logging.warning("ignoring unparsable %s=%r, using %d", name, raw, default)
        return default


@dataclass(frozen=True)
class Settings:
    token_url: str
    client_id: str
    client_secret_file: str
    upstream: str
    port: int
    refresh_margin: int

    def validate(self) -> None:
        missing = [
            name
            for name, value in (
                ("TOKEN_URL", self.token_url),
                ("CLIENT_ID", self.client_id),
                ("CLIENT_SECRET_FILE", self.client_secret_file),
                ("UPSTREAM", self.upstream),
            )
            if not value
        ]
        if missing:
            raise ConfigError(f"{', '.join(missing)} unset")

    @staticmethod
    def from_env() -> Settings:
        return Settings(
            token_url=os.environ.get("TOKEN_URL", "").strip(),
            client_id=os.environ.get("CLIENT_ID", "").strip(),
            client_secret_file=os.environ.get("CLIENT_SECRET_FILE", "").strip(),
            upstream=os.environ.get("UPSTREAM", "").strip().rstrip("/"),
            port=_env_int("PORT", 8099),
            refresh_margin=_env_int("REFRESH_MARGIN_SECONDS", 60),
        )


def _connect(url: urllib.parse.SplitResult, timeout: float) -> http.client.HTTPConnection:
    port = url.port
    if url.scheme == "https":
        return http.client.HTTPSConnection(url.hostname, port, timeout=timeout)
    return http.client.HTTPConnection(url.hostname, port, timeout=timeout)


class TokenSource:
    """Client-credentials token, cached until margin seconds before expiry."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires = 0.0

    def token(self) -> str:
        with self._lock:
            if self._token and time.monotonic() < self._expires - self._settings.refresh_margin:
                return self._token
            self._fetch()
            assert self._token is not None
            return self._token

    def invalidate(self) -> None:
        with self._lock:
            self._token = None

    def _fetch(self) -> None:
        settings = self._settings
        try:
            # The Secret may rotate, so it is read at each fetch.
            with open(settings.client_secret_file, encoding="utf-8") as handle:
                secret = handle.read().strip()
            form = urllib.parse.urlencode({
                "grant_type": "client_credentials",
                "client_id": settings.client_id,
                "client_secret": secret,
            })
            url = urllib.parse.urlsplit(settings.token_url)
            conn = _connect(url, TOKEN_TIMEOUT)
            try:
                path = url.path or "/"
                if url.query:
                    path += f"?{url.query}"
                conn.request("POST", path, body=form, headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                })
                resp = conn.getresponse()
                payload = resp.read()
            finally:
                conn.close()
            if resp.status != 200:
                raise TokenError(f"Keycloak token endpoint returned HTTP {resp.status}")
            data = json.loads(payload)
            token = data["access_token"]
            lifetime = float(data.get("expires_in", 0))
        except TokenError:
            self._token = None
            raise
        except (OSError, http.client.HTTPException, ValueError, KeyError) as err:
            self._token = None
            raise TokenError(f"Keycloak token request failed: {type(err).__name__}") from err
        self._token = token
        self._expires = time.monotonic() + lifetime


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "token-proxy"
    sys_version = ""
    settings: Settings
    tokens: TokenSource

    def log_message(self, fmt: str, *args: Any) -> None:
        logging.info("%s", fmt % args)

    def _send(self, status: int, payload: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _error(self, status: int, message: str) -> None:
        self._send(status, json.dumps({"error": message}).encode())

    def _read_body(self) -> bytes:
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            body = b""
            while True:
                size = int(self.rfile.readline().split(b";")[0].strip() or b"0", 16)
                if size == 0:
                    while self.rfile.readline().strip():
                        pass
                    return body
                body += self.rfile.read(size)
                self.rfile.readline()
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _forward(self) -> None:
        if self.path == "/healthz":
            try:
                self.tokens.token()
            except TokenError as err:
                self._error(503, str(err))
                return
            self._send(200, b"ok", "text/plain")
            return
        body = self._read_body()
        try:
            token = self.tokens.token()
        except TokenError as err:
            logging.error("%s", err)
            self._error(502, str(err))
            return
        upstream = urllib.parse.urlsplit(self.settings.upstream)
        headers = {
            name: value
            for name, value in self.headers.items()
            if name.lower() not in HOP_BY_HOP | {"host", "authorization", "content-length"}
        }
        headers["Host"] = upstream.netloc
        headers["Authorization"] = f"Bearer {token}"
        if body or self.command in ("POST", "PUT", "PATCH"):
            headers["Content-Length"] = str(len(body))
        conn = _connect(upstream, UPSTREAM_TIMEOUT)
        try:
            conn.request(self.command, (upstream.path.rstrip("/") + self.path), body=body or None, headers=headers)
            resp = conn.getresponse()
            self._relay(resp)
        except (OSError, http.client.HTTPException) as err:
            logging.error("upstream request failed: %s", type(err).__name__)
            if not self.wfile.closed and not getattr(self, "_started", False):
                self._error(502, "upstream unreachable")
            self.close_connection = True
        finally:
            conn.close()

    def _relay(self, resp: http.client.HTTPResponse) -> None:
        if resp.status == 401:
            self.tokens.invalidate()
        self._started = True
        self.send_response(resp.status)
        has_length = False
        declared = 0
        for name, value in resp.getheaders():
            lowered = name.lower()
            if lowered in HOP_BY_HOP:
                continue
            if lowered == "content-length":
                has_length = True
                declared = int(value) if value.isdigit() else 0
            self.send_header(name, value)
        chunked = not has_length and self.request_version == "HTTP/1.1"
        if chunked:
            self.send_header("Transfer-Encoding", "chunked")
        elif not has_length:
            self.close_connection = True
        self.end_headers()
        relayed = 0
        while True:
            data = resp.read1(CHUNK_SIZE)
            if not data:
                break
            relayed += len(data)
            if chunked:
                self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
            else:
                self.wfile.write(data)
            self.wfile.flush()
        # read1 returns b"" on a truncated Content-Length body without raising.
        if has_length and self.command != "HEAD" and relayed < declared:
            self.close_connection = True
        if chunked:
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()

    def handle_one_request(self) -> None:
        self._started = False
        super().handle_one_request()

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_HEAD = _forward


def build_server(settings: Settings, port: int | None = None) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {
        "settings": settings,
        "tokens": TokenSource(settings),
    })
    server = ThreadingHTTPServer(("127.0.0.1", settings.port if port is None else port), handler)
    server.daemon_threads = True
    return server


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(levelname)s %(message)s",
        stream=sys.stderr,
    )
    settings = Settings.from_env()
    try:
        settings.validate()
    except ConfigError as err:
        logging.error("%s", err)
        return 2
    server = build_server(settings)
    logging.info("token-proxy listening on 127.0.0.1:%d, upstream %s", settings.port, settings.upstream)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("shutting down")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
