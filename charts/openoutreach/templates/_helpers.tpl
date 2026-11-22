{{/* Labels that are the same on every object of the chart. */}}
{{- define "openoutreach.commonLabels" -}}
app.kubernetes.io/part-of: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{/* Name prefix of every object; the release name. */}}
{{- define "openoutreach.name" -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Pod selector: fixed for the life of a release. */}}
{{- define "openoutreach.selector" -}}
app.kubernetes.io/name: openoutreach
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "openoutreach.image" -}}
{{- if .Values.image.digest -}}
{{ .Values.image.repository }}:{{ .Values.image.tag }}@{{ .Values.image.digest }}
{{- else -}}
{{ .Values.image.repository }}:{{ .Values.image.tag }}
{{- end -}}
{{- end -}}

{{/* The playbook the scout reads: the test run's own one while testRun.enabled. */}}
{{- define "openoutreach.playbook" -}}
{{- toYaml (ternary .Values.testRun.playbook .Values.playbook .Values.testRun.enabled) }}
{{- end -}}

{{/* Sending is on: both switches. */}}
{{- define "openoutreach.maySend" -}}
{{- if and .Values.sendEnabled (not .Values.readOnly) }}true{{ end -}}
{{- end -}}

{{/* Credential values given for a Secret of type kubernetes, by environment name. */}}
{{- define "openoutreach.credentialValues" -}}
{{- $s := .Values.secrets -}}
{{- $all := dict "CLICKHOUSE_USER" $s.clickhouseUser "CLICKHOUSE_PASSWORD" $s.clickhousePassword "SLACK_WEBHOOK_URL" $s.slackWebhookUrl "CHATWOOT_API_TOKEN" $s.chatwootApiToken "ODOO_USER" $s.odooUser "ODOO_PASSWORD" $s.odooPassword -}}
{{- $set := dict -}}
{{- range $k, $v := $all }}{{ if $v }}{{ $_ := set $set $k $v }}{{ end }}{{ end -}}
{{- toYaml $set -}}
{{- end -}}

{{/* The Secret the pod reads its credentials from, or empty when there is none. */}}
{{- define "openoutreach.credentialsSecret" -}}
{{- if .Values.secrets.existingSecret -}}
{{- .Values.secrets.existingSecret -}}
{{- else if and (eq .Values.secrets.type "kubernetes") (include "openoutreach.credentialValues" . | fromYaml) -}}
{{- include "openoutreach.name" . }}-credentials
{{- else if and (eq .Values.secrets.type "externalSecret") .Values.secrets.externalSecret.items -}}
{{- include "openoutreach.name" . }}-credentials
{{- end -}}
{{- end -}}
