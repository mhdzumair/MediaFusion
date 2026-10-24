FROM qbittorrentofficial/qbittorrent-nox:latest

# Setting environment variables
ENV QBT_LEGAL_NOTICE=confirm \
    QBT_WEBUI_PORT=8080 \
    QBT_TORRENTING_PORT=6881 \
    DOWNLOADS_PATH=/downloads \
    PROFILE_PATH=/config

# Install Apache2 with WebDAV and reverse proxy support plus supervisord
# apache2-proxy and apache2-webdav ship conf.d files that already load the proxy
# and dav modules, only rewrite_module is left commented out in httpd.conf
RUN apk --no-cache upgrade \
    && apk --no-cache add apache2 apache2-proxy apache2-webdav supervisor \
    && sed -i 's|^#LoadModule rewrite_module |LoadModule rewrite_module |' /etc/apache2/httpd.conf \
    && for module in proxy_module proxy_http_module dav_module dav_fs_module rewrite_module; do \
           httpd -M 2>/dev/null | grep -q "^ $module " \
               || { echo "Apache module $module is not loaded" >&2; exit 1; }; \
       done \
    && mkdir -p /config /downloads /run/apache2 /var/log/apache2 /var/log/supervisor \
    && chown -R qbtUser:qbtUser /config /downloads /var/log/apache2 /run/apache2

# Setup work directory
WORKDIR /downloads

# Copy Apache and supervisord configuration files
COPY apache-config.conf /etc/apache2/conf.d/000-default.conf
COPY supervisord.conf /etc/supervisord.conf

# Expose Apache's port
EXPOSE 80

# Copy entrypoint script
COPY entrypoint.sh /etc/entrypoint.sh
RUN chmod +x /etc/entrypoint.sh

# Set entrypoint
ENTRYPOINT ["/etc/entrypoint.sh"]
