{{/* Labels that are the same on every object of the chart. */}}
{{- define "chatwoot.commonLabels" -}}
app.kubernetes.io/part-of: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{- define "wrapper.image" -}}
{{ .Values.image.repository }}:{{ .Values.image.tag | default (printf "v%s-ce" .Chart.AppVersion) }}
{{- end -}}

{{/* The break-glass super admin: an address under .invalid, which no mail system and no
identity provider can deliver to or verify, so no login of a person can match it. */}}
{{- define "wrapper.adminEmail" -}}
{{ .Values.account.adminEmail | default "superadmin@chatwoot.invalid" | lower }}
{{- end -}}

{{/* Stops a render whose values cannot work. */}}
{{- define "wrapper.validate" -}}
{{- if or .Values.odoo.enabled .Values.draftAgent.enabled }}
{{- fail "odoo.enabled and draftAgent.enabled are reserved for a later chart version" }}
{{- end }}
{{- if include "wrapper.agentBridge" . }}
{{- if not (regexMatch "^https?://" .Values.agent.url) }}
{{- fail "agent.url, an http or https address, is required with agentBot.enabled or copilotPanel.enabled" }}
{{- end }}
{{- $auth := .Values.agent.auth }}
{{- if not (has $auth.type (list "none" "serviceAccountToken" "clientAssertion")) }}
{{- fail "agent.auth.type is none, serviceAccountToken or clientAssertion" }}
{{- end }}
{{- if and (ne $auth.type "none") (not $auth.audience) }}
{{- fail "agent.auth.audience is required unless agent.auth.type is none" }}
{{- end }}
{{- if and (eq $auth.type "clientAssertion") (not (and $auth.tokenUrl $auth.clientId)) }}
{{- fail "agent.auth.tokenUrl and agent.auth.clientId are required with agent.auth.type clientAssertion" }}
{{- end }}
{{- end }}
{{- if and .Values.copilotPanel.enabled (not .Values.sso.enabled) }}
{{- fail "copilotPanel.enabled needs sso.enabled: the panel is served behind the staff login" }}
{{- end }}
{{- if not (has .Values.attachments.storage (list "volume" "objectBucketClaim")) }}
{{- fail "attachments.storage is volume or objectBucketClaim" }}
{{- end }}
{{- if and (eq .Values.attachments.storage "objectBucketClaim") (not .Values.attachments.objectBucketClaim.storageClassName) }}
{{- fail "attachments.objectBucketClaim.storageClassName is required with attachments.storage objectBucketClaim" }}
{{- end }}
{{- if and .Values.sso.enabled (not .Values.sso.issuerUrl) }}
{{- fail "sso.issuerUrl is required with sso.enabled" }}
{{- end }}
{{- if not (regexMatch "^[a-z0-9._+-]+@([a-z0-9-]+\\.)+invalid$" (include "wrapper.adminEmail" .)) }}
{{- fail "account.adminEmail must be an address under the reserved domain .invalid, for example superadmin@chatwoot.invalid" }}
{{- end }}
{{- if and .Values.sso.enabled (not (hasPrefix "https://" .Values.sso.issuerUrl)) }}
{{- fail "sso.issuerUrl must be an https address" }}
{{- end }}
{{- /* the dashboard keeps its session cookie for whole days rounded down */}}
{{- $hours := int .Values.sso.session.chatwootHours }}
{{- if and .Values.sso.enabled (or (lt $hours 25) (gt $hours 720)) }}
{{- fail "sso.session.chatwootHours must be a whole number of hours from 25 to 720: below 25 the dashboard session cookie expires at once and every login loops" }}
{{- end }}
{{- end -}}

{{/* Non-empty when the agent bridge runs: for the bot, the panel or both. */}}
{{- define "wrapper.agentBridge" -}}
{{- if or .Values.agentBot.enabled .Values.copilotPanel.enabled }}true{{ end }}
{{- end -}}

{{/* Address of the bridge's webhook port, which Chatwoot may reach on a private address. */}}
{{- define "wrapper.agentBotUrl" -}}
http://chatwoot-agent-bridge:8080/
{{- end -}}

{{/* Environment of a container that asks the agent (agent_bot.py). */}}
{{- define "wrapper.agentEnv" -}}
{{- $a := .Values.agent }}
- name: A2A_URL
  value: {{ $a.url | quote }}
- name: A2A_TIMEOUT
  value: {{ $a.timeoutSeconds | quote }}
- name: A2A_AUTH
  value: {{ $a.auth.type | quote }}
{{- if ne $a.auth.type "none" }}
- name: A2A_TOKEN_FILE
  value: /var/run/secrets/agent/token
{{- end }}
{{- if eq $a.auth.type "clientAssertion" }}
- name: A2A_TOKEN_URL
  value: {{ $a.auth.tokenUrl | quote }}
- name: A2A_CLIENT_ID
  value: {{ $a.auth.clientId | quote }}
- name: A2A_SCOPE
  value: {{ $a.auth.scope | quote }}
{{- end }}
- name: PYTHONUNBUFFERED
  value: "1"
{{- end }}

{{/* The ServiceAccount token the agent gets, renewed by the kubelet. */}}
{{- define "wrapper.agentTokenVolume" -}}
{{- if ne .Values.agent.auth.type "none" }}
- name: agent-token
  projected:
    defaultMode: 0440
    sources:
    - serviceAccountToken:
        audience: {{ .Values.agent.auth.audience | quote }}
        expirationSeconds: 3600
        path: token
{{- end }}
{{- end }}

{{- define "wrapper.agentTokenMount" -}}
{{- if ne .Values.agent.auth.type "none" }}
- name: agent-token
  mountPath: /var/run/secrets/agent
  readOnly: true
{{- end }}
{{- end }}

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

{{/* ServiceAccount of a pod that reads the public address from the Route. */}}
{{- define "wrapper.serviceAccount" -}}
{{- if .Values.route.hostname }}
automountServiceAccountToken: false
{{- else }}
serviceAccountName: chatwoot-route
{{- end }}
{{- end }}

{{/* Writes /run/chatwoot/frontend-url (and the oauth2-proxy settings) from the Route. */}}
{{- define "wrapper.routeHostInit" -}}
- name: route-host
  image: {{ .Values.loginBridge.image }}
  command: ["python3", "/scripts/route_host.py", "chatwoot", "/run/chatwoot", {{ .Values.route.hostname | quote }}]
  securityContext:
    {{- include "wrapper.restricted" . | nindent 4 }}
  volumeMounts:
  - name: scripts
    mountPath: /scripts
  - name: run
    mountPath: /run/chatwoot
  resources:
    {{- toYaml .Values.loginBridge.resources | nindent 4 }}
{{- end }}

{{/* Environment of web, worker and the prepare Job. */}}
{{- define "wrapper.env" -}}
- name: RAILS_ENV
  value: production
- name: NODE_ENV
  value: production
- name: RAILS_LOG_TO_STDOUT
  value: "true"
- name: HOME
  value: /tmp
{{- if .Values.route.hostname }}
- name: FRONTEND_URL
  value: https://{{ .Values.route.hostname }}
{{- end }}
- name: SECRET_KEY_BASE
  valueFrom:
    secretKeyRef:
      name: chatwoot-credentials
      key: secret-key-base
{{- range $env, $key := dict "HOST" "host" "PORT" "port" "DATABASE" "dbname" "USERNAME" "username" "PASSWORD" "password" }}
- name: POSTGRES_{{ $env }}
  valueFrom:
    secretKeyRef:
      name: chatwoot-db-app
      key: {{ $key }}
{{- end }}
# libpq reads these; the server certificate is checked against the operator's CA
- name: PGSSLMODE
  value: verify-full
- name: PGSSLROOTCERT
  value: /db-ca/ca.crt
- name: REDIS_URL
  value: redis://chatwoot-redis:6379
- name: REDIS_PASSWORD
  valueFrom:
    secretKeyRef:
      name: chatwoot-credentials
      key: redis-password
- name: ENABLE_ACCOUNT_SIGNUP
  value: "false"
- name: DISABLE_TELEMETRY
  value: "true"
# relays mobile push notifications through chatwoot.com otherwise
- name: ENABLE_PUSH_RELAY_SERVER
  value: "false"
- name: INSTALLATION_ENV
  value: helm
- name: CHATWOOT_ACCOUNT_ID
  value: {{ .Values.account.id | quote }}
{{- if .Values.sso.enabled }}
- name: CHATWOOT_SESSION_HOURS
  value: {{ .Values.sso.session.chatwootHours | quote }}
{{- end }}
{{- if eq .Values.attachments.storage "objectBucketClaim" }}
- name: ACTIVE_STORAGE_SERVICE
  value: s3_compatible
- name: BUCKET_HOST
  valueFrom:
    configMapKeyRef:
      name: chatwoot-attachments
      key: BUCKET_HOST
- name: BUCKET_PORT
  valueFrom:
    configMapKeyRef:
      name: chatwoot-attachments
      key: BUCKET_PORT
- name: STORAGE_BUCKET_NAME
  valueFrom:
    configMapKeyRef:
      name: chatwoot-attachments
      key: BUCKET_NAME
- name: STORAGE_ACCESS_KEY_ID
  valueFrom:
    secretKeyRef:
      name: chatwoot-attachments
      key: AWS_ACCESS_KEY_ID
- name: STORAGE_SECRET_ACCESS_KEY
  valueFrom:
    secretKeyRef:
      name: chatwoot-attachments
      key: AWS_SECRET_ACCESS_KEY
- name: STORAGE_REGION
  value: {{ .Values.attachments.objectBucketClaim.region | quote }}
- name: STORAGE_FORCE_PATH_STYLE
  value: "true"
{{- if .Values.attachments.objectBucketClaim.caConfigMap }}
# start.sh adds this to the system certificates for the S3 client alone
- name: CHART_S3_EXTRA_CA
  value: /etc/chatwoot/s3-ca/service-ca.crt
{{- end }}
{{- else }}
- name: ACTIVE_STORAGE_SERVICE
  value: local
{{- end }}
{{- if .Values.agentBot.enabled }}
# the chart initializer lets webhooks to this address alone reach a private address
- name: CHART_AGENT_BOT_URL
  value: {{ include "wrapper.agentBotUrl" . }}
{{- end }}
{{- with .Values.extraEnv }}
{{ toYaml . }}
{{- end }}
{{- end }}

{{/* .job: the prepare Job, which writes no files and must not hold the ReadWriteOnce volume */}}
{{- define "wrapper.volumeMounts" -}}
- name: tmp
  mountPath: /app/tmp
- name: home
  mountPath: /tmp
- name: scripts
  mountPath: /scripts
- name: chart-initializer
  mountPath: /app/config/initializers/zz_chart.rb
  subPath: chart_initializer.rb
- name: db-ca
  mountPath: /db-ca
{{- if not .Values.route.hostname }}
- name: run
  mountPath: /run/chatwoot
{{- end }}
{{- if eq .Values.attachments.storage "volume" }}
{{- if not .job }}
- name: storage
  mountPath: /app/storage
{{- end }}
{{- else if .Values.attachments.objectBucketClaim.caConfigMap }}
- name: s3-ca
  mountPath: /etc/chatwoot/s3-ca
{{- end }}
{{- end }}

{{- define "wrapper.volumes" -}}
- name: tmp
  emptyDir: {}
- name: home
  emptyDir: {}
- name: run
  emptyDir: {}
- name: scripts
  configMap:
    name: chatwoot-scripts
- name: chart-initializer
  configMap:
    name: chatwoot-scripts
    items:
    - key: chart_initializer.rb
      path: chart_initializer.rb
- name: db-ca
  secret:
    secretName: chatwoot-db-ca
    items:
    - key: ca.crt
      path: ca.crt
{{- if eq .Values.attachments.storage "volume" }}
{{- if not .job }}
- name: storage
  persistentVolumeClaim:
    claimName: chatwoot-storage
{{- end }}
{{- else if .Values.attachments.objectBucketClaim.caConfigMap }}
- name: s3-ca
  configMap:
    name: {{ .Values.attachments.objectBucketClaim.caConfigMap }}
{{- end }}
{{- end }}

{{/* The command of a Chatwoot container: the address from the Route when no hostname is set. */}}
{{- define "wrapper.start" -}}
["/bin/sh", "/scripts/start.sh"
{{- range . }}, {{ . | quote }}{{ end -}}
]
{{- end }}

{{/*
Paths of the host that website visitors reach without a login, as oauth2-proxy
skip-auth-route rules (METHOD=regex). Everything else needs a login.
*/}}
{{- define "wrapper.publicRoutes" -}}
{{- $p := .Values.sso.public -}}
{{- $r := list -}}
{{- if $p.widget -}}
{{- $r = concat $r (list
  "GET=^/packs/js/sdk\\.js$"
  "GET=^/vite/assets/[^/]+$"
  "GET=^/widget$"
  "^/api/v1/widget/"
  "GET=^/cable$"
  "GET=^/audio/(widget|dashboard)/[^/]+\\.mp3$"
  "GET=^/assets/images/[^/]+$"
  "GET=^/rails/active_storage/(blobs|representations)/(proxy|redirect)/"
  "GET=^/rails/active_storage/disk/"
) -}}
{{- end -}}
{{- if $p.inboxApi -}}
{{- $r = append $r "^/public/api/v1/inboxes/" -}}
{{- end -}}
{{- if $p.surveys -}}
{{- $r = concat $r (list "GET=^/survey/responses/[^/]+$" "^/public/api/v1/csat_survey/") -}}
{{- end -}}
{{- if $p.helpCenter -}}
{{- $r = append $r "GET=^/hc/" -}}
{{- end -}}
{{- toJson $r -}}
{{- end }}
