"""Apply the declared sources, destinations and connections through the Airbyte API.

Objects are matched by name: missing ones are created, existing ones updated. Strings
${secret:<Secret>/<key>} in a configuration are read from /secrets/<Secret>/<key>, and
${secretlist:<Secret>/<key>} as a comma-separated list; an object whose Secret or key is
missing is skipped together with its connections.
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from airbyte_api import Airbyte, ApiError  # noqa: E402

SECRET = re.compile(r"\$\{secret:([a-z0-9.-]+)/([A-Za-z0-9._-]+)\}")
SECRET_LIST = re.compile(r"\$\{secretlist:([a-z0-9.-]+)/([A-Za-z0-9._-]+)\}")
SECRETS_DIR = os.environ.get("SECRETS_DIR", "/secrets")


class Missing(Exception):
    pass


def resolve(value):
    if isinstance(value, dict):
        return {k: resolve(v) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v) for v in value]
    if isinstance(value, str):
        def read(m):
            path = os.path.join(SECRETS_DIR, m.group(1), m.group(2))
            try:
                with open(path) as f:
                    text = f.read().strip()
            except OSError:
                raise Missing(f"{m.group(1)}/{m.group(2)}") from None
            if not text:
                raise Missing(f"{m.group(1)}/{m.group(2)} is empty")
            return text
        whole = SECRET.fullmatch(value)
        if whole:
            return read(whole)
        listed = SECRET_LIST.fullmatch(value)
        if listed:
            return [x.strip() for x in read(listed).split(",") if x.strip()]
        return SECRET.sub(read, value)
    return value


def wait_for_api(api):
    for _ in range(90):
        try:
            api.token()
            return
        except Exception as err:  # noqa: BLE001
            print(f"waiting for the Airbyte API: {err}", flush=True)
            time.sleep(10)
    sys.exit("Airbyte API not reachable")


def main():
    spec = json.load(open(os.environ.get("CONFIG_FILE", "/config/connections.json")))
    api = Airbyte()
    wait_for_api(api)
    workspaces = list(api.pages("/workspaces"))
    want = spec.get("workspace")
    ws = next((w for w in workspaces if not want or w.get("name") == want), None)
    if not ws:
        sys.exit(f"workspace {want!r} not found")
    wid = ws["workspaceId"]
    print(f"workspace {ws.get('name')} {wid}")
    failed = []

    # declarative (low-code) source definitions, by name
    base = f"/workspaces/{wid}/definitions/declarative_sources"
    declared = {d["name"]: d for d in api.pages(base)}
    for d in spec.get("declarativeSources") or []:
        try:
            if d["name"] in declared:
                api.public("PUT", f"{base}/{declared[d['name']]['id']}", {"manifest": d["manifest"]})
                print(f"declarative source {d['name']}: updated")
            else:
                declared[d["name"]] = api.public("POST", base, {"name": d["name"], "manifest": d["manifest"]})
                print(f"declarative source {d['name']}: created")
        except ApiError as err:
            failed.append(f"declarative source {d['name']}: {err}")

    defs = {}
    for kind in ("source", "destination"):
        out = api.internal(f"/{kind}_definitions/list_for_workspace", {"workspaceId": wid})
        defs[kind] = {x["name"]: x[f"{kind}DefinitionId"] for x in out[f"{kind}Definitions"]}
        for name, d in declared.items():
            if kind == "source":
                defs[kind].setdefault(name, d.get("id"))

    # connector versions, by registry name
    for name, tag in (spec.get("definitionVersions") or {}).items():
        kind = next((k for k in ("source", "destination") if name in defs[k]), None)
        if not kind:
            failed.append(f"definition version {name}: no such connector")
            continue
        try:
            out = api.internal(f"/{kind}_definitions/update", {
                "workspaceId": wid, f"{kind}DefinitionId": defs[kind][name], "dockerImageTag": str(tag)})
            print(f"{kind} definition {name}: {out.get('dockerImageTag')}")
        except ApiError as err:
            failed.append(f"definition version {name}: {err}")

    ids = {"source": {}, "destination": {}}
    for kind, plural in (("destination", "destinations"), ("source", "sources")):
        existing = {x["name"]: x for x in api.pages(f"/{plural}", workspaceIds=wid)}
        for item in spec.get(plural) or []:
            name = item["name"]
            try:
                config = resolve(item.get("configuration") or {})
            except Missing as err:
                print(f"{kind} {name}: skipped, secret {err} missing")
                continue
            definition = defs[kind].get(item["definition"], item["definition"])
            try:
                if name in existing:
                    oid = existing[name][f"{kind}Id"]
                    api.public("PATCH", f"/{plural}/{oid}", {"name": name, "configuration": config})
                    print(f"{kind} {name}: updated")
                else:
                    out = api.public("POST", f"/{plural}", {"name": name, "workspaceId": wid,
                                                            "definitionId": definition,
                                                            "configuration": config})
                    oid = out[f"{kind}Id"]
                    print(f"{kind} {name}: created")
                ids[kind][name] = oid
            except ApiError as err:
                failed.append(f"{kind} {name}: {err}")

    existing = {c["name"]: c for c in api.pages("/connections", workspaceIds=wid)}
    for c in spec.get("connections") or []:
        name = c["name"]
        src, dst = ids["source"].get(c["source"]), ids["destination"].get(c["destination"])
        if not src or not dst:
            print(f"connection {name}: skipped, source or destination not configured")
            continue
        body = {"name": name,
                "status": "active" if c.get("enabled", True) else "inactive",
                "schedule": ({"scheduleType": "cron", "cronExpression": c["schedule"]}
                             if c.get("schedule") else {"scheduleType": "manual"})}
        if c.get("namespaceFormat"):
            body["namespaceDefinition"] = "custom_format"
            body["namespaceFormat"] = c["namespaceFormat"]
        if c.get("prefix"):
            body["prefix"] = c["prefix"]
        if c.get("streams"):
            body["configurations"] = {"streams": [s if isinstance(s, dict) else {"name": s}
                                                  for s in c["streams"]]}
        try:
            if name in existing:
                api.public("PATCH", f"/connections/{existing[name]['connectionId']}", body)
                print(f"connection {name}: updated")
            else:
                body.update({"sourceId": src, "destinationId": dst})
                api.public("POST", "/connections", body)
                print(f"connection {name}: created")
        except ApiError as err:
            failed.append(f"connection {name}: {err}")

    for f in failed:
        print("FAILED " + f)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
