# MediaFusion Ultra Seedbox Deployment Guide 🌱

**No Docker Required - User Space Installation**

This guide is specifically for deploying MediaFusion on Ultra Seedbox and similar shared hosting environments where:
- ❌ **No root access** available
- ❌ **No Docker** allowed
- ❌ **Limited virtual memory**
- ✅ **User space deployment** required
- ✅ **Reverse proxy** access available
- ✅ **Good physical RAM** (10GB+)

## 📋 Table of Contents

1. [Prerequisites](#prerequisites)
2. [Quick Start](#quick-start)
3. [Manual Installation](#manual-installation)
4. [Reverse Proxy Configuration](#reverse-proxy-configuration)
5. [Service Management](#service-management)
6. [Memory Optimization](#memory-optimization)
7. [Troubleshooting](#troubleshooting)
8. [Updating MediaFusion](#updating-mediafusion)

## 🔍 Prerequisites

### Required

- **Python 3.12+** installed on your seedbox
- **SSH access** to your seedbox
- **MongoDB Atlas account** (free tier available)
- **Redis service** (free cloud options available, or local installation)
- **Reverse proxy** access (nginx/apache)
- **At least 1GB** available RAM

### Optional but Recommended

- **systemd** access (for service management)
- **Git** installed
- **Debrid service accounts** (Real-Debrid, AllDebrid, etc.)

## 🚀 Quick Start

### One-Line Installation

```bash
curl -fsSL https://raw.githubusercontent.com/mhdzumair/MediaFusion/main/deployment/seedbox/deploy-ultra-seedbox.sh | bash
```

Or download and run:

```bash
wget https://raw.githubusercontent.com/mhdzumair/MediaFusion/main/deployment/seedbox/deploy-ultra-seedbox.sh
chmod +x deploy-ultra-seedbox.sh
./deploy-ultra-seedbox.sh
```

The script will:
1. ✅ Check Python version
2. ✅ Clone MediaFusion repository
3. ✅ Create virtual environment
4. ✅ Install dependencies
5. ✅ Configure MongoDB Atlas and Redis
6. ✅ Set up systemd service
7. ✅ Start MediaFusion

### What You'll Need

The installation script will ask for:

1. **Seedbox domain** (e.g., `username.ultra.seedbox.io`)
2. **Port number** (e.g., `8000`)
3. **MongoDB Atlas connection string**
4. **Redis service** (Cloud Redis, Upstash, or local installation)
5. **API password** (for securing your instance)

## 📝 Manual Installation

If you prefer to install manually:

### Step 1: Set Up MongoDB Atlas (Free)

1. Go to https://www.mongodb.com/cloud/atlas
2. Create a free account
3. Click "Build a Database"
4. Choose **M0 (Free tier)** - 512MB storage
5. Select a cloud provider and region close to your seedbox
6. Create cluster (takes ~5 minutes)
7. Click "Database Access" → "Add New Database User"
   - Username: `mediafusion`
   - Password: Generate secure password
   - Database User Privileges: "Read and write to any database"
8. Click "Network Access" → "Add IP Address"
   - Click "Allow Access from Anywhere" (0.0.0.0/0)
   - Or add your seedbox IP specifically
9. Click "Connect" → "Connect your application"
10. Copy connection string:
    ```
    mongodb+srv://mediafusion:<password>@cluster0.xxxxx.mongodb.net/mediafusion?retryWrites=true&w=majority
    ```
11. Replace `<password>` with your actual password

### Step 2: Set Up Redis (Choose One)

#### Option A: Redis Cloud (Recommended - Free Tier)

1. Go to https://redis.com/try-free/
2. Sign up for free account
3. Create a database:
   - Name: `mediafusion`
   - Type: Redis Stack
   - Plan: Free (30MB)
4. Get connection URL:
   ```
   redis://default:<password>@redis-xxxxx.c1.us-east-1-1.ec2.cloud.redislabs.com:12345
   ```

#### Option B: Upstash Redis (Serverless - Free Tier)

1. Go to https://upstash.com/
2. Sign up for free account
3. Create Redis database
4. Copy connection URL from dashboard

#### Option C: Local Redis Installation

```bash
cd ~/mediafusion/deployment/seedbox
./install-redis.sh
```

Uses: `redis://localhost:6379`

### Step 3: Install MediaFusion

```bash
# Clone repository
cd ~
git clone https://github.com/mhdzumair/MediaFusion
cd MediaFusion

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install uv
uv pip install -e .

# Create necessary directories
mkdir -p logs data
```

### Step 4: Configure Environment

Create `.env` file:

```bash
nano .env
```

Add configuration:

```bash
# Server Configuration
HOST_URL="https://your-seedbox.com/mediafusion"
POSTER_HOST_URL="https://your-seedbox.com/mediafusion"

# Database Configuration
MONGO_URI="mongodb+srv://mediafusion:PASSWORD@cluster.mongodb.net/mediafusion?retryWrites=true&w=majority"
REDIS_URL="redis://default:PASSWORD@redis-xxxxx.cloud.redislabs.com:12345"

# Security
SECRET_KEY="generate-with-openssl-rand-hex-32"
API_PASSWORD="your-secure-password"

# Performance (Optimized for Seedbox)
GUNICORN_WORKERS=1
GUNICORN_THREADS=2

# Logging
LOG_LEVEL="WARNING"

# Disable resource-intensive features
IS_SCRAP_FROM_TORRENTIO="false"
is_scrap_from_mediafusion="false"
is_scrap_from_zilean="false"

# Rate limiting
ENABLE_RATE_LIMIT="true"
```

Generate secret key:
```bash
openssl rand -hex 32
```

### Step 5: Run Database Migrations

```bash
source venv/bin/activate
source .env
beanie migrate -uri "$MONGO_URI" -db mediafusion -p migrations/
```

### Step 6: Create Startup Script

Create `start-mediafusion.sh`:

```bash
#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate
source .env

export $(cat .env | grep -v '^#' | xargs)

exec gunicorn api.main:app \
    -w ${GUNICORN_WORKERS:-1} \
    -k uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:8000 \
    --timeout 180 \
    --max-requests 200 \
    --max-requests-jitter 50 \
    --worker-tmp-dir /tmp \
    --access-logfile logs/access.log \
    --error-logfile logs/error.log \
    --log-level warning \
    --pid mediafusion.pid
```

Make executable:
```bash
chmod +x start-mediafusion.sh
```

### Step 7: Set Up Systemd Service

Create `~/.config/systemd/user/mediafusion.service`:

```ini
[Unit]
Description=MediaFusion Stremio Addon
After=network.target

[Service]
Type=simple
WorkingDirectory=/home/username/MediaFusion
ExecStart=/home/username/MediaFusion/start-mediafusion.sh
Restart=on-failure
RestartSec=10
StandardOutput=append:/home/username/MediaFusion/logs/output.log
StandardError=append:/home/username/MediaFusion/logs/error.log

# Resource limits
MemoryMax=1G
CPUQuota=150%

[Install]
WantedBy=default.target
```

**Replace `/home/username/` with your actual home directory path!**

Enable and start:
```bash
systemctl --user daemon-reload
systemctl --user enable mediafusion.service
systemctl --user start mediafusion.service
systemctl --user status mediafusion.service
```

## 🔧 Reverse Proxy Configuration

### Nginx Configuration

Add to your nginx configuration (usually managed by Ultra Seedbox panel):

```nginx
location /mediafusion/ {
    # Remove trailing slash for proper routing
    rewrite ^/mediafusion/(.*) /$1 break;

    proxy_pass http://localhost:8000;
    proxy_http_version 1.1;

    # Headers
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header X-Forwarded-Port $server_port;

    # WebSocket support (if needed)
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";

    # Timeouts
    proxy_connect_timeout 60s;
    proxy_send_timeout 180s;
    proxy_read_timeout 180s;

    # Buffering
    proxy_buffering off;
    proxy_request_buffering off;
}
```

### Apache Configuration

Add to your Apache configuration:

```apache
<Location /mediafusion>
    ProxyPreserveHost On
    ProxyPass http://localhost:8000
    ProxyPassReverse http://localhost:8000

    RequestHeader set X-Forwarded-Proto "https"
    RequestHeader set X-Forwarded-Port "443"
</Location>
```

### Testing Reverse Proxy

After configuring:

1. Reload nginx/apache:
   ```bash
   # This might vary depending on your seedbox panel
   sudo systemctl reload nginx
   # or
   sudo service apache2 reload
   ```

2. Test access:
   ```bash
   curl https://your-seedbox.com/mediafusion/health
   ```

   Should return: `{"status":"healthy"}`

## 🛠️ Service Management

### Using systemd (Recommended)

```bash
# Start MediaFusion
systemctl --user start mediafusion

# Stop MediaFusion
systemctl --user stop mediafusion

# Restart MediaFusion
systemctl --user restart mediafusion

# Check status
systemctl --user status mediafusion

# View logs
journalctl --user -u mediafusion -f

# Enable auto-start on login
systemctl --user enable mediafusion
```

### Using Scripts

If systemd is not available:

```bash
# Start
./start-mediafusion.sh

# Stop
./stop-mediafusion.sh

# Restart
./restart-mediafusion.sh

# Check if running
ps aux | grep gunicorn
```

### Using screen/tmux (Alternative)

```bash
# Start in screen
screen -dmS mediafusion bash -c 'cd ~/MediaFusion && ./start-mediafusion.sh'

# Attach to screen
screen -r mediafusion

# Detach: Ctrl+A, then D

# Kill screen
screen -X -S mediafusion quit
```

## 💾 Memory Optimization

### Current Memory Usage

Check memory usage:

```bash
# Check MediaFusion process
ps aux | grep gunicorn

# Monitor in real-time
top -u $USER
```

### Further Optimization

#### Reduce Workers/Threads

Edit `.env`:
```bash
GUNICORN_WORKERS=1
GUNICORN_THREADS=1  # Reduce from 2 to 1
```

Restart:
```bash
systemctl --user restart mediafusion
```

#### Disable Optional Features

In `.env`:
```bash
# Disable all scrapers
IS_SCRAP_FROM_TORRENTIO="false"
is_scrap_from_mediafusion="false"
is_scrap_from_zilean="false"
prowlarr_live_title_search="false"

# Disable caching
store_stremthru_magnet_cache="false"

# Reduce logging
LOG_LEVEL="ERROR"
```

#### Expected Memory Usage

With optimized settings:
- **MediaFusion (1 worker, 1 thread)**: 300-500MB
- **MongoDB Atlas**: 0MB (cloud-hosted)
- **Redis Cloud**: 0MB (cloud-hosted)
- **Total Local**: ~400MB

Or with local Redis:
- **MediaFusion**: 300-500MB
- **Redis (local)**: 50-100MB
- **Total**: ~450MB

## 🐛 Troubleshooting

### MediaFusion Won't Start

```bash
# Check logs
journalctl --user -u mediafusion -n 50

# Or check log files
tail -f ~/MediaFusion/logs/error.log

# Test manually
cd ~/MediaFusion
source venv/bin/activate
source .env
python -m api.main
```

### Port Already in Use

Change port in startup script:
```bash
--bind 0.0.0.0:8001  # Change from 8000 to 8001
```

Update reverse proxy configuration accordingly.

### MongoDB Connection Issues

Test connection:
```bash
source venv/bin/activate
python3 << EOF
from pymongo import MongoClient
client = MongoClient("YOUR_MONGO_URI")
print(client.list_database_names())
EOF
```

Common issues:
- Wrong password
- IP not whitelisted in MongoDB Atlas
- Missing database name in URI

### Redis Connection Issues

Test connection:
```bash
# For Redis Cloud/Upstash
redis-cli -u "YOUR_REDIS_URL" ping

# For local Redis
redis-cli -h localhost -p 6379 ping
```

Should return: `PONG`

### High Memory Usage

```bash
# Check memory
ps aux --sort=-%mem | head -10

# Reduce workers
# Edit .env and set: GUNICORN_WORKERS=1

# Restart
systemctl --user restart mediafusion
```

### Permission Denied Errors

```bash
# Fix permissions
chmod +x ~/MediaFusion/*.sh
chmod 600 ~/MediaFusion/.env
```

### Python Version Issues

If Python 3.12+ not available:

```bash
# Check available versions
python3 --version
python3.12 --version

# Use specific version
python3.12 -m venv venv
```

Contact seedbox support if Python 3.12+ is not installed.

## 🔄 Updating MediaFusion

### Update Process

```bash
# Stop service
systemctl --user stop mediafusion

# Backup current installation
cd ~
cp -r MediaFusion MediaFusion.backup.$(date +%Y%m%d)

# Pull latest changes
cd MediaFusion
git pull

# Update dependencies
source venv/bin/activate
uv pip install -e . --upgrade

# Run migrations
source .env
beanie migrate -uri "$MONGO_URI" -db mediafusion -p migrations/

# Restart service
systemctl --user start mediafusion

# Check status
systemctl --user status mediafusion
```

### Rollback if Issues

```bash
# Stop service
systemctl --user stop mediafusion

# Restore backup
cd ~
rm -rf MediaFusion
mv MediaFusion.backup.YYYYMMDD MediaFusion

# Start service
systemctl --user start mediafusion
```

## 📊 Monitoring

### Check Service Health

```bash
# Health endpoint
curl http://localhost:8000/health

# Via reverse proxy
curl https://your-seedbox.com/mediafusion/health
```

### View Logs

```bash
# Systemd logs
journalctl --user -u mediafusion -f

# Application logs
tail -f ~/MediaFusion/logs/error.log
tail -f ~/MediaFusion/logs/access.log

# Output logs
tail -f ~/MediaFusion/logs/output.log
```

### Monitor Performance

```bash
# Real-time process monitoring
htop -u $USER

# Check specific process
ps aux | grep gunicorn

# Memory usage
free -h
```

## 🔐 Security Best Practices

1. **Strong API Password**: Use a long, random password
2. **Keep .env Secure**:
   ```bash
   chmod 600 ~/.env
   ```
3. **HTTPS Only**: Always use reverse proxy with SSL
4. **IP Restrictions**: Consider restricting MongoDB Atlas to your seedbox IP
5. **Regular Updates**: Keep MediaFusion updated
6. **Monitor Logs**: Check for suspicious activity

## 📱 Accessing MediaFusion

Once deployed, access at:
- **Main URL**: `https://your-seedbox.com/mediafusion`
- **Configuration**: `https://your-seedbox.com/mediafusion/configure`
- **Health Check**: `https://your-seedbox.com/mediafusion/health`

## ✅ Success Checklist

- [ ] Python 3.12+ installed
- [ ] MongoDB Atlas cluster created and accessible
- [ ] Redis service configured (cloud or local)
- [ ] MediaFusion cloned and dependencies installed
- [ ] `.env` file configured with all required settings
- [ ] Database migrations completed successfully
- [ ] systemd service created and enabled
- [ ] MediaFusion service running without errors
- [ ] Reverse proxy configured and working
- [ ] Can access `/mediafusion/health` endpoint
- [ ] Can access `/mediafusion/configure` page
- [ ] Memory usage under 1GB

## 💡 Tips for Ultra Seedbox

1. **Use MongoDB Atlas**: Saves ~400MB RAM vs local MongoDB
2. **Use Redis Cloud**: Saves ~100MB RAM vs local Redis
3. **Single Worker**: One worker is enough for personal use
4. **Monitor Regularly**: Use `htop` to watch resource usage
5. **Enable Rate Limiting**: Prevents abuse and saves resources
6. **Disable Unused Scrapers**: Only enable scrapers you actually use
7. **Regular Updates**: Keep MediaFusion updated for performance improvements

## 📞 Getting Help

If you encounter issues:

1. **Check Logs**: `journalctl --user -u mediafusion -n 100`
2. **Test Components**:
   - MongoDB connection
   - Redis connection
   - Python version
   - Port availability
3. **Resource Usage**: `htop` or `top`
4. **Open Issue**: https://github.com/mhdzumair/MediaFusion/issues

Include in your issue:
- Seedbox provider and plan
- Python version: `python3 --version`
- Error logs
- Memory usage: `free -h`
- MediaFusion logs

## 🎯 Performance Expectations

With this deployment:

| Metric | Expected Value |
|--------|---------------|
| Memory Usage | 400-600MB |
| CPU Usage | 10-30% (1 core) |
| Startup Time | 10-20 seconds |
| Response Time | 200-500ms |
| Concurrent Users | 5-10 (personal use) |

## 🌟 Advanced Configuration

### Custom Port

Change port in `start-mediafusion.sh`:
```bash
--bind 0.0.0.0:YOUR_PORT
```

### Multiple Workers (if you have RAM)

In `.env`:
```bash
GUNICORN_WORKERS=2
GUNICORN_THREADS=2
```

Expected memory: ~800MB-1.2GB

### Enable All Features

If you have sufficient memory:

In `.env`:
```bash
IS_SCRAP_FROM_TORRENTIO="true"
is_scrap_from_mediafusion="true"
is_scrap_from_zilean="true"
prowlarr_live_title_search="true"
```

Expected memory: ~1-1.5GB

### Backup Configuration

Create a backup script:

```bash
#!/bin/bash
BACKUP_DIR="$HOME/mediafusion-backups"
mkdir -p "$BACKUP_DIR"
DATE=$(date +%Y%m%d_%H%M%S)

# Backup .env
cp ~/MediaFusion/.env "$BACKUP_DIR/env_$DATE"

# Backup logs
tar -czf "$BACKUP_DIR/logs_$DATE.tar.gz" ~/MediaFusion/logs/

# Keep only last 7 days
find "$BACKUP_DIR" -type f -mtime +7 -delete

echo "Backup completed: $DATE"
```

## 🚀 Next Steps

After successful deployment:

1. **Configure Stremio**: Visit `/mediafusion/configure`
2. **Add Debrid Services**: Configure Real-Debrid, AllDebrid, etc.
3. **Test Streaming**: Try playing a movie/show
4. **Monitor Performance**: Check logs and resource usage
5. **Enable Features**: Gradually enable scrapers as needed
6. **Set Up Backups**: Backup your `.env` file

Happy Streaming! 🎬
