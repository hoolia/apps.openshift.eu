{{/* Labels that are the same on every object of the chart */}}
{{- define "mcp-officeagent.commonLabels" -}}
app.kubernetes.io/part-of: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{/* Catalog labels of the server */}}
{{- define "mcp-officeagent.catalogLabels" -}}
{{ .Values.mcp.labelDomain }}/server: {{ .Values.mcp.server | quote }}
{{ .Values.mcp.labelDomain }}/bundle: {{ .Values.mcp.bundle | quote }}
{{ .Values.mcp.labelDomain }}/category: {{ .Values.mcp.category | quote }}
{{- end -}}

{{/* Name of every object */}}
{{- define "mcp-officeagent.name" -}}
{{- default .Values.mcp.server .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "mcp-officeagent.selectorLabels" -}}
app: {{ include "mcp-officeagent.name" . }}
app.kubernetes.io/name: mcp-officeagent
{{- end -}}

{{/* Labels of every object */}}
{{- define "mcp-officeagent.labels" -}}
{{ include "mcp-officeagent.selectorLabels" . }}
app.kubernetes.io/component: server
{{ include "mcp-officeagent.commonLabels" . }}
{{- end -}}

{{/* Namespace of every object: <tenant>-<team> for a server of a team, else the release namespace */}}
{{- define "mcp-officeagent.namespace" -}}
{{- if .Values.team -}}
{{- printf "%s-%s" (required "tenant is required with team" .Values.tenant) .Values.team -}}
{{- else -}}
{{- .Release.Namespace -}}
{{- end -}}
{{- end -}}

{{/* Claim of the documents connection */}}
{{- define "mcp-officeagent.claim" -}}
{{- default (printf "%s-documents" (include "mcp-officeagent.name" .)) .Values.documents.persistence.existingClaim -}}
{{- end -}}

{{/* JWKS address of the token check: <issuerUrl>/protocol/openid-connect/certs unless auth.jwksUri */}}
{{- define "mcp-officeagent.jwksUri" -}}
{{- default (printf "%s/protocol/openid-connect/certs" (trimSuffix "/" .Values.auth.issuerUrl)) .Values.auth.jwksUri -}}
{{- end -}}

{{/* Port of the JWKS address */}}
{{- define "mcp-officeagent.jwksPort" -}}
{{- $u := urlParse (include "mcp-officeagent.jwksUri" .) -}}
{{- $hp := splitList ":" $u.host -}}
{{- if gt (len $hp) 1 }}{{ last $hp }}{{ else if eq $u.scheme "http" }}80{{ else }}443{{ end -}}
{{- end -}}
