{{/* Labels that are the same on every object */}}
{{- define "mcp-sonarqube.commonLabels" -}}
app.kubernetes.io/part-of: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ splitList "@" (.Values.image.tag | default .Chart.AppVersion) | first | quote }}
{{- end -}}

{{/* Catalogue labels of the MCP server */}}
{{- define "mcp-sonarqube.catalogLabels" -}}
mcp.openshift.eu/server: {{ .Values.mcp.server | quote }}
mcp.openshift.eu/category: {{ .Values.mcp.category | quote }}
{{- with .Values.mcp.bundle }}
mcp.openshift.eu/bundle: {{ . | quote }}
{{- end }}
{{- end -}}

{{- define "mcp-sonarqube.selectorLabels" -}}
app.kubernetes.io/name: mcp-sonarqube
app.kubernetes.io/instance: {{ .Release.Name | quote }}
app.kubernetes.io/component: server
{{- end -}}

{{- define "mcp-sonarqube.brokerSelectorLabels" -}}
app.kubernetes.io/name: mcp-sonarqube
app.kubernetes.io/instance: {{ .Release.Name | quote }}
app.kubernetes.io/component: broker
{{- end -}}

{{/* Namespace of every object: <tenant>-<team> for a server of a team, else the release namespace */}}
{{- define "mcp-sonarqube.namespace" -}}
{{- if .Values.team -}}
{{- printf "%s-%s" (required "tenant is required with team" .Values.tenant) .Values.team -}}
{{- else -}}
{{- .Release.Namespace -}}
{{- end -}}
{{- end -}}

{{- define "mcp-sonarqube.broker" -}}
{{- if not (has .Values.credentials.mode (list "perCaller" "broker")) }}{{ fail "credentials.mode is perCaller or broker" }}{{ end -}}
{{- eq .Values.credentials.mode "broker" -}}
{{- end -}}
