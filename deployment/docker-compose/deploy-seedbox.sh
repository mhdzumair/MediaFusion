#!/bin/bash
set -e

echo "=================================="
echo "MediaFusion Seedbox Deployment"
echo "=================================="
echo ""

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo "❌ Docker is not installed. Please install Docker first."
    exit 1
fi

# Check if Docker Compose is installed
if ! command -v docker compose &> /dev/null; then
    echo "❌ Docker Compose is not installed. Please install Docker Compose first."
    exit 1
fi

# Check if .env file exists
if [ ! -f .env ]; then
    echo "📝 Creating .env file from template..."
    cp .env-seedbox-sample .env

    # Generate secret key
    SECRET_KEY=$(openssl rand -hex 16)
    sed -i "s/your_secret_key_here/${SECRET_KEY}/" .env

    echo ""
    echo "⚠️  IMPORTANT: Edit the .env file with your settings!"
    echo ""
    echo "Required changes:"
    echo "1. Set HOST_URL to your seedbox IP/domain"
    echo "2. Set API_PASSWORD to a secure password"
    echo "3. Optional: Configure MongoDB Atlas if you have very limited memory"
    echo ""
    read -p "Press Enter after you've edited .env file, or Ctrl+C to cancel..."
fi

# Get seedbox IP/domain from user
echo ""
read -p "Enter your seedbox IP or domain (e.g., 192.168.1.100 or myseedbox.com): " SEEDBOX_HOST

if [ -z "$SEEDBOX_HOST" ]; then
    echo "❌ Seedbox host cannot be empty"
    exit 1
fi

# Update .env with seedbox host
sed -i "s|HOST_URL=.*|HOST_URL=\"http://${SEEDBOX_HOST}:8000\"|" .env
sed -i "s|POSTER_HOST_URL=.*|POSTER_HOST_URL=\"http://${SEEDBOX_HOST}:8000\"|" .env

echo ""
echo "🔍 Configuration Summary:"
echo "  - MediaFusion URL: http://${SEEDBOX_HOST}:8000"
echo "  - Workers: 1 (optimized for low memory)"
echo "  - Memory limit: ~1.5GB total"
echo ""

# Ask about MongoDB Atlas
echo "MongoDB Options:"
echo "1. Use local MongoDB (included in Docker Compose) - Uses ~400MB RAM"
echo "2. Use MongoDB Atlas (free cloud database) - Saves local RAM"
echo ""
read -p "Choose option (1 or 2) [default: 1]: " MONGO_OPTION
MONGO_OPTION=${MONGO_OPTION:-1}

if [ "$MONGO_OPTION" == "2" ]; then
    echo ""
    echo "To use MongoDB Atlas:"
    echo "1. Create free account at https://www.mongodb.com/cloud/atlas"
    echo "2. Create a free cluster"
    echo "3. Get your connection string"
    echo ""
    read -p "Enter MongoDB Atlas connection string: " MONGO_ATLAS_URI

    if [ ! -z "$MONGO_ATLAS_URI" ]; then
        sed -i "s|MONGO_URI=.*|MONGO_URI=\"${MONGO_ATLAS_URI}\"|" .env
        echo "✅ MongoDB Atlas configured"
        COMPOSE_SERVICES="mediafusion redis"
    else
        echo "⚠️  Empty connection string, using local MongoDB"
        COMPOSE_SERVICES=""
    fi
else
    COMPOSE_SERVICES=""
fi

echo ""
echo "🚀 Starting deployment..."
echo ""

# Pull images first
echo "📥 Pulling Docker images..."
docker compose -f docker-compose-seedbox.yml pull

# Start services
echo "🏃 Starting services..."
if [ -z "$COMPOSE_SERVICES" ]; then
    docker compose -f docker-compose-seedbox.yml up -d
else
    docker compose -f docker-compose-seedbox.yml up -d $COMPOSE_SERVICES
fi

echo ""
echo "⏳ Waiting for services to be healthy (this may take 2-3 minutes)..."
sleep 30

# Check status
echo ""
echo "📊 Service Status:"
docker compose -f docker-compose-seedbox.yml ps

echo ""
echo "💾 Memory Usage:"
docker stats --no-stream

echo ""
echo "=================================="
echo "✅ Deployment Complete!"
echo "=================================="
echo ""
echo "MediaFusion is now available at:"
echo "  🌐 http://${SEEDBOX_HOST}:8000"
echo ""
echo "Useful commands:"
echo "  📋 View logs:        docker compose -f docker-compose-seedbox.yml logs -f"
echo "  📊 Check status:     docker compose -f docker-compose-seedbox.yml ps"
echo "  💾 Memory usage:     docker stats"
echo "  🔄 Restart:          docker compose -f docker-compose-seedbox.yml restart"
echo "  🛑 Stop:             docker compose -f docker-compose-seedbox.yml down"
echo ""
echo "Next steps:"
echo "1. Visit http://${SEEDBOX_HOST}:8000/configure to set up your add-on"
echo "2. Monitor memory usage with: docker stats"
echo "3. Check logs if you encounter issues: docker compose -f docker-compose-seedbox.yml logs"
echo ""
echo "📖 Full documentation: deployment/docker-compose/SEEDBOX-DEPLOYMENT.md"
echo ""
