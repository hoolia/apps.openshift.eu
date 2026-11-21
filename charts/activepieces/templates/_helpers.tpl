{{/* Labels that are the same on every object of the chart. */}}
{{- define "activepieces.commonLabels" -}}
app.kubernetes.io/part-of: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{/* The Secret with the encryption key, the token key and the queue password. */}}
{{- define "activepieces.secretName" -}}
{{- .Values.secrets.existingSecret | default "activepieces" -}}
{{- end -}}

{{/* Public address of Activepieces. */}}
{{- define "activepieces.url" -}}
https://{{ required "route.hostname is required" .Values.route.hostname }}
{{- end -}}

{{/* Activepieces wants exactly 32 hexadecimal characters; any other key is hashed to them. */}}
{{- define "activepieces.encryptionKey" -}}
{{- if regexMatch "^[0-9a-f]{32}$" . -}}
{{- . -}}
{{- else -}}
{{- sha256sum . | trunc 32 -}}
{{- end -}}
{{- end -}}

{{- define "activepieces.restricted" -}}
allowPrivilegeEscalation: false
capabilities:
  drop:
  - ALL
runAsNonRoot: true
seccompProfile:
  type: RuntimeDefault
{{- end -}}
