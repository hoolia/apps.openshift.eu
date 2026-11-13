{{/* Labels on every object of the chart; component is set per object */}}
{{- define "mcp-odoo.labels" -}}
app: {{ .Release.Name }}
app.kubernetes.io/name: mcp-odoo
app.kubernetes.io/instance: {{ .Release.Name | quote }}
app.kubernetes.io/part-of: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ .Values.image.tag | default .Chart.AppVersion | quote }}
{{- end -}}

{{/* Catalogue labels of the MCP server */}}
{{- define "mcp-odoo.catalogLabels" -}}
mcp.openshift.eu/server: {{ .Values.mcp.server | quote }}
mcp.openshift.eu/category: {{ .Values.mcp.category | quote }}
{{- with .Values.mcp.bundle }}
mcp.openshift.eu/bundle: {{ . | quote }}
{{- end }}
{{- end -}}

{{- define "mcp-odoo.selectorLabels" -}}
app.kubernetes.io/name: mcp-odoo
app.kubernetes.io/instance: {{ .Release.Name | quote }}
{{- end -}}

{{/* Namespace of every object: <tenant>-<team> for a server of a team, else the release namespace */}}
{{- define "mcp-odoo.namespace" -}}
{{- if .Values.team -}}
{{- printf "%s-%s" (required "tenant is required with team" .Values.tenant) .Values.team -}}
{{- else -}}
{{- .Release.Namespace -}}
{{- end -}}
{{- end -}}
