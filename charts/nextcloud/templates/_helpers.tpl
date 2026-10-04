{{- define "wrapper.host" -}}
{{- required "nextcloud.nextcloud.host is required" .Values.nextcloud.nextcloud.host -}}
{{- end -}}

{{- define "wrapper.onlyofficeHost" -}}
{{- if .Values.onlyoffice.host -}}
{{- .Values.onlyoffice.host -}}
{{- else -}}
{{- printf "office-%s" (include "wrapper.host" .) -}}
{{- end -}}
{{- end -}}

{{/*
Value of one Secret key, base64: the given value, else the value already in the
cluster, else a new random one.
*/}}
{{- define "wrapper.secretValue" -}}
{{- if .value -}}
{{- .value | b64enc -}}
{{- else if hasKey .existing .key -}}
{{- index .existing .key -}}
{{- else -}}
{{- randAlphaNum (int .length) | b64enc -}}
{{- end -}}
{{- end -}}

{{- define "wrapper.existingData" -}}
{{- $found := lookup "v1" "Secret" .namespace .name -}}
{{- if $found -}}{{- $found.data | toJson -}}{{- else -}}{}{{- end -}}
{{- end -}}
