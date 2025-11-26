#!/bin/bash
set -e

echo "Installing Redis in user space..."
echo ""

# Set Redis directory
REDIS_DIR="$HOME/redis"
REDIS_VERSION="7.2.4"
REDIS_PORT="${REDIS_PORT:-6379}"

# Create directory
mkdir -p "$REDIS_DIR"
cd "$REDIS_DIR"

# Download Redis
echo "📥 Downloading Redis ${REDIS_VERSION}..."
wget -q "http://download.redis.io/releases/redis-${REDIS_VERSION}.tar.gz"
tar xzf "redis-${REDIS_VERSION}.tar.gz"
cd "redis-${REDIS_VERSION}"

# Compile Redis
echo "🔧 Compiling Redis (this may take a few minutes)..."
make -j$(nproc) PREFIX="$REDIS_DIR" install

# Create configuration
echo "📝 Creating Redis configuration..."
mkdir -p "$REDIS_DIR/data"
mkdir -p "$REDIS_DIR/logs"

cat > "$REDIS_DIR/redis.conf" <<EOF
# Redis configuration for user space
port $REDIS_PORT
bind 127.0.0.1
protected-mode yes
daemonize yes
pidfile $REDIS_DIR/redis.pid
logfile $REDIS_DIR/logs/redis.log
dir $REDIS_DIR/data
dbfilename dump.rdb

# Memory settings
maxmemory 128mb
maxmemory-policy allkeys-lru

# Persistence
save 60 1
appendonly yes
appendfilename "appendonly.aof"

# Performance
tcp-backlog 511
timeout 0
tcp-keepalive 300
EOF

# Create startup script
cat > "$REDIS_DIR/start-redis.sh" <<'STARTSCRIPT'
#!/bin/bash
cd "$(dirname "$0")"
./bin/redis-server redis.conf
echo "Redis started on port ${REDIS_PORT:-6379}"
STARTSCRIPT

chmod +x "$REDIS_DIR/start-redis.sh"

# Create stop script
cat > "$REDIS_DIR/stop-redis.sh" <<'STOPSCRIPT'
#!/bin/bash
cd "$(dirname "$0")"
if [ -f redis.pid ]; then
    PID=$(cat redis.pid)
    if ps -p $PID > /dev/null; then
        echo "Stopping Redis (PID: $PID)..."
        ./bin/redis-cli shutdown
        echo "Redis stopped"
    else
        echo "Process not running, cleaning up PID file"
        rm redis.pid
    fi
else
    echo "No PID file found. Redis may not be running."
fi
STOPSCRIPT

chmod +x "$REDIS_DIR/stop-redis.sh"

# Create systemd user service
mkdir -p "$HOME/.config/systemd/user"

cat > "$HOME/.config/systemd/user/redis.service" <<EOF
[Unit]
Description=Redis In-Memory Data Store (User Space)
After=network.target

[Service]
Type=forking
ExecStart=$REDIS_DIR/bin/redis-server $REDIS_DIR/redis.conf
ExecStop=$REDIS_DIR/bin/redis-cli shutdown
Restart=on-failure
RestartSec=5
PIDFile=$REDIS_DIR/redis.pid

# Resource limits
MemoryMax=256M

[Install]
WantedBy=default.target
EOF

# Enable and start service
systemctl --user daemon-reload
systemctl --user enable redis.service
systemctl --user start redis.service

echo ""
echo "✅ Redis installed successfully!"
echo ""
echo "Redis is running on: localhost:$REDIS_PORT"
echo ""
echo "Useful commands:"
echo "  Start:  systemctl --user start redis"
echo "  Stop:   systemctl --user stop redis"
echo "  Status: systemctl --user status redis"
echo ""
echo "Or use the scripts:"
echo "  $REDIS_DIR/start-redis.sh"
echo "  $REDIS_DIR/stop-redis.sh"
echo ""
