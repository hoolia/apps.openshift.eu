{{/* Labels that are the same on every object of the chart */}}
{{- define "mcp-hosted.commonLabels" -}}
app.kubernetes.io/part-of: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{/* Name of the server and its backend; a dot is refused in a tenant namespace */}}
{{- define "mcp-hosted.name" -}}
{{- default (required "mcp.server is required" .Values.mcp.server) .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Secret with the API key */}}
{{- define "mcp-hosted.secret" -}}
{{- default (printf "%s-%s-mcp" .Release.Name (include "mcp-hosted.name" .)) .Values.secrets.existingSecret | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Key of the API key in that Secret */}}
{{- define "mcp-hosted.secretKey" -}}
{{- if .Values.secrets.existingSecret }}{{ .Values.secrets.existingSecretKey }}{{ else }}api-key{{ end -}}
{{- end -}}

{{/* true when a key source exists, so the backend is rendered */}}
{{- define "mcp-hosted.ready" -}}
{{- $s := .Values.secrets -}}
{{- if and .Values.enabled .Values.endpoint.host (or $s.existingSecret (eq $s.type "externalSecret") $s.apiKey) }}true{{ end -}}
{{- end -}}

{{/* Namespace of every object: <tenant>-<team> for a server of a team, else the release namespace */}}
{{- define "mcp-hosted.namespace" -}}
{{- if .Values.team -}}
{{- printf "%s-%s" (required "tenant is required with team" .Values.tenant) .Values.team -}}
{{- else -}}
{{- .Release.Namespace -}}
{{- end -}}
{{- end -}}

{{/* Labels of every object */}}
{{- define "mcp-hosted.labels" -}}
app: {{ include "mcp-hosted.name" . }}
app.kubernetes.io/name: mcp-hosted
{{ include "mcp-hosted.commonLabels" . }}
{{- end -}}
