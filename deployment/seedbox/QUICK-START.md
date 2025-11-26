# MediaFusion Ultra Seedbox - Quick Start Guide

**TL;DR: Deploy MediaFusion without Docker on your seedbox**

## ⚡ Super Quick Installation (5 minutes)

### 1. Prepare Cloud Services (One-time setup)

#### MongoDB Atlas (Free - Required)
```
1. Visit: https://www.mongodb.com/cloud/atlas/register
2. Create free M0 cluster (takes 5 min)
3. Create database user
4. Whitelist IP: 0.0.0.0/0
5. Get connection string
```

#### Redis Cloud (Free - Required)
```
1. Visit: https://redis.com/try-free/
2. Create free 30MB database
3. Get connection URL
```

### 2. Run Installation Script

```bash
# SSH into your seedbox, then:
cd ~
wget https://raw.githubusercontent.com/mhdzumair/MediaFusion/main/deployment/seedbox/deploy-ultra-seedbox.sh
chmod +x deploy-ultra-seedbox.sh
./deploy-ultra-seedbox.sh
```

### 3. Answer Prompts

The script will ask for:
- **Seedbox domain**: `username.ultra.seedbox.io`
- **Port**: `8000` (or any available port)
- **MongoDB URI**: Paste from MongoDB Atlas
- **Redis option**: Choose option 1 (Redis Cloud)
- **Redis URL**: Paste from Redis Cloud
- **API Password**: Choose a secure password

### 4. Configure Reverse Proxy

The installer creates the service but you need to add reverse proxy configuration.

**For Nginx** (add to your nginx config):
```nginx
location /mediafusion/ {
    rewrite ^/mediafusion/(.*) /$1 break;
    proxy_pass http://localhost:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

**For Apache** (add to your vhost):
```apache
<Location /mediafusion>
    ProxyPass http://localhost:8000
    ProxyPassReverse http://localhost:8000
    RequestHeader set X-Forwarded-Proto "https"
</Location>
```

Reload nginx/apache after adding configuration.

### 5. Access MediaFusion

Visit: `https://your-seedbox.com/mediafusion/configure`

## ✅ That's It!

Your MediaFusion instance is now running with:
- 📊 Memory usage: ~400-600MB
- 🚀 No Docker required
- ☁️ Cloud databases (no local MongoDB/Redis overhead)
- 🔒 Secure HTTPS via reverse proxy

## 🛠️ Common Commands

```bash
# Check status
systemctl --user status mediafusion

# View logs
journalctl --user -u mediafusion -f

# Restart
systemctl --user restart mediafusion

# Stop
systemctl --user stop mediafusion

# Start
systemctl --user start mediafusion
```

## 🐛 Troubleshooting

### Service won't start?
```bash
# Check logs
journalctl --user -u mediafusion -n 50

# Or
tail -f ~/mediafusion/logs/error.log
```

### Can't access via web?
1. Check service is running: `systemctl --user status mediafusion`
2. Check port is correct: `netstat -tulpn | grep 8000`
3. Verify reverse proxy config is loaded
4. Check firewall allows port 8000

### Out of memory?
```bash
# Edit config
nano ~/mediafusion/.env

# Change to:
GUNICORN_WORKERS=1
GUNICORN_THREADS=1

# Restart
systemctl --user restart mediafusion
```

## 📖 Full Documentation

For detailed information, see:
- **Full Guide**: `deployment/seedbox/ULTRA-SEEDBOX-DEPLOYMENT.md`
- **Configuration**: Edit `~/mediafusion/.env`
- **Logs**: `~/mediafusion/logs/`

## 💾 Resource Usage

Expected usage:
- **RAM**: 400-600MB
- **CPU**: 10-30% of 1 core
- **Disk**: ~500MB
- **Network**: Minimal

## 🎯 Next Steps

1. ✅ Visit `/mediafusion/configure`
2. ✅ Add your Debrid service API keys
3. ✅ Install in Stremio
4. ✅ Start streaming!

## 🆘 Need Help?

1. Check logs: `journalctl --user -u mediafusion -f`
2. Read full guide: `deployment/seedbox/ULTRA-SEEDBOX-DEPLOYMENT.md`
3. Open issue: https://github.com/mhdzumair/MediaFusion/issues

---

**Memory Comparison:**

| Deployment | Memory |
|------------|--------|
| Docker (standard) | 3-4GB |
| Docker (optimized) | 1-1.5GB |
| **Seedbox (this)** | **400-600MB** ✅ |

Happy Streaming! 🎬
