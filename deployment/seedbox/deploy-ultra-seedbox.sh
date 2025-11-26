#!/bin/bash
set -e

echo "=========================================="
echo "MediaFusion Ultra Seedbox Deployment"
echo "No Docker - User Space Installation"
echo "=========================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check Python version
echo "🐍 Checking Python version..."
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}❌ Python 3 is not installed${NC}"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
echo -e "${GREEN}✓${NC} Python $PYTHON_VERSION found"

if ! python3 -c 'import sys; exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
    echo -e "${YELLOW}⚠️  Python 3.12+ recommended. Current: $PYTHON_VERSION${NC}"
    echo "Continuing anyway, but some features may not work optimally..."
fi

# Set installation directory
INSTALL_DIR="$HOME/mediafusion"
echo ""
echo "📁 Installation directory: $INSTALL_DIR"

# Check if already exists
if [ -d "$INSTALL_DIR" ]; then
    echo -e "${YELLOW}⚠️  MediaFusion directory already exists${NC}"
    read -p "Do you want to reinstall? (y/N): " REINSTALL
    if [[ ! $REINSTALL =~ ^[Yy]$ ]]; then
        echo "Exiting..."
        exit 0
    fi
    echo "Backing up existing installation..."
    mv "$INSTALL_DIR" "$INSTALL_DIR.backup.$(date +%s)"
fi

# Clone repository
echo ""
echo "📥 Cloning MediaFusion repository..."
git clone https://github.com/mhdzumair/MediaFusion "$INSTALL_DIR"
cd "$INSTALL_DIR"

# Create virtual environment
echo ""
echo "🔧 Creating Python virtual environment..."
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate

# Upgrade pip
echo ""
echo "📦 Upgrading pip..."
pip install --upgrade pip

# Install uv for faster dependency installation
echo ""
echo "⚡ Installing uv package manager..."
pip install uv

# Install dependencies using uv
echo ""
echo "📦 Installing MediaFusion dependencies (this may take a few minutes)..."
uv pip install -e .

# Create necessary directories
echo ""
echo "📁 Creating necessary directories..."
mkdir -p "$INSTALL_DIR/logs"
mkdir -p "$INSTALL_DIR/data"

# Configuration
echo ""
echo "=========================================="
echo "📝 Configuration Setup"
echo "=========================================="
echo ""

# Get user inputs
read -p "Enter your seedbox domain (e.g., username.seedbox.io): " SEEDBOX_DOMAIN
read -p "Enter the port you want to use (e.g., 8000): " MEDIAFUSION_PORT
MEDIAFUSION_PORT=${MEDIAFUSION_PORT:-8000}

# MongoDB Atlas setup
echo ""
echo "MongoDB Atlas Setup (Required - Free Tier Available)"
echo "---------------------------------------------------"
echo "MediaFusion requires MongoDB. Since Docker is not available,"
echo "we'll use MongoDB Atlas (free cloud database)."
echo ""
echo "Steps to set up MongoDB Atlas:"
echo "1. Go to https://www.mongodb.com/cloud/atlas"
echo "2. Sign up for a free account"
echo "3. Create a free cluster (M0 - 512MB RAM)"
echo "4. Create a database user"
echo "5. Whitelist all IPs (0.0.0.0/0) or your seedbox IP"
echo "6. Get your connection string"
echo ""
read -p "Enter your MongoDB Atlas connection string: " MONGO_URI

if [ -z "$MONGO_URI" ]; then
    echo -e "${RED}❌ MongoDB URI is required${NC}"
    exit 1
fi

# Ensure database name is in the URI
if [[ ! $MONGO_URI == *"/mediafusion"* ]]; then
    # Add database name if not present
    MONGO_URI="${MONGO_URI%\?*}/mediafusion?${MONGO_URI##*\?}"
fi

# Redis setup
echo ""
echo "Redis Setup Options"
echo "-------------------"
echo "1. Use Redis Cloud (free tier - recommended)"
echo "2. Use Upstash Redis (free tier - serverless)"
echo "3. Install Redis locally in user space"
echo ""
read -p "Choose option (1-3) [default: 1]: " REDIS_OPTION
REDIS_OPTION=${REDIS_OPTION:-1}

case $REDIS_OPTION in
    1)
        echo ""
        echo "Redis Cloud Setup:"
        echo "1. Go to https://redis.com/try-free/"
        echo "2. Create a free account"
        echo "3. Create a free database (30MB)"
        echo "4. Get your Redis connection URL"
        echo ""
        read -p "Enter Redis Cloud URL (redis://default:password@host:port): " REDIS_URL
        ;;
    2)
        echo ""
        echo "Upstash Redis Setup:"
        echo "1. Go to https://upstash.com/"
        echo "2. Create a free account"
        echo "3. Create a Redis database"
        echo "4. Get your Redis connection URL"
        echo ""
        read -p "Enter Upstash Redis URL: " REDIS_URL
        ;;
    3)
        echo ""
        echo "Installing Redis locally..."
        bash "$INSTALL_DIR/deployment/seedbox/install-redis.sh"
        REDIS_URL="redis://localhost:6379"
        ;;
esac

if [ -z "$REDIS_URL" ]; then
    echo -e "${RED}❌ Redis URL is required${NC}"
    exit 1
fi

# Generate secret key
SECRET_KEY=$(openssl rand -hex 32)

# API Password
read -s -p "Enter API Password for MediaFusion: " API_PASSWORD
echo ""

if [ -z "$API_PASSWORD" ]; then
    echo -e "${RED}❌ API Password is required${NC}"
    exit 1
fi

# Create .env file
echo ""
echo "📝 Creating configuration file..."
cat > "$INSTALL_DIR/.env" <<EOF
# MediaFusion Configuration for Ultra Seedbox
# Generated on $(date)

# Server Configuration
HOST_URL="https://${SEEDBOX_DOMAIN}/mediafusion"
POSTER_HOST_URL="https://${SEEDBOX_DOMAIN}/mediafusion"

# Database Configuration
MONGO_URI="${MONGO_URI}"
REDIS_URL="${REDIS_URL}"

# Security
SECRET_KEY="${SECRET_KEY}"
API_PASSWORD="${API_PASSWORD}"

# Performance Settings (Optimized for Seedbox)
GUNICORN_WORKERS=1
GUNICORN_THREADS=2

# Logging
LOG_LEVEL="WARNING"

# Disable resource-intensive features
IS_SCRAP_FROM_TORRENTIO="false"
is_scrap_from_mediafusion="false"
is_scrap_from_zilean="false"
prowlarr_live_title_search="false"

# Rate limiting
ENABLE_RATE_LIMIT="true"

# Cache settings
store_stremthru_magnet_cache="false"

# Optional: Add your debrid service API keys here
# REALDEBRID_API_KEY=""
# ALLDEBRID_API_KEY=""
# PREMIUMIZE_OAUTH_CLIENT_ID=""
# PREMIUMIZE_OAUTH_CLIENT_SECRET=""
EOF

echo -e "${GREEN}✓${NC} Configuration file created at $INSTALL_DIR/.env"

# Run migrations
echo ""
echo "🔄 Running database migrations..."
source venv/bin/activate
beanie migrate -uri "$MONGO_URI" -db mediafusion -p migrations/

# Create startup script
echo ""
echo "📝 Creating startup scripts..."
cat > "$INSTALL_DIR/start-mediafusion.sh" <<'STARTSCRIPT'
#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate
source .env

# Load environment variables
export $(cat .env | grep -v '^#' | xargs)

# Start MediaFusion with optimized settings for seedbox
exec gunicorn api.main:app \
    -w ${GUNICORN_WORKERS:-1} \
    -k uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:${MEDIAFUSION_PORT:-8000} \
    --timeout 180 \
    --max-requests 200 \
    --max-requests-jitter 50 \
    --worker-tmp-dir /tmp \
    --graceful-timeout 30 \
    --keep-alive 5 \
    --access-logfile logs/access.log \
    --error-logfile logs/error.log \
    --log-level warning \
    --pid mediafusion.pid
STARTSCRIPT

chmod +x "$INSTALL_DIR/start-mediafusion.sh"

# Create stop script
cat > "$INSTALL_DIR/stop-mediafusion.sh" <<'STOPSCRIPT'
#!/bin/bash
cd "$(dirname "$0")"
if [ -f mediafusion.pid ]; then
    PID=$(cat mediafusion.pid)
    if ps -p $PID > /dev/null; then
        echo "Stopping MediaFusion (PID: $PID)..."
        kill $PID
        rm mediafusion.pid
        echo "MediaFusion stopped"
    else
        echo "Process not running, cleaning up PID file"
        rm mediafusion.pid
    fi
else
    echo "No PID file found. MediaFusion may not be running."
fi
STOPSCRIPT

chmod +x "$INSTALL_DIR/stop-mediafusion.sh"

# Create restart script
cat > "$INSTALL_DIR/restart-mediafusion.sh" <<'RESTARTSCRIPT'
#!/bin/bash
cd "$(dirname "$0")"
./stop-mediafusion.sh
sleep 2
./start-mediafusion.sh
RESTARTSCRIPT

chmod +x "$INSTALL_DIR/restart-mediafusion.sh"

# Create systemd user service
echo ""
echo "📝 Creating systemd user service..."
mkdir -p "$HOME/.config/systemd/user"

cat > "$HOME/.config/systemd/user/mediafusion.service" <<EOF
[Unit]
Description=MediaFusion Stremio Addon
After=network.target

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/start-mediafusion.sh
ExecStop=$INSTALL_DIR/stop-mediafusion.sh
Restart=on-failure
RestartSec=10
StandardOutput=append:$INSTALL_DIR/logs/output.log
StandardError=append:$INSTALL_DIR/logs/error.log

# Resource limits
MemoryMax=1G
CPUQuota=150%

[Install]
WantedBy=default.target
EOF

# Enable and start service
echo ""
echo "🚀 Starting MediaFusion service..."
systemctl --user daemon-reload
systemctl --user enable mediafusion.service
systemctl --user start mediafusion.service

# Wait a bit for service to start
sleep 5

# Check status
echo ""
echo "📊 Service Status:"
systemctl --user status mediafusion.service --no-pager

echo ""
echo "=========================================="
echo -e "${GREEN}✅ Installation Complete!${NC}"
echo "=========================================="
echo ""
echo "MediaFusion is now running on port $MEDIAFUSION_PORT"
echo ""
echo "Next Steps:"
echo "1. Configure your reverse proxy (nginx/apache) to forward /mediafusion to localhost:$MEDIAFUSION_PORT"
echo "2. Access MediaFusion at: https://${SEEDBOX_DOMAIN}/mediafusion"
echo ""
echo "Useful commands:"
echo "  Start:   systemctl --user start mediafusion"
echo "  Stop:    systemctl --user stop mediafusion"
echo "  Restart: systemctl --user restart mediafusion"
echo "  Status:  systemctl --user status mediafusion"
echo "  Logs:    journalctl --user -u mediafusion -f"
echo ""
echo "Or use the scripts in $INSTALL_DIR:"
echo "  ./start-mediafusion.sh"
echo "  ./stop-mediafusion.sh"
echo "  ./restart-mediafusion.sh"
echo ""
echo "Configuration file: $INSTALL_DIR/.env"
echo "Logs directory: $INSTALL_DIR/logs"
echo ""
echo "📖 See deployment/seedbox/ULTRA-SEEDBOX-DEPLOYMENT.md for more details"
echo ""
