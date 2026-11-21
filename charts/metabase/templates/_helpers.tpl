{{/* Labels that are the same on every object of the chart. */}}
{{- define "metabase.commonLabels" -}}
app.kubernetes.io/part-of: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{/* The Secret with the encryption key and the cookie secret. */}}
{{- define "metabase.secretName" -}}
{{- .Values.secrets.existingSecret | default "metabase" -}}
{{- end -}}

{{- define "metabase.restricted" -}}
allowPrivilegeEscalation: false
capabilities:
  drop:
  - ALL
runAsNonRoot: true
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{- define "metabase.healthProbe" -}}
exec:
  command:
  - sh
  - -c
  - curl -fsS http://127.0.0.1:3000/api/health
{{- end -}}
