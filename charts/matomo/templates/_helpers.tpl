{{/* Labels that are the same on every object of the chart. */}}
{{- define "matomo.commonLabels" -}}
app.kubernetes.io/part-of: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{/* The Secret with the database passwords. */}}
{{- define "matomo.secretName" -}}
{{- .Values.secrets.existingSecret | default "matomo-db" -}}
{{- end -}}

{{- define "matomo.restricted" -}}
allowPrivilegeEscalation: false
capabilities:
  drop:
  - ALL
runAsNonRoot: true
seccompProfile:
  type: RuntimeDefault
{{- end -}}
