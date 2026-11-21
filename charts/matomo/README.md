# matomo

## Purpose

Matomo: web analytics that keeps the visitor data in your own project. The chart runs
the upstream php-fpm image behind nginx, with a MariaDB database of the MariaDB operator
and a small volume for the configuration file. A scheduled database backup is an option.

## Install

    helm repo add grncloud https://apps.openshift.eu
    helm install matomo grncloud/matomo

Install one release per project. Open the address and follow the installer in the
browser; it writes `config/config.ini.php` to the configuration volume. The database
host, name, user and password are filled in from the chart.

    oc get route matomo -o jsonpath='https://{.spec.host}{"\n"}'

| Value | Default | Meaning |
|---|---|---|
| `route.hostname` | empty | Hostname of the site and the tracker. Empty: OpenShift assigns one. |
| `route.labels` | empty | Extra Route labels, such as a router shard. |
| `route.annotations` | empty | Extra Route annotations, such as an external-dns hostname. |
| `persistence.size` | `2Gi` | Size of the configuration volume. |
| `database.size` | `5Gi` | Size of the database volume. |
| `database.backup.enabled` | `false` | Daily logical backup of the database to its own volume. |
| `database.backup.schedule` | `0 2 * * *` | Cron schedule of the backup. |
| `database.backup.maxRetention` | `168h` | How long a backup is kept. |
| `secrets.existingSecret` | empty | Secret with the keys `password` and `root-password`; the chart creates none. |
| `secrets.type` | `kubernetes` | `externalSecret` reads the passwords from a secret store. |
| `podLabels` | empty | Extra pod labels. |
| `extraEnv` | empty | Further Matomo settings as environment variables. |

At every start the pod copies the Matomo code from the image into an empty web root,
then rebuilds `matomo.js` so that a tracker setting made in the browser, such as
cookieless tracking, survives the restart. Settings live in the configuration file and in
the database; neither is touched by an upgrade of the chart.

The passwords in the Secret `matomo-db` are generated at the first install and kept on
every upgrade with `helm upgrade`. A tool that renders the chart without access to the
cluster, such as Argo CD, generates new ones on every render: set
`secrets.databasePassword` and `secrets.databaseRootPassword`, or `secrets.existingSecret`.

`helm uninstall` deletes the database. The configuration volume is kept; delete the
claim `matomo-config` by hand when you remove Matomo for good.

## How to login

- **Single sign-on**: the chart has no single sign-on.
- **Admin user**: Matomo has no default password. The installer in the browser creates the super user.

## Delivery instructions

- A hostname outside the default application domain needs a DNS record pointing at the
  platform's ingress. Ask the platform team for the target; the certificate follows
  automatically once the name resolves. A tracker on a public website needs the public
  router: ask the platform team for its Route label.
- The chart needs the MariaDB operator on the cluster.
- Plugins installed from the Marketplace live on volume `matomo-plugins` and survive a
  restart; the plugins of the image are copied over them at every start.
- With `secrets.type=externalSecret`, create the items named in
  `secrets.externalSecret.items` in your vault collection with the value in the password
  field, and set `secrets.externalSecret.storeName` to your store.
