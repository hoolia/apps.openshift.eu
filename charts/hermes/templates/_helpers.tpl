{{- define "hermes.name" -}}
{{- .Release.Name -}}
{{- end -}}

{{/* Namespace of every object: <tenant>-<team> for an app of a team, else the release namespace */}}
{{- define "hermes.namespace" -}}
{{- if .Values.team -}}
{{- printf "%s-%s" (required "tenant is required with team" .Values.tenant) .Values.team -}}
{{- else -}}
{{- .Release.Namespace -}}
{{- end -}}
{{- end -}}

{{- define "hermes.labels" -}}
app: {{ include "hermes.name" . }}
app.kubernetes.io/name: hermes
app.kubernetes.io/part-of: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{- define "hermes.selectorLabels" -}}
app: {{ include "hermes.name" . }}
app.kubernetes.io/name: hermes
{{- end -}}

{{/* SAR verb of the login: login is granted by the login Role only, get also by admin and edit */}}
{{- define "hermes.loginVerb" -}}
{{- ternary "login" "get" (not (empty .Values.oauth.allowedGroups)) -}}
{{- end -}}

{{/* The ServiceAccount is the OAuth client of the login; its redirect goes to the Route */}}
{{- define "hermes.redirectReference" -}}
{{- dict "kind" "OAuthRedirectReference" "apiVersion" "v1" "reference" (dict "kind" "Route" "name" (include "hermes.name" .)) | toJson -}}
{{- end -}}

{{/* Audience of the projected ServiceAccount token: the value, else the upstream host */}}
{{- define "hermes.audience" -}}
{{- if .Values.gateway.audience -}}
{{- .Values.gateway.audience -}}
{{- else if .Values.gateway.upstream -}}
{{- (urlParse .Values.gateway.upstream).host | splitList ":" | first -}}
{{- end -}}
{{- end -}}

{{- define "hermes.image" -}}
{{- if .Values.image.digest -}}
{{- printf "%s@%s" .Values.image.repository .Values.image.digest -}}
{{- else -}}
{{- printf "%s:%s" .Values.image.repository .Values.image.tag -}}
{{- end -}}
{{- end -}}

{{- define "hermes.containerSecurityContext" -}}
allowPrivilegeEscalation: false
runAsNonRoot: true
capabilities:
  drop:
  - ALL
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{/* config.yaml keys the chart owns, merged at every start: model, skills dir, values.config */}}
{{- define "hermes.config" -}}
{{- $cfg := dict -}}
{{- if .Values.gateway.upstream -}}
{{- $model := dict "provider" "custom" "base_url" (printf "http://127.0.0.1:%v/v1" .Values.tokenProxy.port) "api_key" "token-proxy" -}}
{{- with .Values.llm.model }}{{ $_ := set $model "default" . }}{{ end -}}
{{- with .Values.llm.contextLength }}{{ $_ := set $model "context_length" (int .) }}{{ end -}}
{{- with .Values.llm.providerName -}}
{{- /* a named provider: /model and hermes status show this name, not "Custom endpoint" */ -}}
{{- $id := regexReplaceAll "[^a-z0-9]+" (lower .) "-" | trimAll "-" -}}
{{- $entry := dict "name" . "base_url" $model.base_url "api_key" $model.api_key -}}
{{- with $model.default }}{{ $_ := set $entry "model" . }}{{ end -}}
{{- with $model.context_length }}{{ $_ := set $entry "context_length" . }}{{ end -}}
{{- $_ := set $model "provider" $id -}}
{{- $_ := set $cfg "providers" (dict $id $entry) -}}
{{- end -}}
{{- $_ := set $cfg "model" $model -}}
{{- end -}}
{{- if .Values.skills.repositories -}}
{{- $_ := set $cfg "skills" (dict "external_dirs" (list "/opt/data/skills-external")) -}}
{{- end -}}
{{- toJson (mergeOverwrite $cfg (deepCopy .Values.config)) -}}
{{- end -}}
