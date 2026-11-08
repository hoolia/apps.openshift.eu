"""Posts one A2A task to a role and returns at once: the CronJob of a role schedule.

    python -m accountant_agent.trigger <A2A URL> <task text>

The task runs on in the agent (blocking false); the job prints the task id and state.
"""

from __future__ import annotations

import json
import sys
import uuid

import httpx


def request(text: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "message/send",
        "params": {
            "message": {
                "kind": "message",
                "role": "user",
                "messageId": str(uuid.uuid4()),
                "parts": [{"kind": "text", "text": text}],
            },
            "configuration": {"blocking": False, "acceptedOutputModes": ["text"]},
        },
    }


def post(url: str, text: str, client: httpx.Client) -> dict:
    resp = client.post(url, json=request(text))
    resp.raise_for_status()
    body = resp.json()
    if "error" in body:
        raise RuntimeError(f"A2A error: {body['error']}")
    return body.get("result") or {}


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    with httpx.Client(timeout=60.0) as client:
        result = post(args[0], args[1], client)
    print(json.dumps({"task": result.get("id"), "state": (result.get("status") or {}).get("state")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
