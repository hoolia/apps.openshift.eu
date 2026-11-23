import fcntl
import os
from pathlib import Path
import sys


def main() -> None:
    state = Path(os.environ["CODEX_AUTH_STATE_DIR"])
    if not state.is_absolute() or len(sys.argv) < 2:
        raise SystemExit("Codex worker requires an absolute state path and command")
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    state.chmod(0o700)
    private = state / "private"
    private.mkdir(mode=0o700, parents=True, exist_ok=True)
    private.chmod(0o700)
    with (private / "owner.lock").open("a+b") as lease:
        os.fchmod(lease.fileno(), 0o600)
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Codex worker state has an active owner") from None
        os.dup2(lease.fileno(), 3, inheritable=True)
        os.set_inheritable(3, True)
        environment = {
            name: os.environ[name]
            for name in (
                "PATH", "HOME", "TMPDIR", "LANG", "LC_ALL",
                "SSL_CERT_FILE", "NODE_EXTRA_CA_CERTS", "CODEX_HOME",
                "CI_API_V4_URL", "CI_PROJECT_ID", "CODEX_AUTH_STORE_PROJECT_ID",
                "CODEX_AUTH_STORE_TOKEN", "CODEX_AUTH_STATE_DIR",
                "CODEX_EXCHANGE_DIR", "CODEX_AGENT_URL",
                "CODEX_AGENT_POLL_MS", "CODEX_AGENT_WAIT_MS",
                "CODEX_GATEWAY_URL", "CODEX_GATEWAY_KEY", "CODEX_MODEL",
                "CODEX_REASONING_EFFORT",
                "CODEX_WORKER_HOST", "CODEX_WORKER_PORT", "CODEX_WORKER_TOKEN",
                "CODEX_WORKER_TLS_CERT_FILE", "CODEX_WORKER_TLS_KEY_FILE",
            )
            if name in os.environ
        }
        environment["CODEX_AUTH_LOCK_FD"] = "3"
        os.execvpe(sys.argv[1], sys.argv[1:], environment)


if __name__ == "__main__":
    main()
