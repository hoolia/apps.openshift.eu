{{/* Labels every object of the release carries. */}}
{{- define "postiz.commonLabels" -}}
app.kubernetes.io/part-of: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{- define "wrapper.image" -}}
{{ .Values.image.repository }}:{{ .Values.image.tag | default (printf "v%s" .Chart.AppVersion) }}
{{- end -}}

{{- define "wrapper.hostname" -}}
{{ required "route.hostname is required: Postiz builds its OAuth callbacks from it" .Values.route.hostname }}
{{- end -}}

{{- define "wrapper.restricted" -}}
allowPrivilegeEscalation: false
capabilities:
  drop: ["ALL"]
{{- end }}

{{- define "wrapper.podSecurity" -}}
runAsNonRoot: true
seccompProfile:
  type: RuntimeDefault
{{- end }}

{{/* Environment of the three Postiz processes and the schema Job. */}}
{{- define "wrapper.env" -}}
- name: NODE_ENV
  value: production
- name: HOME
  value: /tmp
- name: MAIN_URL
  value: https://{{ include "wrapper.hostname" . }}
- name: FRONTEND_URL
  value: https://{{ include "wrapper.hostname" . }}
- name: NEXT_PUBLIC_BACKEND_URL
  value: https://{{ include "wrapper.hostname" . }}/api
- name: BACKEND_INTERNAL_URL
  value: http://127.0.0.1:3000
- name: JWT_SECRET
  valueFrom:
    secretKeyRef:
      name: postiz-credentials
      key: jwt-secret
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: postiz-db-app
      key: uri
- name: REDIS_PASSWORD
  valueFrom:
    secretKeyRef:
      name: postiz-credentials
      key: redis-password
# the password from the variable above; the URL never holds it in the pod spec
- name: REDIS_URL
  value: redis://:$(REDIS_PASSWORD)@postiz-redis:6379
- name: TEMPORAL_ADDRESS
  value: postiz-temporal:7233
- name: IS_GENERAL
  value: "true"
- name: DISABLE_REGISTRATION
  value: {{ .Values.disableRegistration | quote }}
- name: STORAGE_PROVIDER
  value: local
- name: UPLOAD_DIRECTORY
  value: /uploads
- name: NEXT_PUBLIC_UPLOAD_DIRECTORY
  value: /uploads
- name: API_LIMIT
  value: "30"
{{- with .Values.extraEnv }}
{{ toYaml . }}
{{- end }}
{{- end }}

{{/* The developer app credentials, when there are any. */}}
{{- define "wrapper.providersEnvFrom" -}}
{{- $name := .Values.providers.existingSecret }}
{{- if and (eq .Values.secrets.type "externalSecret") .Values.providers.externalSecret }}
{{- $name = "postiz-providers" }}
{{- end }}
{{- if $name }}
envFrom:
- secretRef:
    name: {{ $name }}
{{- end }}
{{- end }}
