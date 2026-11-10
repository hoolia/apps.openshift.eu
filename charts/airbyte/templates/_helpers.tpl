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

{{/* Install hook Job that creates Secret name with random keys once, inside the cluster,
before the first sync; a later render never sees or changes it */}}
{{- define "wrapper.secretJob" -}}
{{- $root := .root }}
apiVersion: batch/v1
kind: Job
metadata:
  name: {{ .name }}
  namespace: {{ $root.Release.Namespace }}
  labels:
    app: airbyte-{{ .component }}
    app.kubernetes.io/name: {{ .component }}
    app.kubernetes.io/component: credentials
    {{- include "airbyte.commonLabels" $root | nindent 4 }}
  annotations:
    helm.sh/hook: pre-install,pre-upgrade
    helm.sh/hook-weight: "-5"
    helm.sh/hook-delete-policy: before-hook-creation,hook-succeeded
spec:
  backoffLimit: 3
  template:
    metadata:
      labels:
        app.kubernetes.io/name: {{ .component }}-credentials
        app.kubernetes.io/instance: {{ $root.Release.Name | quote }}
        sidecar.istio.io/inject: "false"
    spec:
      restartPolicy: Never
      serviceAccountName: {{ $root.Values.upstream.airbyteBootloader.serviceAccountName | default "airbyte-bootloader" }}
      containers:
      - name: create
        image: "{{ $root.Values.connections.image.repository }}:{{ $root.Values.connections.image.tag }}"
        imagePullPolicy: {{ $root.Values.connections.image.pullPolicy }}
        command:
        - python3
        - -c
        - {{ $root.Files.Get "files/ensure-secret.py" | quote }}
        env:
        - name: SECRET_NAME
          value: {{ .name }}
        - name: FIXED_KEYS
          value: {{ .fixed | toJson | quote }}
        - name: RANDOM_KEYS
          value: {{ .random }}
        - name: LABELS
          value: {{ dict "app" (printf "airbyte-%s" .component) "app.kubernetes.io/name" .component "app.kubernetes.io/component" "credentials" | toJson | quote }}
        resources:
          {{- toYaml $root.Values.connections.resources | nindent 10 }}
        securityContext:
          {{- include "wrapper.containerSecurityContext" $root | nindent 10 }}
{{- end -}}

{{/* ExternalSecret name: data {secretKey: {key, property}}, optional template {key: text};
as an install hook (hook true) it exists before the database and the bootloader, and its
Secret outlives the ExternalSecret that a sync replaces */}}
{{- define "wrapper.externalSecret" -}}
{{- $root := .root }}
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: {{ .name }}
  namespace: {{ $root.Release.Namespace }}
  labels:
    app: airbyte-{{ .component }}
    app.kubernetes.io/name: {{ .component }}
    app.kubernetes.io/component: credentials
    {{- include "airbyte.commonLabels" $root | nindent 4 }}
  {{- if .hook }}
  annotations:
    helm.sh/hook: pre-install,pre-upgrade
    helm.sh/hook-weight: "-6"
    helm.sh/hook-delete-policy: before-hook-creation
    argocd.argoproj.io/hook: PreSync
    argocd.argoproj.io/hook-delete-policy: HookFailed
    argocd.argoproj.io/sync-wave: "-6"
  {{- end }}
spec:
  refreshInterval: {{ $root.Values.secrets.externalSecret.refreshInterval }}
  secretStoreRef:
    kind: {{ .store.kind }}
    name: {{ .store.name }}
  target:
    name: {{ .name }}
    creationPolicy: {{ ternary "Orphan" "Owner" (eq .hook true) }}
    deletionPolicy: Retain
    {{- with .template }}
    template:
      engineVersion: v2
      data:
        {{- range $k, $v := . }}
        {{ $k }}: {{ $v | quote }}
        {{- end }}
    {{- end }}
  data:
  {{- range $secretKey, $ref := .data }}
  - secretKey: {{ $secretKey }}
    remoteRef:
      key: {{ $ref.key }}
      property: {{ $ref.property | quote }}
      conversionStrategy: Default
      decodingStrategy: None
      metadataPolicy: None
      nullBytePolicy: Ignore
  {{- end }}
{{- end -}}
