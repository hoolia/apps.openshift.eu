# Janus image

`images/janus/Containerfile` builds [Janus 1.4.3](https://github.com/meetecho/janus-gateway/tree/v1.4.3)
with VideoRoom, SCTP data channels, the websocket API transport and the websocket event
handler. The build stage uses UBI 10 standard and the runtime uses UBI 10 minimal,
both pinned by digest. Other plugins, API transports and event handlers are disabled.

Public UBI repositories supply GLib, OpenSSL and zlib. `build.sh` builds the remaining
native libraries from upstream source. [source-lock.txt](source-lock.txt) pins each
source version, archive URL and SHA-256. The runtime includes this inventory at
`/usr/local/share/janus/source-lock.txt` and the libraries' license files.
These third-party libraries are separate from Red Hat's supported RPM content.
They install under `/opt/janus/lib` and use application-specific ELF runpaths,
keeping them separate from the libraries used by RHEL's system tools.

Libnice uses OpenSSL with UPnP and GStreamer disabled. The edge uses an assigned
public address, so it does not need UPnP router configuration.
Libwebsockets provides the HTTP/1 websocket transport with HTTP/2 and HTTP/3 disabled.

Mount configuration at `/etc/janus`, readable by the runtime UID. The entrypoint is
`/usr/local/bin/janus`; its default arguments are `--configs-folder=/etc/janus` and
`--config=/etc/janus/janus.jcfg`. Plugin folders are under `/usr/local/lib/janus`:
`plugins`, `transports`, `events` and `loggers`. Configuration, listener addresses,
media addresses and credentials belong to the deployment. No production configuration
is included. The default UID is 1001; the integration test uses UID 12345 and GID 23456
with a read-only root filesystem and all capabilities dropped.

The signaling proxy integration requires `events.broadcast = true` and an enabled
`wsevh` handler pointing to the proxy's loopback listener at `/proxy`, with
subprotocol `janus-events`. The deployment supplies that listener's port.

On Linux with Docker and Bun 1.4.4, run from the repository root:

```sh
docker build --platform linux/amd64 -f images/janus/Containerfile -t janus:test images/janus
JANUS_IMAGE=janus:test bun test --timeout 60000 ./images/janus/tests/container.test.js
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
  aquasec/trivy:0.76.2@sha256:8f6bb947dbc528902e11f2f5be52f7b519f6a39d5d148f41166f2040499768fb \
  image --image-src docker --scanners vuln --severity HIGH,CRITICAL --exit-code 1 janus:test
```

On Docker Desktop for macOS, run the suite inside the Linux VM.
After building `janus:test`, use the pinned Linux runner:

```sh
(
set -eu
docker build -f images/janus/tests/Containerfile -t janus-test-runner:1.4.4 images/janus/tests
shared=$(mktemp -d /tmp/janus-test.XXXXXX)
trap 'rm -rf "$shared"' EXIT
docker run --rm --network host \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$PWD:$PWD:ro" -v "$shared:$shared" -w "$PWD" \
  -e "TMPDIR=$shared" -e JANUS_IMAGE=janus:test \
  janus-test-runner:1.4.4 ./images/janus/tests/container.test.js
)
```

The runner and Janus share Linux loopback. The shared temporary path lets the
Docker daemon mount the generated fixtures into Janus. The test removes its
Janus container, and `--rm` removes the runner.

The test uses host networking and requires local port 8188 to be free. It runs real
websocket info, session creation, VideoRoom attachment and room listing, session
destruction and an invalid-session request. A local websocket receiver checks matching
session-created and session-destroyed events at `/proxy`, requiring the `janus-events`
subprotocol. This test receiver uses an ephemeral loopback port. The process socket table must contain
exactly one TCP listener, `127.0.0.1:8188`, so admin and other API listeners fail the test.
These fixtures are only for image tests; they do not test media connectivity.

The registry image name is `ghcr.io/hoolia/janus`.
`VERSION` names the image release. Raise its suffix for an image change without an
upstream version change. The Images workflow builds and tests `linux/amd64` on pull
requests and main, and fails on any HIGH or CRITICAL vulnerability or scanner error.
Only main publishes, using the same tested image. An existing release tag is skipped;
an ambiguous registry failure stops publication. After a successful push, the workflow
records the published digest in its job summary and the `janus-image` artifact's
`janus-image.txt`. The `janus-vulnerability-report` artifact holds the scan result.

The RPM vulnerability scan and upstream source-advisory review have different
coverage. Review the pinned native sources separately; an empty RPM finding list
does not establish that every compiled component is free of vulnerabilities.
