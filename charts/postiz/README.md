# Postiz

## Purpose

Postiz schedules and publishes posts on LinkedIn (profiles and pages), Facebook pages,
Instagram, Reddit, X, Threads, Bluesky, Mastodon, YouTube, TikTok, Pinterest and more,
from one calendar. Its MCP server lets AI agents draft, schedule and list posts for the
connected channels. The chart runs the upstream image as three processes (web interface,
API with the MCP server, and the worker that publishes), a PostgreSQL database from the
CloudNativePG operator, a Valkey store and a Temporal server that holds the schedule.
WhatsApp is not a Postiz channel.

## Install

    helm repo add grncloud https://apps.openshift.eu
    helm install postiz grncloud/postiz --set route.hostname=postiz.example.com

Install one release per project; `route.hostname` is required. The first start takes a few
minutes: the image is large and the database schema is created before the API starts.

| Value | Default | Meaning |
|---|---|---|
| `route.hostname` | required | Hostname of the web interface; the OAuth callbacks of every channel use it. |
| `disableRegistration` | `true` | Only the first account can sign up; it invites the rest. |
| `providers.existingSecret` | empty | Secret with the developer app credentials as variables (see below). |
| `providers.externalSecret` | empty | With `secrets.type: externalSecret`: variable name to `{key, property}` of a vault item. |
| `uploads.size` | `10Gi` | Volume of uploaded media, ReadWriteOnce. |
| `database.instances` | `1` | PostgreSQL instances; `2` adds a standby. |
| `mcp.service` | `true` | Service `postiz-mcp` on port 3000, path `/mcp`, for an MCP gateway. |
| `networkPolicy.mcpIngressFrom` | empty | Peers that may reach the MCP server on port 3000. |
| `secrets.type` | `kubernetes` | `externalSecret` reads the secrets from a secret store. |
| `extraEnv` | empty | Further Postiz settings, for example `EMAIL_*` or `OPENAI_API_KEY`. |

### Channels

A channel posts through a developer app of its platform. Create the app, give it the
redirect URL `https://<hostname>/integrations/social/<provider>`, and put its credentials
in the providers Secret:

| Channel | Variables | Developer app |
|---|---|---|
| LinkedIn, LinkedIn page | `LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET` | LinkedIn app with the products "Share on LinkedIn" and "Sign In with LinkedIn using OpenID Connect" |
| Facebook page, Instagram (business) | `FACEBOOK_APP_ID`, `FACEBOOK_APP_SECRET` | Meta app of type Business |
| Instagram (standalone) | `INSTAGRAM_APP_ID`, `INSTAGRAM_APP_SECRET` | Meta app with Instagram API with Instagram Login |
| Reddit | `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` | Reddit app of type web app |
| Threads | `THREADS_APP_ID`, `THREADS_APP_SECRET` | Meta app with the Threads API |

Then connect each account in Postiz with "Add channel". The token Postiz receives stays in
its database.

### MCP server

The API answers MCP at `/api/mcp` on the Route and at `/mcp` on Service `postiz-mcp`. A
client sends an API key of the organisation (Settings, Public API) as
`Authorization: Bearer <key>`.

## How to login

Open `https://<hostname>` and sign up: the first account owns the organisation. With
`disableRegistration` the signup closes after it; invite the others from Settings, Teams.

## Delivery instructions

Postiz runs at `https://<hostname>`. Sign up there first, connect your channels with
"Add channel", and create an API key under Settings, Public API for AI agents. The MCP
address is `https://<hostname>/api/mcp`.
