# mcp-bundle-market-research

## Purpose

Installs the hosted MCP servers of market research vendors for the agents of your project in one release, each switched on separately:

| Value | Server | What the agent can do |
|---|---|---|
| `semrush` | Semrush (`https://mcp.semrush.com/v2/mcp`) | Keyword research, search demand, competitor domain overviews and backlinks |
| `similarweb` | Similarweb (`https://mcp.similarweb.com`) | Estimated website traffic, engagement, audience and traffic sources of competitors |
| `serpapi` | SerpApi (`https://mcp.serpapi.com/mcp`) | Live Google Search and Google Trends results (engine `google_trends`), and other search engines |

Every server is an `mcp-hosted` release: an `AgentgatewayBackend` with the vendor endpoint and a Secret with the team's API key. The project's MCP gateway serves it as `/mcp/<namespace>/<server>` and adds the key, so agents never see it. The servers carry the labels `mcp.openshift.eu/bundle: market-research` and `mcp.openshift.eu/category: marketing`.

## Install

```bash
helm repo add grncloud https://apps.openshift.eu
helm install market-research grncloud/mcp-bundle-market-research \
  --set semrush.enabled=true --set semrush.secrets.apiKey=<Semrush API key> \
  --set serpapi.enabled=true --set serpapi.secrets.apiKey=<SerpApi key>
```

- `enabled` (default: true): Switch of the bundle inside mcp-all.
- `semrush`, `similarweb`, `serpapi` (default: `enabled: false`): The values of chart `mcp-hosted` for each server. Set `enabled: true` and a key: `secrets.apiKey`, or `secrets.type: externalSecret` with `secrets.externalSecret.storeName` and `secrets.externalSecret.item`. Endpoint and key header are preset.

## How to login

No human login: the gateway authenticates to each vendor with the team's API key, and agents authenticate to the gateway with their own identity.

## Delivery instructions

1. Take a plan with API access at each vendor you switch on:
   - Semrush: SEO Pro, Guru or Business with API units; the key is under Subscription info > API units.
   - Similarweb: an API plan with data credits; the key is under the account's API settings.
   - SerpApi: any plan, the free plan holds 250 searches a month; the key is under Your account.
2. Put each key into `<server>.secrets.apiKey`, or into a vault item for `secrets.type: externalSecret`.
3. Give the agents the servers at `/mcp/<namespace>/semrush`, `/mcp/<namespace>/similarweb` and `/mcp/<namespace>/serpapi` on the gateway.

All agents of the project spend the vendor's units with one key: watch the vendor's usage page. Search results come from the open web and can steer an agent that reads them; review what it does.
