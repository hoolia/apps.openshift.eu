{{/* Labels that are the same on every object of the chart. */}}
{{- define "matomo.commonLabels" -}}
app.kubernetes.io/part-of: {{ .Release.Name | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{/* The Secret with the database passwords. */}}
{{- define "matomo.secretName" -}}
{{- .Values.secrets.existingSecret | default "matomo-db" -}}
{{- end -}}

{{- define "matomo.restricted" -}}
allowPrivilegeEscalation: false
capabilities:
  drop:
  - ALL
runAsNonRoot: true
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{/* nginx in front of php-fpm, after github.com/matomo-org/matomo-nginx */}}
{{- define "matomo.nginxConf" -}}
server {
  listen 8080;
  server_name _;
  root /var/www/html;
  index index.php;
  client_max_body_size 16m;
  # the router terminates TLS
  {{- range .Values.nginx.realIpFrom }}
  set_real_ip_from {{ . }};
  {{- end }}
  real_ip_header X-Forwarded-For;
  real_ip_recursive on;

  location ~ ^/(index|matomo|piwik|js/index|plugins/HeatmapSessionRecording/configs)\.php$ {
    include fastcgi_params;
    fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
    fastcgi_param HTTP_PROXY "";
    fastcgi_pass 127.0.0.1:9000;
  }
  location ~* ^.+\.php$ {
    deny all;
    return 403;
  }
  location / {
    try_files $uri $uri/ =404;
  }
  location ~ ^/(config|tmp|core|lang) {
    deny all;
    return 403;
  }
  location ~ /\.ht {
    deny all;
    return 403;
  }
  location ~ js/container_.*_preview\.js$ {
    expires off;
    add_header Cache-Control 'private, no-cache, no-store';
  }
  location ~ \.(gif|ico|jpg|png|svg|js|css|htm|html|mp3|mp4|wav|ogg|avi|ttf|eot|woff|woff2)$ {
    allow all;
    expires 1h;
    add_header Pragma public;
    add_header Cache-Control "public";
  }
  location ~ ^/(libs|vendor|plugins|misc|node_modules) {
    deny all;
    return 403;
  }
  location ~/(.*\.md|LEGAL|README) {
    default_type text/plain;
  }
}
{{- end -}}
