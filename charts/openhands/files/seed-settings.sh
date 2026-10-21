#!/bin/sh
# Write the chart's model and MCP defaults into the agent-server settings once
# per change of those values; later edits in the UI are kept until then.
# SEED_LLM: JSON llm diff or empty. SEED_MCP: lines "<name> <url> <timeout>".
marker="$HOME/.openhands/agent-canvas/.seeded-${SEED_HASH}"
[ -f "$marker" ] && exit 0
api=http://127.0.0.1:18000
key="X-Session-API-Key: $LOCAL_BACKEND_API_KEY"
json="Content-Type: application/json"
i=0
until curl -sf -o /dev/null "$api/alive"; do
  i=$((i + 1))
  [ "$i" -ge 120 ] && exit 0
  sleep 2
done
ok=1
if [ -n "$SEED_LLM" ]; then
  curl -sf -o /dev/null -X PATCH -H "$key" -H "$json" "$api/api/settings" \
    -d "{\"agent_settings_diff\":{\"llm\":$SEED_LLM}}" || ok=0
fi
printf '%s\n' "$SEED_MCP" | while read -r name url timeout; do
  [ -n "$name" ] || continue
  body="{\"url\":\"$url\",\"transport\":\"streamable-http\",\"timeout\":$timeout}"
  curl -sf -o /dev/null -X POST -H "$key" -H "$json" "$api/api/settings/mcp/$name" -d "$body" ||
    curl -sf -o /dev/null -X PATCH -H "$key" -H "$json" "$api/api/settings/mcp/$name" -d "$body" ||
    echo "seed: MCP server $name not written" >&2
done
[ "$ok" = 1 ] && mkdir -p "$(dirname "$marker")" && touch "$marker"
exit 0
