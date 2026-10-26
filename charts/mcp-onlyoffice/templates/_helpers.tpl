{{/* Labels that are the same on every object of the chart */}}
{{- define "mcp-onlyoffice.commonLabels" -}}
app.kubernetes.io/part-of: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{/* Catalog labels of the server */}}
{{- define "mcp-onlyoffice.catalogLabels" -}}
{{ .Values.mcp.labelDomain }}/server: {{ .Values.mcp.server | quote }}
{{ .Values.mcp.labelDomain }}/bundle: {{ .Values.mcp.bundle | quote }}
{{ .Values.mcp.labelDomain }}/category: {{ .Values.mcp.category | quote }}
{{- end -}}

{{/* Name of every object */}}
{{- define "mcp-onlyoffice.name" -}}
{{- default .Values.mcp.server .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "mcp-onlyoffice.selectorLabels" -}}
app: {{ include "mcp-onlyoffice.name" . }}
app.kubernetes.io/name: mcp-onlyoffice
{{- end -}}

{{/* Secret with the Document Server's JWT secret */}}
{{- define "mcp-onlyoffice.jwtSecret" -}}
{{- default (printf "%s-%s" .Release.Name .Values.documentServer.secretSuffix) .Values.documentServer.existingSecret | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Namespace of every object: <tenant>-<team> for a server of a team, else the release namespace */}}
{{- define "mcp-onlyoffice.namespace" -}}
{{- if .Values.team -}}
{{- printf "%s-%s" (required "tenant is required with team" .Values.tenant) .Values.team -}}
{{- else -}}
{{- .Release.Namespace -}}
{{- end -}}
{{- end -}}
