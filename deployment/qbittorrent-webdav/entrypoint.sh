#!/bin/sh

# qBittorrent setup from base image
qbtConfigFile="$PROFILE_PATH/qBittorrent/config/qBittorrent.conf"

# Ensure the configuration and download directories exist
mkdir -p "$PROFILE_PATH/qBittorrent/config"

# Adjust file ownership and permissions
chown -R qbtUser:qbtUser $PROFILE_PATH $DOWNLOADS_PATH /var/log/apache2 /var/run/apache2

# Custom logic to handle qBittorrent configuration setup
if [ ! -f "$qbtConfigFile" ]; then
    echo "Creating qBittorrent configuration file at $qbtConfigFile"
    cat << EOF > "$qbtConfigFile"
[BitTorrent]
Session\DefaultSavePath=$DOWNLOADS_PATH
Session\Port=6881
Session\TempPath=$DOWNLOADS_PATH/temp

[LegalNotice]
Accepted=true
EOF
fi

# Path to the .htpasswd file
HTPASSWD_PATH="/etc/apache2/.htpasswd"

# Check if .htpasswd file exists and configure basic auth for WebDAV accordingly
if [ -f "$HTPASSWD_PATH" ]; then
    echo "Basic auth password file found. Configuring WebDAV with basic auth."
    sed -i '/<Directory \/downloads>/,/<\/Directory>/{/Require /d}' /etc/apache2/conf.d/000-default.conf
    sed -i '/<Directory \/downloads>/a \\tAuthType Basic\n\tAuthName "WebDAV"\n\tAuthUserFile '"$HTPASSWD_PATH"'\n\tRequire valid-user' /etc/apache2/conf.d/000-default.conf
else
    echo "No basic auth password file found. Configuring WebDAV without basic auth."
fi

# start supervisord
/usr/bin/supervisord -c /etc/supervisord.conf
