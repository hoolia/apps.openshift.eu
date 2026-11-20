"""Signed events to the Odoo bridge of Chatwoot.

An event carries record ids only; the bridge reads the record over the JSON-2 API. The body
is signed with HMAC-SHA256 over "<timestamp>.<body>". The URL is the system parameter
chatwoot_bridge.url; the secret comes from the environment of the Odoo process (a file
mounted from a Secret, or a variable), never from the database. Events are written to the
outbox model chatwoot.bridge.event and delivered by a cron (event.py).
"""
import hashlib
import hmac
import logging
import os
import time
import uuid
from urllib.parse import urlsplit, urlunsplit

_logger = logging.getLogger(__name__)

PARAM_URL = "chatwoot_bridge.url"
ENV_SECRET_FILE = "CHATWOOT_BRIDGE_SECRET_FILE"
ENV_SECRET = "CHATWOOT_BRIDGE_SECRET"
MIN_SECRET_LENGTH = 32
SIGNATURE_HEADER = "X-Chatwoot-Bridge-Signature"
TIMESTAMP_HEADER = "X-Chatwoot-Bridge-Timestamp"
EVENT_HEADER = "X-Chatwoot-Bridge-Event"
DELIVERY_HEADER = "X-Chatwoot-Bridge-Delivery"
TOLERANCE_SECONDS = 300
TIMEOUT = (2, 3)


def _bytes(value):
    return value.encode() if isinstance(value, str) else value


def sign(secret, timestamp, body):
    """Return the signature header value for a body sent at a Unix timestamp."""
    mac = hmac.new(_bytes(secret), b"%d." % int(timestamp) + _bytes(body), hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def _timestamp(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, bytes):
        try:
            value = value.decode("ascii")
        except UnicodeDecodeError:
            return None
    # int() also accepts non-ASCII digits and signs; the header is ASCII digits only
    if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 12:
        return int(value)
    return None


def verify(secret, timestamp, body, signature, now=None, tolerance=TOLERANCE_SECONDS):
    """Reference check for the receiving side: a valid signature inside the time window.

    Any malformed input (non-ASCII, wrong type, bad timestamp) returns False, never raises.
    """
    if not secret or not signature or not isinstance(signature, (str, bytes)):
        return False
    ts = _timestamp(timestamp)
    if ts is None:
        return False
    now = time.time() if now is None else now
    if abs(now - ts) > tolerance:
        return False
    try:
        given = signature.encode("ascii") if isinstance(signature, str) else bytes(signature)
        given.decode("ascii")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return False
    return hmac.compare_digest(sign(secret, ts, body).encode("ascii"), given)


def log_url(url):
    """The URL as it may appear in a log line: scheme, host and path only."""
    parts = urlsplit(url or "")
    return urlunsplit((parts.scheme, parts.hostname or "", parts.path, "", ""))


def bridge_url(env, warn=True):
    """The bridge URL when set and safe, else None. An unset URL switches the bridge off."""
    url = (env["ir.config_parameter"].sudo().get_param(PARAM_URL) or "").strip()
    if not url:
        return None
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        if warn:
            _logger.warning("Bridge URL %s is not http(s); no event sent", log_url(url))
        return None
    # credentials belong in the signature, never in a URL that gets logged
    if parts.username or parts.password or parts.query:
        if warn:
            _logger.warning("Bridge URL %s carries credentials or a query; no event sent", log_url(url))
        return None
    return url


def secret_from_env(file_var=ENV_SECRET_FILE, value_var=ENV_SECRET):
    """The HMAC key from the process environment, or None.

    The file variable (a file mounted from a Secret) wins over the value variable. The file
    is read on every delivery, so a rotated Secret takes effect without a restart.
    """
    path = os.environ.get(file_var)
    secret = None
    if path:
        try:
            with open(path, encoding="utf-8") as handle:
                secret = handle.read().strip()
        except OSError as e:
            _logger.warning("Bridge secret file %s is not readable: %s", path, type(e).__name__)
            return None
    else:
        secret = (os.environ.get(value_var) or "").strip()
    if not secret:
        return None
    if len(secret) < MIN_SECRET_LENGTH:
        _logger.warning("Bridge secret is shorter than %d characters; no event sent", MIN_SECRET_LENGTH)
        return None
    return secret


def post(url, secret, event, body):
    """POST one signed body; returns (delivery id, error) where error is None on a 2xx.

    Never raises: an error is the exception class name or the HTTP status, never a body,
    header or secret. Redirects are not followed, so the signed request reaches only the
    configured URL.
    """
    import requests  # noqa: PLC0415

    timestamp = int(time.time())
    delivery = str(uuid.uuid4())
    headers = {
        "Content-Type": "application/json",
        EVENT_HEADER: event,
        DELIVERY_HEADER: delivery,
        TIMESTAMP_HEADER: str(timestamp),
        SIGNATURE_HEADER: sign(secret, timestamp, body),
    }
    try:
        response = requests.post(url, data=_bytes(body), headers=headers, timeout=TIMEOUT, allow_redirects=False)
    except Exception as e:  # noqa: BLE001
        return delivery, type(e).__name__
    status = getattr(response, "status_code", None)
    if isinstance(status, int) and 200 <= status < 300:
        return delivery, None
    return delivery, "HTTP %s" % status
