#!/bin/sh

set -e

profilePath="${PROFILE_PATH:-/config}"
downloadsPath="${DOWNLOADS_PATH:-/downloads}"
htpasswdPath="/etc/apache2/.htpasswd"
webdavConf="/etc/apache2/conf.d/010-webdav.conf"

mkdir -p "$profilePath" "$downloadsPath"

# httpd refuses to start with "httpd (pid N) already running" when a pid file
# survived a kill, drop the leftovers before handing over to supervisord
rm -f /run/apache2/httpd.pid /run/supervisord.pid

# downloads are populated by the host user, only fix the top level directory
# like the upstream entrypoint does
chown qbtUser:qbtUser "$downloadsPath"
chown -R qbtUser:qbtUser "$profilePath" /var/log/apache2 /run/apache2

# write the WebDAV access rules from scratch on every start, editing the vhost
# in place would stack up duplicate directives on each restart
rm -f "$authConf"
if [ -f "$htpasswdPath" ]; then
    echo "Basic auth password file found. Configuring WebDAV with basic auth."
    cat > "$webdavConf" <<EOF
Alias /webdav $downloadsPath
<Directory $downloadsPath>
    DAV On
    Options Indexes FollowSymLinks MultiViews
    AllowOverride All
    AuthType Basic
    AuthName "WebDAV"
    AuthUserFile $htpasswdPath
    Require valid-user
</Directory>
EOF
else
    echo "No basic auth password file found. Configuring WebDAV without basic auth."
    cat > "$webdavConf" <<EOF
Alias /webdav $downloadsPath
<Directory $downloadsPath>
    DAV On
    Options Indexes FollowSymLinks MultiViews
    AllowOverride All
    Require all granted
</Directory>
EOF
fi

# qBittorrent itself is started by the upstream entrypoint, see supervisord.conf
exec /usr/bin/supervisord -c /etc/supervisord.conf
