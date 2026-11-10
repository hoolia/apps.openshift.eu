{{/* Labels the same on every object of the chart's own templates */}}
{{- define "airbyte.commonLabels" -}}
app.kubernetes.io/part-of: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{/* Service of the Airbyte server (API and web UI), as the upstream chart names it */}}
{{- define "wrapper.serverService" -}}
{{- printf "%s-airbyte-server-svc" .Release.Name -}}
{{- end -}}

{{/* Internal URL of the Airbyte API */}}
{{- define "wrapper.apiUrl" -}}
{{- printf "http://%s.%s.svc:%v/api/public/v1" (include "wrapper.serverService" .) .Release.Namespace (.Values.upstream.server.service.port | default 8001) -}}
{{- end -}}

{{/* Secret the bootloader writes with the instance admin login and API client */}}
{{- define "wrapper.authSecret" -}}
{{- .Values.global.auth.managedSecretName | default "airbyte-auth-secrets" -}}
{{- end -}}

{{- define "wrapper.mcpName" -}}
{{- printf "%s-mcp" .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "wrapper.configName" -}}
{{- printf "%s-config" .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Container security context under restricted-v2 */}}
{{- define "wrapper.containerSecurityContext" -}}
allowPrivilegeEscalation: false
runAsNonRoot: true
capabilities:
  drop:
  - ALL
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{/* Pod template of the job that applies the declared connections */}}
{{- define "wrapper.configPod" -}}
metadata:
  labels:
    app.kubernetes.io/name: airbyte-config
    app.kubernetes.io/instance: {{ .Release.Name | quote }}
    sidecar.istio.io/inject: "false"
spec:
  restartPolicy: Never
  automountServiceAccountToken: false
  containers:
  - name: configure
    image: "{{ .Values.connections.image.repository }}:{{ .Values.connections.image.tag }}"
    imagePullPolicy: {{ .Values.connections.image.pullPolicy }}
    command:
    - python3
    - /app/configure.py
    env:
    - name: PYTHONUNBUFFERED
      value: "1"
    - name: CONFIG_FILE
      value: /config/connections.json
    - name: AIRBYTE_URL
      value: {{ printf "http://%s.%s.svc:8001" (include "wrapper.serverService" .) .Release.Namespace | quote }}
    - name: AIRBYTE_CLIENT_ID
      valueFrom:
        secretKeyRef:
          name: {{ include "wrapper.authSecret" . }}
          key: instance-admin-client-id
    - name: AIRBYTE_CLIENT_SECRET
      valueFrom:
        secretKeyRef:
          name: {{ include "wrapper.authSecret" . }}
          key: instance-admin-client-secret
    volumeMounts:
    - name: scripts
      mountPath: /app
      readOnly: true
    - name: config
      mountPath: /config
      readOnly: true
    {{- range include "wrapper.connectorSecrets" . | fromJsonArray }}
    - name: secret-{{ . }}
      mountPath: /secrets/{{ . }}
      readOnly: true
    {{- end }}
    resources:
      {{- toYaml .Values.connections.resources | nindent 6 }}
    securityContext:
      {{- include "wrapper.containerSecurityContext" . | nindent 6 }}
  volumes:
  - name: scripts
    configMap:
      name: {{ .Release.Name }}-scripts
  - name: config
    configMap:
      name: {{ include "wrapper.configName" . }}
  {{- range include "wrapper.connectorSecrets" . | fromJsonArray }}
  - name: secret-{{ . }}
    secret:
      secretName: {{ . }}
      optional: true
  {{- end }}
{{- end -}}

{{/* Names of the Secrets the declared connectors read */}}
{{- define "wrapper.connectorSecrets" -}}
{{- $names := list -}}
{{- $text := concat .Values.connections.sources .Values.connections.destinations | toJson -}}
{{- range regexFindAll "\\$\\{secret(list)?:[a-z0-9.-]+/" $text -1 -}}
{{- $names = append $names (regexReplaceAll "^\\$\\{secret(list)?:([a-z0-9.-]+)/$" . "${2}") -}}
{{- end -}}
{{- $names | uniq | sortAlpha | toJson -}}
{{- end -}}
