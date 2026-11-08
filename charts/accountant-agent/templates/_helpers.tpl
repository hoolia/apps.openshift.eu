{{/* Labels that are the same on every object */}}
{{- define "accountant-agent.commonLabels" -}}
app.kubernetes.io/part-of: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{- define "accountant-agent.selectorLabels" -}}
app: {{ .Release.Name }}
app.kubernetes.io/name: accountant-agent
app.kubernetes.io/component: server
{{- end -}}

{{- define "accountant-agent.image" -}}
{{- if .Values.image.digest -}}
{{- printf "%s@%s" .Values.image.repository .Values.image.digest -}}
{{- else -}}
{{- printf "%s:%s" .Values.image.repository .Values.image.tag -}}
{{- end -}}
{{- end -}}

{{/* Enabled roles as a JSON list of {name, ...}, sorted by name */}}
{{- define "accountant-agent.roles" -}}
{{- $roles := list -}}
{{- range $name := keys .Values.roles | sortAlpha -}}
{{- $r := index $.Values.roles $name -}}
{{- if $r.enabled -}}
{{- $roles = append $roles (merge (dict "name" $name) $r) -}}
{{- end -}}
{{- end -}}
{{- if not $roles }}{{ fail "enable at least one role" }}{{ end -}}
{{- toJson $roles -}}
{{- end -}}

{{- define "accountant-agent.agentName" -}}
{{- default .Release.Name .Values.agentName -}}
{{- end -}}

{{- define "accountant-agent.ledgerName" -}}
{{- printf "%s-ledger" .Release.Name -}}
{{- end -}}

{{/* Secret with the database URL: the value, else the one CloudNativePG makes */}}
{{- define "accountant-agent.urlSecret" -}}
{{- if .Values.ledger.urlSecret.name -}}
{{- .Values.ledger.urlSecret.name -}}
{{- else if .Values.ledger.enabled -}}
{{- printf "%s-app" (include "accountant-agent.ledgerName" .) -}}
{{- end -}}
{{- end -}}

{{/* Audience of the model token: the value, else the host of model.baseUrl */}}
{{- define "accountant-agent.modelAudience" -}}
{{- if .Values.model.tokenAudience -}}
{{- .Values.model.tokenAudience -}}
{{- else if .Values.model.baseUrl -}}
{{- (urlParse .Values.model.baseUrl).host | splitList ":" | first -}}
{{- end -}}
{{- end -}}

{{/* true when the model call carries the ServiceAccount token */}}
{{- define "accountant-agent.modelToken" -}}
{{- if and (not .Values.model.apiKeySecret.name) (include "accountant-agent.modelAudience" .) -}}true{{- end -}}
{{- end -}}

{{/* MCP servers that get a ServiceAccount token: JSON list of {name, audience} */}}
{{- define "accountant-agent.mcpAudiences" -}}
{{- $out := list -}}
{{- range .Values.mcpServers -}}
{{- if not (regexMatch "^[a-z0-9]([-a-z0-9]*[a-z0-9])?$" (toString .name)) }}{{ fail (printf "mcpServers name %q is not a DNS label" .name) }}{{ end -}}
{{- if .tokenAudience -}}
{{- $out = append $out (dict "name" .name "audience" .tokenAudience) -}}
{{- end -}}
{{- end -}}
{{- toJson $out -}}
{{- end -}}

{{- define "accountant-agent.config" -}}
{{- $roles := dict -}}
{{- range include "accountant-agent.roles" . | fromJsonArray -}}
{{- $_ := set $roles .name (dict "port" .port "description" .description "instructions" .instructions "tools" (default list .tools)) -}}
{{- end -}}
{{- $model := dict "baseUrl" .Values.model.baseUrl "name" .Values.model.name "temperature" .Values.model.temperature "maxToolRounds" .Values.model.maxToolRounds "timeout" .Values.model.timeout -}}
{{- if .Values.model.apiKeySecret.name -}}
{{- $_ := set $model "apiKeyEnv" "MODEL_API_KEY" -}}
{{- else if include "accountant-agent.modelToken" . -}}
{{- $_ := set $model "tokenFile" "/var/run/secrets/tokens/model/token" -}}
{{- end -}}
{{- $servers := list -}}
{{- range .Values.mcpServers -}}
{{- $s := dict "name" .name "url" .url -}}
{{- if .tokenAudience -}}
{{- $_ := set $s "tokenFile" (printf "/var/run/secrets/tokens/mcp-%s/token" .name) -}}
{{- end -}}
{{- $servers = append $servers $s -}}
{{- end -}}
{{- $ns := .Release.Namespace -}}
{{- $cfg := dict "agentName" (include "accountant-agent.agentName" .) "publicUrl" (printf "http://%s.%s.svc:{port}/" .Release.Name $ns) "roles" $roles "model" $model "mcpServers" $servers "toolCacheSeconds" .Values.toolCacheSeconds "approval" .Values.approval "taskStoreUrlEnv" "TASK_STORE_URL" -}}
{{- toPrettyJson $cfg -}}
{{- end -}}

{{- define "accountant-agent.containerSecurityContext" -}}
allowPrivilegeEscalation: false
runAsNonRoot: true
capabilities:
  drop:
  - ALL
seccompProfile:
  type: RuntimeDefault
{{- end -}}
