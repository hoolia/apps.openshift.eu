{{/* Labels that are the same on every object of the chart */}}
{{- define "mcp-higgsfield.commonLabels" -}}
app.kubernetes.io/part-of: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{/* Catalog labels of the server */}}
{{- define "mcp-higgsfield.catalogLabels" -}}
{{ .Values.mcp.labelDomain }}/server: {{ .Values.mcp.server | quote }}
{{ .Values.mcp.labelDomain }}/bundle: {{ .Values.mcp.bundle | quote }}
{{ .Values.mcp.labelDomain }}/category: {{ .Values.mcp.category | quote }}
{{- end -}}

{{/* Name of every object; kagent names the Deployment and Service after the MCPServer */}}
{{- define "mcp-higgsfield.name" -}}
{{- default .Values.mcp.server .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Namespace of every object: <tenant>-<team> for a server of a team, else the release namespace */}}
{{- define "mcp-higgsfield.namespace" -}}
{{- if .Values.team -}}
{{- printf "%s-%s" (required "tenant is required with team" .Values.tenant) .Values.team -}}
{{- else -}}
{{- .Release.Namespace -}}
{{- end -}}
{{- end -}}

{{/* Secret with HF_API_KEY and HF_SECRET; empty when no key source is set */}}
{{- define "mcp-higgsfield.secret" -}}
{{- $s := .Values.secrets -}}
{{- if $s.existingSecret -}}
{{- $s.existingSecret -}}
{{- else if or (eq $s.type "externalSecret") $s.apiKey $s.apiSecret -}}
{{- printf "%s-api-key" (include "mcp-higgsfield.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
