# Menjalankan skrip pembantu (staging dan hosting) di Linux untuk e2e di Docker
# Desktop. Hanya untuk test: di VPS skrip dijalankan root langsung lewat sudo.
# nginx dipakai sungguhan untuk `nginx -t`/`nginx -T` (NGINX_UJI_SAJA=1), openssl
# untuk SERTIFIKAT_SENDIRI=1, dan GNU tar untuk prod-backup, serta flock util-linux (opsi -w; flock BusyBox tidak punya).
FROM docker:27-cli
RUN apk add --no-cache bash coreutils curl grep setpriv nginx openssl tar flock \
 && mkdir -p /run/nginx /etc/letsencrypt \
 && printf 'ssl_protocols TLSv1.2 TLSv1.3;\n' > /etc/letsencrypt/options-ssl-nginx.conf \
 && printf 'include /srv/wpmgr/nginx-hosting/*.conf;\n' > /etc/nginx/http.d/wpmgr-hosting.conf
