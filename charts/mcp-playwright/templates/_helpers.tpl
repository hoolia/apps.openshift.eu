{{/* Namespace of every object: <tenant>-<team> for a server of a team, else the release namespace */}}
{{- define "mcp-playwright.namespace" -}}
{{- if .Values.team -}}
{{- printf "%s-%s" (required "tenant is required with team" .Values.tenant) .Values.team -}}
{{- else -}}
{{- .Release.Namespace -}}
{{- end -}}
{{- end -}}

{{/* Name of every object: the release name, plus nameSuffix (as a subchart: -playwright) */}}
{{- define "mcp-playwright.fullname" -}}
{{- printf "%s%s" .Release.Name (.Values.nameSuffix | default "") | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Labels on every object of the chart; component is set per object */}}
{{- define "mcp-playwright.labels" -}}
app: {{ include "mcp-playwright.fullname" . }}
app.kubernetes.io/name: mcp-playwright
app.kubernetes.io/instance: {{ include "mcp-playwright.fullname" . | quote }}
app.kubernetes.io/part-of: {{ include "mcp-playwright.fullname" . | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{- define "mcp-playwright.selectorLabels" -}}
app.kubernetes.io/name: mcp-playwright
app.kubernetes.io/instance: {{ include "mcp-playwright.fullname" . | quote }}
{{- end -}}
