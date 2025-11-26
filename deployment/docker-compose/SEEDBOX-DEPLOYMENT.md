# MediaFusion Seedbox Deployment Guide 🌱

This guide is specifically designed for deploying MediaFusion on seedbox environments (like Ultra Seedbox) where you have limited virtual memory but sufficient physical RAM (10GB+).

## 🔍 Problem Overview

Seedboxes often have restrictions on:
- **Virtual memory** (swap space) - Cannot be increased without root access
- **Memory overcommit** - Shared environments with strict limits
- **System resources** - Shared CPU and memory with other users

## ✨ Solution

This optimized deployment configuration reduces memory footprint by:

1. **Reducing Gunicorn workers** from 4 to 1-2 workers
2. **Limiting MongoDB memory** with WiredTiger cache size
3. **Constraining Redis** with maxmemory settings
4. **Using Alpine Linux** images where possible (smaller footprint)
5. **Setting Docker memory limits** to prevent overcommit
6. **Disabling memory swap** to use only physical RAM
7. **Removing optional services** (Prowlarr, FlareSolverr, Browserless, Nginx)

## 📋 Prerequisites

- Docker and Docker Compose installed on your seedbox
- At least 2GB of available RAM (3-4GB recommended)
- Port 8000 available for MediaFusion
- SSH/terminal access to your seedbox

## 🚀 Quick Start

### 1. Clone the Repository

```bash
git clone https://github.com/mhdzumair/MediaFusion
cd MediaFusion/deployment/docker-compose
```

### 2. Configure Environment

```bash
# Copy the sample environment file
cp .env-sample .env

# Generate a secret key
echo "SECRET_KEY=$(openssl rand -hex 16)" >> .env

# Set API password
echo "API_PASSWORD=your_secure_password" >> .env

# Edit the .env file to customize other settings
nano .env
```

### 3. Deploy with Seedbox Configuration

```bash
# Deploy using the seedbox-optimized docker-compose file
docker compose -f docker-compose-seedbox.yml up -d
```

### 4. Monitor Deployment

```bash
# Check service status
docker compose -f docker-compose-seedbox.yml ps

# View logs
docker compose -f docker-compose-seedbox.yml logs -f mediafusion

# Check memory usage
docker stats
```

### 5. Access MediaFusion

Your MediaFusion instance will be available at:
- `http://YOUR_SEEDBOX_IP:8000`
- Or via your seedbox domain if configured

## ⚙️ Memory Configuration Tuning

### If you still encounter memory issues:

#### Option 1: Use MongoDB Atlas (Recommended for very limited memory)

1. Create a free MongoDB Atlas account at https://www.mongodb.com/cloud/atlas
2. Create a free cluster (512MB RAM)
3. Get your connection string
4. Update `.env`:
```bash
MONGO_URI=mongodb+srv://<username>:<password>@<cluster-url>/mediafusion?retryWrites=true&w=majority
```
5. Deploy without local MongoDB:
```bash
docker compose -f docker-compose-seedbox.yml up -d mediafusion redis
```

#### Option 2: Further Reduce Memory Limits

Edit `docker-compose-seedbox.yml` and adjust:

```yaml
# For MediaFusion service
mem_limit: 768m        # Reduce from 1g to 768m
mem_reservation: 384m  # Reduce from 512m to 384m

# For MongoDB
mem_limit: 384m        # Reduce from 512m to 384m
mem_reservation: 192m  # Reduce from 256m to 192m
```

#### Option 3: Use Minimal Configuration (No Worker)

If you don't need background scraping, you can skip the dramatiq worker entirely and just run the main MediaFusion service. This saves additional memory.

## 🔧 Advanced Configuration

### Customize Worker and Thread Count

Set environment variables in `.env`:

```bash
# Reduce to single worker with single thread (lowest memory)
GUNICORN_WORKERS=1
GUNICORN_THREADS=1

# Or use 1 worker with 2 threads for better concurrency
GUNICORN_WORKERS=1
GUNICORN_THREADS=2
```

### Monitor Memory Usage

```bash
# Real-time container stats
docker stats

# Check specific container memory
docker stats mediafusion-mediafusion-1

# View detailed memory info
docker inspect mediafusion-mediafusion-1 | grep -A 10 Memory
```

## 🐛 Troubleshooting

### Container Keeps Restarting

```bash
# Check logs for OOM (Out of Memory) errors
docker compose -f docker-compose-seedbox.yml logs mediafusion | grep -i "memory\|oom"

# If OOM killed, reduce memory limits or use MongoDB Atlas
```

### Slow Performance

```bash
# Increase worker threads (but stay at 1 worker)
# In .env:
GUNICORN_WORKERS=1
GUNICORN_THREADS=4  # Increase threads instead of workers
```

### MongoDB Issues

```bash
# MongoDB is often the memory-hungry service
# Best solution: Use MongoDB Atlas (free tier)
# Or reduce WiredTiger cache in docker-compose-seedbox.yml:
command: ["mongod", "--wiredTigerCacheSizeGB", "0.25", "--nojournal"]
```

## 📊 Expected Memory Usage

With this configuration, expect approximately:

- **MediaFusion**: 400-800 MB
- **MongoDB**: 200-400 MB
- **Redis**: 50-150 MB
- **Total**: ~1-1.5 GB RAM

## 🔄 Updating MediaFusion

```bash
cd MediaFusion/deployment/docker-compose
git pull
docker compose -f docker-compose-seedbox.yml pull
docker compose -f docker-compose-seedbox.yml down
docker compose -f docker-compose-seedbox.yml up -d
```

## 🛑 Stopping MediaFusion

```bash
docker compose -f docker-compose-seedbox.yml down
```

## 🗑️ Complete Cleanup

```bash
# Remove containers and volumes
docker compose -f docker-compose-seedbox.yml down -v

# Remove images to free space
docker rmi mhdzumair/mediafusion:4.3.35 mongo:latest redis:alpine
```

## 💡 Tips for Seedbox Environments

1. **Use MongoDB Atlas** - Offloads the most memory-intensive service
2. **Monitor regularly** - Use `docker stats` to track memory usage
3. **Avoid peak hours** - Deploy during off-peak times
4. **Request limits** - Some seedbox providers can adjust limits upon request
5. **Use screen/tmux** - Keep deployment sessions alive: `screen -S mediafusion`

## 📞 Getting Help

If you continue to experience issues:

1. Check logs: `docker compose -f docker-compose-seedbox.yml logs`
2. Report memory usage: `docker stats --no-stream`
3. Share your seedbox specs (RAM, CPU, OS)
4. Open an issue: https://github.com/mhdzumair/MediaFusion/issues

## 🎯 Alternative: Direct Python Deployment (No Docker)

If Docker memory overhead is too high, you can deploy directly with Python:

```bash
# Install Python 3.12+
# Install dependencies
pip install -r requirements.txt

# Configure environment variables
export MONGO_URI="mongodb+srv://..."  # Use Atlas
export REDIS_URL="redis://localhost:6379"

# Run with minimal settings
gunicorn api.main:app -w 1 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

However, you'll still need Redis running (can be installed directly on the seedbox if available).

## ✅ Success Indicators

Your deployment is successful when:
- ✓ All containers show "healthy" status
- ✓ No OOM (Out of Memory) errors in logs
- ✓ Can access MediaFusion web interface
- ✓ Memory usage stable under 2GB total
- ✓ Containers don't restart unexpectedly

Happy Streaming! 🎬
