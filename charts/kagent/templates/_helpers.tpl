{{/* Objects of this chart; the upstream objects are named kagent-* */}}
{{- define "tenant.login" -}}
{{- printf "%s-login" .Values.upstream.fullnameOverride -}}
{{- end -}}

{{/* Labels on every object of the chart; app and component are set per object */}}
{{- define "tenant.labels" -}}
app: {{ include "tenant.login" . }}
app.kubernetes.io/name: kagent
app.kubernetes.io/instance: {{ .Release.Name | quote }}
app.kubernetes.io/part-of: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{- define "tenant.loginSelectorLabels" -}}
app.kubernetes.io/name: kagent
app.kubernetes.io/instance: {{ .Release.Name | quote }}
app.kubernetes.io/component: login
{{- end -}}

{{/* The ServiceAccount is the OAuth client of the login; its redirect goes to the Route */}}
{{- define "tenant.redirectReference" -}}
{{- dict "kind" "OAuthRedirectReference" "apiVersion" "v1" "reference" (dict "kind" "Route" "name" (include "tenant.login" .)) | toJson -}}
{{- end -}}

{{/* Plain value of a generated secret: the given value, else the one in the cluster, else new */}}
{{- define "tenant.secretValue" -}}
{{- if .value -}}
{{- .value -}}
{{- else if hasKey .existing .key -}}
{{- index .existing .key | b64dec -}}
{{- else -}}
{{- randAlphaNum (int .length) -}}
{{- end -}}
{{- end -}}

{{/* Fails on values that would give the tenant cluster-wide objects */}}
{{- define "tenant.validate" -}}
{{- if not (has .Release.Namespace (default list .Values.upstream.rbac.namespaces)) -}}
{{- fail (printf "upstream.rbac.namespaces must contain the release namespace %q: empty renders cluster-wide RBAC" .Release.Namespace) -}}
{{- end -}}
{{- if and (dig "kmcp" "enabled" false .Values.upstream) (ne (toString (dig "kmcp" "rbac" "create" true .Values.upstream)) "false") -}}
{{- fail "upstream.kmcp needs rbac.create false: the platform grants it access" -}}
{{- end -}}
{{- if dig "kagent-tools" "enabled" false .Values.upstream -}}
{{- fail "upstream.kagent-tools binds cluster-admin; team namespaces get tools from the platform" -}}
{{- end -}}
{{- end -}}
