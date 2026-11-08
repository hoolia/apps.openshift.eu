"""Run the Hermes dashboard, with its OpenID Connect login while the SSO Secret exists.

The Secret is an optional volume: the kubelet fills it when the Secret appears and empties
it when the Secret goes. Every INTERVAL seconds the client is read again; when it changed,
the dashboard is restarted in this container with the new login, or without one.
"""

import hashlib
import os
import signal
import subprocess
import sys
import time

SSO_DIR = os.environ.get("SSO_DIR", "")
PUBLIC_URL = os.environ.get("PUBLIC_URL", "")
KEY_ISSUER = os.environ.get("KEY_ISSUER", "sso-issuer-url")
KEY_CLIENT_ID = os.environ.get("KEY_CLIENT_ID", "sso-client-id")
KEY_CLIENT_SECRET = os.environ.get("KEY_CLIENT_SECRET", "sso-client-secret")
SCOPES = os.environ.get("SCOPES", "openid profile email")
INTERVAL = int(os.environ.get("INTERVAL", "30"))
COMMAND = sys.argv[1:]
OWN = ("HERMES_DASHBOARD_PUBLIC_URL", "HERMES_DASHBOARD_OIDC_ISSUER",
       "HERMES_DASHBOARD_OIDC_CLIENT_ID", "HERMES_DASHBOARD_OIDC_CLIENT_SECRET",
       "HERMES_DASHBOARD_OIDC_SCOPES")

child = None
stopping = False


def log(msg):
    print(f"dashboard: {msg}", flush=True)


def read(key):
    if not SSO_DIR:
        return ""
    try:
        with open(os.path.join(SSO_DIR, key)) as f:
            return f.read().strip()
    except OSError:
        return ""


def client():
    """The login settings, or None for the dashboard without a login of its own."""
    issuer, client_id = read(KEY_ISSUER), read(KEY_CLIENT_ID)
    if not (SSO_DIR and PUBLIC_URL and issuer and client_id):
        return None
    return {
        "HERMES_DASHBOARD_PUBLIC_URL": PUBLIC_URL,
        "HERMES_DASHBOARD_OIDC_ISSUER": issuer,
        "HERMES_DASHBOARD_OIDC_CLIENT_ID": client_id,
        "HERMES_DASHBOARD_OIDC_CLIENT_SECRET": read(KEY_CLIENT_SECRET),
        "HERMES_DASHBOARD_OIDC_SCOPES": SCOPES,
    }


def fingerprint(cfg):
    if cfg is None:
        return ""
    return hashlib.sha256(repr(sorted(cfg.items())).encode()).hexdigest()


def start(cfg):
    env = {k: v for k, v in os.environ.items() if k not in OWN}
    if cfg:
        env.update({k: v for k, v in cfg.items() if v})
        log(f"single sign-on on: issuer {cfg['HERMES_DASHBOARD_OIDC_ISSUER']}, "
            f"client {cfg['HERMES_DASHBOARD_OIDC_CLIENT_ID']}, public address {PUBLIC_URL}")
    elif SSO_DIR and not PUBLIC_URL:
        log("single sign-on off: no route.host")
    else:
        log("single sign-on off: no client in the Secret")
    return subprocess.Popen(COMMAND, env=env, start_new_session=True)


def stop(proc):
    if proc.poll() is not None:
        return
    os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()


def on_term(signum, frame):
    global stopping
    stopping = True
    if child is not None:
        stop(child)
    sys.exit(0)


def main():
    global child
    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)
    cfg = client()
    current = fingerprint(cfg)
    child = start(cfg)
    while True:
        for _ in range(INTERVAL):
            code = child.poll()
            if code is not None and not stopping:
                # the dashboard stopped by itself: the kubelet restarts the container
                log(f"exited with {code}")
                sys.exit(code or 1)
            time.sleep(1)
        cfg = client()
        if fingerprint(cfg) != current:
            log("client changed, restarting")
            current = fingerprint(cfg)
            stop(child)
            child = start(cfg)


if __name__ == "__main__":
    main()
