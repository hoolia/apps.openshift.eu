import fcntl
import os
import sys

HELD = 75


def main() -> None:
    probe = len(sys.argv) == 3 and sys.argv[1] == "--probe"
    if not probe and len(sys.argv) < 3:
        raise SystemExit("Codex run lock requires a lock file and command")
    path = sys.argv[2] if probe else sys.argv[1]
    if probe and not os.path.exists(path):
        return
    flags = os.O_RDWR | os.O_NOFOLLOW | (0 if probe else os.O_CREAT)
    descriptor = os.open(path, flags, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(HELD) from None
    if probe:
        return
    os.dup2(descriptor, 3, inheritable=True)
    os.set_inheritable(3, True)
    os.execvp(sys.argv[2], sys.argv[2:])


if __name__ == "__main__":
    main()
