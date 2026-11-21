{{/* Labels that are the same on every object of the chart */}}
{{- define "searxng.commonLabels" -}}
app.kubernetes.io/part-of: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{/* Name of SearXNG's objects */}}
{{- define "searxng.fullname" -}}
{{- .Release.Name | trunc 59 | trimSuffix "-" -}}
{{- end -}}

{{- define "searxng.selectorLabels" -}}
app.kubernetes.io/name: searxng
app.kubernetes.io/instance: {{ .Release.Name | quote }}
{{- end -}}

{{- define "searxng.mcpSelectorLabels" -}}
app.kubernetes.io/name: searxng-mcp
app.kubernetes.io/instance: {{ .Release.Name | quote }}
{{- end -}}

{{/* Secret with SEARXNG_SECRET */}}
{{- define "searxng.secretName" -}}
{{- .Values.secrets.existingSecret | default (include "searxng.fullname" .) -}}
{{- end -}}

{{- define "searxng.restricted" -}}
allowPrivilegeEscalation: false
capabilities:
  drop:
  - ALL
runAsNonRoot: true
seccompProfile:
  type: RuntimeDefault
{{- end -}}
