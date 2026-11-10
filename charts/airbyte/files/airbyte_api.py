"""Small client for the Airbyte API, shared by the configuration job and the MCP server."""
import json
import os
import time
import urllib.error
import urllib.request


class ApiError(Exception):
    def __init__(self, status, body):
        super().__init__(f"HTTP {status}: {body[:500]}")
        self.status = status
        self.body = body


class Airbyte:
    def __init__(self, base=None, client_id=None, client_secret=None, timeout=600):
        self.base = (base or os.environ["AIRBYTE_URL"]).rstrip("/")
        self.client_id = client_id or os.environ["AIRBYTE_CLIENT_ID"]
        self.client_secret = client_secret or os.environ["AIRBYTE_CLIENT_SECRET"]
        self.timeout = timeout
        self._token = None
        self._expires = 0.0

    def _raw(self, method, path, body=None, token=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", "Bearer " + token)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                text = resp.read().decode() or "{}"
                return json.loads(text)
        except urllib.error.HTTPError as err:
            raise ApiError(err.code, err.read().decode(errors="replace")) from None

    def token(self):
        if self._token and time.time() < self._expires:
            return self._token
        body = {"client_id": self.client_id, "client_secret": self.client_secret,
                "grant-type": "client_credentials", "grant_type": "client_credentials"}
        out = self._raw("POST", "/api/public/v1/applications/token", body)
        self._token = out["access_token"]
        self._expires = time.time() + max(30, int(out.get("expires_in", 900)) - 60)
        return self._token

    def public(self, method, path, body=None):
        return self._raw(method, "/api/public/v1" + path, body, self.token())

    def internal(self, path, body):
        return self._raw("POST", "/api/v1" + path, body, self.token())

    def pages(self, path, key="data", **params):
        params.setdefault("limit", 100)
        offset = 0
        while True:
            query = "&".join(f"{k}={v}" for k, v in {**params, "offset": offset}.items())
            out = self.public("GET", f"{path}?{query}")
            items = out.get(key) or []
            yield from items
            if len(items) < params["limit"]:
                return
            offset += params["limit"]
