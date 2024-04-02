FROM qbittorrentofficial/qbittorrent-nox:latest

# Install Apache2 and supervisord
RUN apk --no-cache add apache2 apache2-proxy apache2-webdav mod_dav_svn \
    && mkdir -p /run/apache2 /var/log/supervisor \
    && apk add --no-cache supervisor

# Configure Apache to load necessary modules for WebDAV and reverse proxy
RUN sed -i 's/#LoadModule proxy_module/LoadModule proxy_module/' /etc/apache2/httpd.conf \
    && sed -i 's/#LoadModule proxy_http_module/LoadModule proxy_http_module/' /etc/apache2/httpd.conf \
    && sed -i 's/#LoadModule dav_module/LoadModule dav_module/' /etc/apache2/httpd.conf \
    && sed -i 's/#LoadModule dav_fs_module/LoadModule dav_fs_module/' /etc/apache2/httpd.conf \
    && sed -i 's/#LoadModule rewrite_module/LoadModule rewrite_module/' /etc/apache2/httpd.conf

# Setting environment variables
ENV QBT_EULA=accept \
    QBT_WEBUI_PORT=8080 \
    PUID=1000 \
    PGID=1000 \
    DOWNLOADS_PATH=/downloads \
    PROFILE_PATH=/config

# Create necessary directories and set permissions
RUN mkdir -p /config /downloads /var/log/apache2 \
    && chown -R qbtUser:qbtUser $DOWNLOADS_PATH $PROFILE_PATH /var/log/apache2

# Setup work directory
WORKDIR $DOWNLOADS_PATH

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

