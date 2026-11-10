{{/* The release name, with -clickhouse unless it already holds it */}}
{{- define "clickhouse.fullname" -}}
{{- if contains "clickhouse" .Release.Name -}}
{{- .Release.Name | trunc 50 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-clickhouse" .Release.Name | trunc 50 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{/* Labels that are the same on every object */}}
{{- define "clickhouse.commonLabels" -}}
app.kubernetes.io/instance: {{ .Release.Name | quote }}
app.kubernetes.io/part-of: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{- define "clickhouse.selectorLabels" -}}
app.kubernetes.io/name: clickhouse
app.kubernetes.io/instance: {{ .Release.Name | quote }}
{{- end -}}

{{- define "clickhouse.mcpSelectorLabels" -}}
app.kubernetes.io/name: clickhouse-mcp
app.kubernetes.io/instance: {{ .Release.Name | quote }}
{{- end -}}

{{/* The given value, else the value already in the cluster, else a new random one.
Arguments: list <root> <role> */}}
{{- define "clickhouse.password" -}}
{{- $root := index . 0 -}}
{{- $role := index . 1 -}}
{{- $given := index $root.Values.secrets (printf "%sPassword" $role) -}}
{{- $found := lookup "v1" "Secret" $root.Release.Namespace (printf "%s-%s" (include "clickhouse.fullname" $root) $role) -}}
{{- if $given -}}
{{- $given -}}
{{- else if and $found (hasKey (default dict $found.data) "password") -}}
{{- index $found.data "password" | b64dec -}}
{{- else -}}
{{- randAlphaNum 32 -}}
{{- end -}}
{{- end -}}

{{/* Connection keys of a user's Secret besides the password. Arguments: list <root> <role> */}}
{{- define "clickhouse.connection" -}}
{{- $root := index . 0 -}}
{{- $role := index . 1 -}}
{{- $host := printf "%s.%s.svc" (include "clickhouse.fullname" $root) $root.Release.Namespace -}}
username: {{ (index $root.Values.users $role).name | quote }}
host: {{ $host | quote }}
port: "8123"
native-port: "9000"
database: {{ $root.Values.database | quote }}
url: {{ printf "http://%s:8123" $host | quote }}
{{- end -}}
