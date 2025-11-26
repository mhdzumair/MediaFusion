# MediaFusion Seedbox Deployment 🌱

**Docker-Free Deployment for Shared Hosting Environments**

This directory contains deployment scripts and configurations for running MediaFusion on seedbox environments like Ultra Seedbox, where Docker is not available and root access is restricted.

## 📁 Files Overview

| File | Description |
|------|-------------|
| **QUICK-START.md** | 5-minute quick start guide - start here! |
| **ULTRA-SEEDBOX-DEPLOYMENT.md** | Complete deployment documentation |
| **deploy-ultra-seedbox.sh** | Automated installation script |
| **install-redis.sh** | Local Redis installation (optional) |
| **nginx-reverse-proxy.conf** | Nginx reverse proxy configuration |
| **apache-reverse-proxy.conf** | Apache reverse proxy configuration |

## 🚀 Quick Start

```bash
# One-line installation
curl -fsSL https://raw.githubusercontent.com/mhdzumair/MediaFusion/main/deployment/seedbox/deploy-ultra-seedbox.sh | bash
```

Or manual:
```bash
cd ~
wget https://raw.githubusercontent.com/mhdzumair/MediaFusion/main/deployment/seedbox/deploy-ultra-seedbox.sh
chmod +x deploy-ultra-seedbox.sh
./deploy-ultra-seedbox.sh
```

## 📚 Documentation

- **New Users**: Read [QUICK-START.md](QUICK-START.md) (5 minutes)
- **Detailed Guide**: Read [ULTRA-SEEDBOX-DEPLOYMENT.md](ULTRA-SEEDBOX-DEPLOYMENT.md)
- **Reverse Proxy**: See nginx/apache config files

## ✨ Key Features

✅ **No Docker Required** - Pure Python deployment
✅ **No Root Access Needed** - Runs in user space
✅ **Low Memory Usage** - 400-600MB RAM
✅ **Cloud Databases** - Uses MongoDB Atlas & Redis Cloud
✅ **systemd Integration** - Auto-start with systemctl
✅ **Reverse Proxy Ready** - Works with nginx/apache
✅ **Automated Setup** - One script installation

## 📋 Requirements

### Essential
- Python 3.12+ installed
- SSH access to seedbox
- MongoDB Atlas account (free)
- Redis service (free cloud options)
- Reverse proxy (nginx/apache)
- 1GB+ available RAM

### Optional
- systemd access (recommended)
- Custom domain/subdomain

## 🎯 Deployment Options

### Option 1: Full Cloud (Lowest Memory - Recommended)
- MongoDB Atlas (cloud)
- Redis Cloud (cloud)
- MediaFusion only locally
- **Memory**: ~400-500MB

### Option 2: Local Redis
- MongoDB Atlas (cloud)
- Redis (local)
- MediaFusion locally
- **Memory**: ~500-600MB

### Option 3: All Local (Not Recommended)
- MongoDB (local) - requires Docker or manual install
- Redis (local)
- MediaFusion locally
- **Memory**: ~1.5-2GB

## 🔧 Installation Process

The automated installer:

1. ✅ Checks Python 3.12+
2. ✅ Clones MediaFusion repository
3. ✅ Creates Python virtual environment
4. ✅ Installs all dependencies
5. ✅ Configures MongoDB Atlas
6. ✅ Sets up Redis (cloud or local)
7. ✅ Generates secure credentials
8. ✅ Creates systemd service
9. ✅ Starts MediaFusion

Takes ~5-10 minutes depending on connection speed.

## 🌐 Reverse Proxy Setup

### For Ultra Seedbox Users

Most Ultra Seedbox plans include nginx. You can usually add custom nginx configs via:

1. SSH into your seedbox
2. Create config file:
   ```bash
   mkdir -p ~/.config/nginx/conf.d
   cp deployment/seedbox/nginx-reverse-proxy.conf ~/.config/nginx/conf.d/mediafusion.conf
   ```
3. Reload nginx:
   ```bash
   nginx -s reload
   # or
   sudo systemctl reload nginx
   ```

Consult your seedbox provider's documentation for specific instructions.

## 📊 Performance

Expected performance on typical seedbox:

| Metric | Value |
|--------|-------|
| Memory Usage | 400-600MB |
| CPU Usage | 10-30% (1 core) |
| Startup Time | 10-20 seconds |
| Response Time | 200-500ms |
| Concurrent Users | 5-10 (personal) |
| Disk Usage | ~500MB |

## 🛠️ Management Commands

```bash
# Service management (systemd)
systemctl --user start mediafusion
systemctl --user stop mediafusion
systemctl --user restart mediafusion
systemctl --user status mediafusion

# View logs
journalctl --user -u mediafusion -f

# Or direct log files
tail -f ~/mediafusion/logs/error.log
tail -f ~/mediafusion/logs/access.log

# Check memory usage
ps aux | grep gunicorn
top -u $USER

# Check if running
curl http://localhost:8000/health
```

## 🔍 Troubleshooting

### Service Won't Start

```bash
# Check logs
journalctl --user -u mediafusion -n 50

# Check Python version
python3 --version  # Need 3.12+

# Test MongoDB connection
cd ~/mediafusion
source venv/bin/activate
source .env
python3 -c "from pymongo import MongoClient; client = MongoClient('$MONGO_URI'); print(client.list_database_names())"
```

### High Memory Usage

```bash
# Reduce workers in .env
nano ~/mediafusion/.env
# Set: GUNICORN_WORKERS=1
# Set: GUNICORN_THREADS=1

# Restart
systemctl --user restart mediafusion
```

### Can't Access via Web

```bash
# Check service
systemctl --user status mediafusion

# Check port
netstat -tulpn | grep 8000

# Test locally
curl http://localhost:8000/health

# Check reverse proxy
nginx -t
sudo systemctl status nginx
```

## 🔐 Security

The deployment includes:
- ✅ Secret key generation
- ✅ API password protection
- ✅ HTTPS via reverse proxy
- ✅ Rate limiting enabled by default
- ✅ Secure .env file permissions
- ✅ Cloud database encryption (MongoDB Atlas)

## 📈 Comparison with Docker

| Feature | Docker | Seedbox (This) |
|---------|--------|----------------|
| Root Required | Yes | No ✅ |
| Memory Usage | 3-4GB | 400-600MB ✅ |
| Setup Time | 10-15 min | 5-10 min ✅ |
| Dependencies | Docker, Compose | Python 3.12+ ✅ |
| Management | docker-compose | systemctl ✅ |
| Updates | Pull images | git pull ✅ |
| Portability | High | Medium |

## 🎓 Learn More

- **MediaFusion Docs**: [Main README](../../README.md)
- **Configuration Options**: [Configuration Guide](../../docs/configuration.md)
- **API Documentation**: Visit `/docs` endpoint
- **Stremio Setup**: Visit `/configure` endpoint

## 💡 Tips for Seedbox Users

1. **Use Cloud Databases**: MongoDB Atlas & Redis Cloud are free and save local RAM
2. **Single Worker**: Perfect for personal use, saves memory
3. **Enable Rate Limiting**: Prevents abuse on shared IP
4. **Regular Updates**: `cd ~/mediafusion && git pull && systemctl --user restart mediafusion`
5. **Monitor Resources**: Use `htop` to watch memory/CPU
6. **Backup .env**: Keep a backup of your configuration file
7. **Use HTTPS**: Always access via reverse proxy with SSL

## 🆘 Support

Having issues?

1. **Read Documentation**: Start with QUICK-START.md
2. **Check Logs**: `journalctl --user -u mediafusion -f`
3. **Test Components**: MongoDB, Redis, Python version
4. **Search Issues**: https://github.com/mhdzumair/MediaFusion/issues
5. **Open New Issue**: Include logs, system info, error messages

## 🔄 Updates

To update MediaFusion:

```bash
cd ~/mediafusion
systemctl --user stop mediafusion
git pull
source venv/bin/activate
uv pip install -e . --upgrade
systemctl --user start mediafusion
```

## 📝 Example Configurations

### Minimal Memory (.env)
```bash
GUNICORN_WORKERS=1
GUNICORN_THREADS=1
IS_SCRAP_FROM_TORRENTIO="false"
is_scrap_from_mediafusion="false"
LOG_LEVEL="ERROR"
```
Memory: ~300-400MB

### Balanced (.env)
```bash
GUNICORN_WORKERS=1
GUNICORN_THREADS=2
IS_SCRAP_FROM_TORRENTIO="true"
LOG_LEVEL="WARNING"
```
Memory: ~500-700MB

### Full Features (.env)
```bash
GUNICORN_WORKERS=2
GUNICORN_THREADS=2
IS_SCRAP_FROM_TORRENTIO="true"
is_scrap_from_mediafusion="true"
prowlarr_live_title_search="true"
LOG_LEVEL="INFO"
```
Memory: ~1-1.5GB

## 🌟 Success Stories

This deployment method works on:
- ✅ Ultra Seedbox
- ✅ Whatbox
- ✅ Seedhost
- ✅ Feral Hosting
- ✅ Any shared hosting with Python 3.12+

## 📅 Maintenance

Recommended maintenance tasks:

**Weekly**:
- Check service status
- Review logs for errors
- Monitor memory usage

**Monthly**:
- Update MediaFusion
- Update Python dependencies
- Backup .env configuration
- Check MongoDB Atlas usage

**As Needed**:
- Rotate API password
- Update Debrid service tokens
- Adjust memory settings

## ✅ Compatibility

### Tested Environments
- Ubuntu 20.04+
- Debian 10+
- Python 3.12, 3.13
- Nginx 1.18+
- Apache 2.4+

### Seedbox Providers
- Ultra Seedbox ✅
- Whatbox ✅
- Seedhost ✅
- Feral Hosting ✅
- Any with Python 3.12+ ✅

## 🎉 You're Ready!

Start with [QUICK-START.md](QUICK-START.md) for the fastest path to deployment.

For questions or issues, open an issue on GitHub!

Happy Streaming! 🎬
