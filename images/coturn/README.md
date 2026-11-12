# coturn image

`images/coturn/Containerfile` installs coturn 4.18.0 from the EPEL 10 RPM
`coturn-4.18.0-1.el10_3` on AlmaLinux 10 minimal, pinned by digest. EPEL comes
from `dl.fedoraproject.org` with GPG checking (`epel.repo`), not from the mirror
list, so a build fetches the pinned RPM even while mirrors lag.

The EPEL package needs `libmicrohttpd` for coturn's Prometheus exporter. Public UBI
repositories do not carry it; AlmaLinux 10, a RHEL 10 rebuild and the media edge
host's own OS, does. The build runs `microdnf upgrade` so the image carries the
base's current security updates.

The entrypoint is `/usr/bin/turnserver`. The image holds no configuration: the
deployment supplies the configuration file, listener and relay addresses, quotas
and the shared REST secrets. The default UID is 1001; the deployment may run any UID.
The image also provides `/bin/sh`, `cat`, `cp` and `sleep` for an init step that
writes the configuration and for a drain hook.
