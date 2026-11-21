{{/* Labels that are the same on every object of the chart */}}
{{- define "mcp-gsc.commonLabels" -}}
app.kubernetes.io/part-of: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{/* Catalog labels of the server */}}
{{- define "mcp-gsc.catalogLabels" -}}
{{ .Values.mcp.labelDomain }}/server: {{ .Values.mcp.server | quote }}
{{ .Values.mcp.labelDomain }}/bundle: {{ .Values.mcp.bundle | quote }}
{{ .Values.mcp.labelDomain }}/category: {{ .Values.mcp.category | quote }}
{{- end -}}

{{/* Name of every object; a dot is refused for a backend in a tenant namespace */}}
{{- define "mcp-gsc.name" -}}
{{- default .Values.mcp.server .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "mcp-gsc.selectorLabels" -}}
app: {{ include "mcp-gsc.name" . }}
app.kubernetes.io/name: mcp-gsc
{{- end -}}

{{/* Namespace of every object: <tenant>-<team> for a server of a team, else the release namespace */}}
{{- define "mcp-gsc.namespace" -}}
{{- if .Values.team -}}
{{- printf "%s-%s" (required "tenant is required with team" .Values.tenant) .Values.team -}}
{{- else -}}
{{- .Release.Namespace -}}
{{- end -}}
{{- end -}}

{{/* Secret with the service account key */}}
{{- define "mcp-gsc.secret" -}}
{{- default (printf "%s-credentials" (include "mcp-gsc.name" .)) .Values.secrets.existingSecret | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "mcp-gsc.secretKey" -}}
{{- if .Values.secrets.existingSecret }}{{ .Values.secrets.existingSecretKey }}{{ else }}credentials.json{{ end -}}
{{- end -}}

{{/* true when a key source exists: the backend and RemoteMCPServer are rendered */}}
{{- define "mcp-gsc.ready" -}}
{{- $s := .Values.secrets -}}
{{- if and .Values.enabled (or $s.existingSecret (eq $s.type "externalSecret") $s.serviceAccountJson) }}true{{ end -}}
{{- end -}}
