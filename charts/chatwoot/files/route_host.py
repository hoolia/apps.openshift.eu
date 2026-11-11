#!/usr/bin/env python3
"""Write the public address of the instance to a file for the other containers.

usage: route_host.py <route name> <output dir> [hostname]

Writes <output dir>/frontend-url (https://<host>) and <output dir>/oauth2-proxy.cfg (the
client id and callback of oauth2-proxy, both derived from the host: the platform names
the Keycloak client after the Route host). With a hostname that is the host. Without one it is read from the
Route that OpenShift gave a host, through the pod's ServiceAccount, which may read only
that Route. Retries for two minutes, then fails, so the pod does not start with a wrong
address.
"""
import json
import pathlib
import re
import ssl
import sys
import time
import urllib.request

SA = pathlib.Path("/var/run/secrets/kubernetes.io/serviceaccount")
# lowercase DNS name: it lands in a config file and a URL, so nothing else may pass
HOST = re.compile(r"^(?=.{1,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$")


def route_host(name):
    namespace = (SA / "namespace").read_text().strip()
    request = urllib.request.Request(
        "https://kubernetes.default.svc/apis/route.openshift.io/v1/namespaces/%s/routes/%s" % (namespace, name),
        headers={"Authorization": "Bearer " + (SA / "token").read_text().strip()})
    context = ssl.create_default_context(cafile=str(SA / "ca.crt"))
    with urllib.request.urlopen(request, timeout=10, context=context) as response:
        return json.loads(response.read())["spec"].get("host", "")


def main():
    name, output = sys.argv[1], pathlib.Path(sys.argv[2])
    if not HOST.match(sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else "x"):
        sys.exit("hostname %r is not a DNS name" % sys.argv[3])
    host = sys.argv[3] if len(sys.argv) > 3 else ""
    for _ in range(24):
        if host:
            break
        try:
            host = route_host(name)
        except (OSError, ValueError, KeyError) as e:
            print("route %s not readable yet: %s" % (name, type(e).__name__), file=sys.stderr)
        if not host:
            time.sleep(5)
    if not host or not HOST.match(host):
        sys.exit("route %s has no usable host" % name)
    write(output, host)
    print("address https://%s" % host)


def write(output, host):
    (output / "frontend-url").write_text("https://%s\n" % host)
    (output / "oauth2-proxy.cfg").write_text(
        'client_id = "%s"\nredirect_url = "https://%s/oauth2/callback"\n' % (host, host))


if __name__ == "__main__":
    main()
