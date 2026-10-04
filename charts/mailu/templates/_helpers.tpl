{{/* Name prefix shared with the objects of the upstream chart. */}}
{{- define "mailu-wrapper.fullname" -}}
{{- required "upstream.fullnameOverride is required" .Values.upstream.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Name of the Secret that holds all four secrets. */}}
{{- define "mailu-wrapper.secretName" -}}
{{- required "upstream.existingSecret is required" .Values.upstream.existingSecret -}}
{{- end -}}

{{/* Labels that select the pods of one upstream component. */}}
{{- define "mailu-wrapper.selector" -}}
app.kubernetes.io/name: {{ .context.Values.upstream.nameOverride }}
app.kubernetes.io/instance: {{ .context.Release.Name }}
{{- if .component }}
app.kubernetes.io/component: {{ .component }}
{{- end }}
{{- end -}}

{{/*
Value of one Secret key, base64: the given value, else the value already in the
cluster, else a new random one.
*/}}
{{- define "mailu-wrapper.secretValue" -}}
{{- if .value -}}
{{- .value | b64enc -}}
{{- else if hasKey .existing .key -}}
{{- index .existing .key -}}
{{- else -}}
{{- randAlphaNum (int .length) | b64enc -}}
{{- end -}}
{{- end -}}

{{/* Public ports of the front pods: name, number, value key. */}}
{{- define "mailu-wrapper.frontPorts" -}}
{{- $all := list (list "smtp" 25 "smtp") (list "smtps" 465 "smtps") (list "smtpd" 587 "submission") (list "imap" 143 "imap") (list "imaps" 993 "imaps") (list "pop3" 110 "pop3") (list "pop3s" 995 "pop3s") (list "sieve" 4190 "manageSieve") (list "http" 80 "http") (list "https" 443 "https") -}}
{{- $out := list -}}
{{- range $all -}}
{{- if index $.Values.mailService.ports (index . 2) -}}
{{- $out = append $out (dict "name" (index . 0) "port" (index . 1)) -}}
{{- end -}}
{{- end -}}
{{- dict "ports" $out | toYaml -}}
{{- end -}}
