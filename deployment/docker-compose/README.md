# MediaFusion Local Deployment Guide For Docker Compose 🐳

This guide outlines the steps for deploying MediaFusion locally using Docker Compose. It is an alternative to Kubernetes-based deployment for users who prefer a simpler setup or have constraints running Kubernetes on their machines.

### Local Deployment Video Tutorial 📹
[![Watch the video](https://video.elfhosted.com/w/rgRFCmdgWW2HDES4QSD6Kb)](https://video.elfhosted.com/w/rgRFCmdgWW2HDES4QSD6Kb)

## Clone the Repository 📋

```bash
git clone https://github.com/mhdzumair/MediaFusion
cd MediaFusion

# goto deployment/docker-compose
cd deployment/docker-compose
```

## Prerequisites 🛠️

Ensure the following tools are installed:

- **Docker**: For containerization. [Installation guide](https://docs.docker.com/get-docker/).
- **Docker Compose**: For multi-container Docker applications. [Installation guide](https://docs.docker.com/compose/install/).
- **mkcert**: For generating self-signed SSL certificates. [Installation guide](https://github.com/FiloSottile/mkcert?tab=readme-ov-file#installation).

## Database Options 📊

MediaFusion supports both MongoDB and PostgreSQL. The deployment includes both databases during the migration period:

- **MongoDB**: Legacy database (kept for migration compatibility)
- **PostgreSQL**: New primary database with better performance and replica support

### Available Compose Files

| File | Description |
|------|-------------|
| `docker-compose.yml` | Full deployment with single PostgreSQL instance |
| `docker-compose-minimal.yml` | Minimal setup (databases only) for development |
| `docker-compose-postgres-ha.yml` | PostgreSQL High Availability with read replicas |

## Configuration 📝

Rename `.env-sample` to `.env` and update the variables.

> [!TIP]
> For more configuration options, refer to the [Configuration](/docs/configuration.md) documentation.


### Linux/macOS

```bash
cp .env-sample .env
# Generate and update SECRET_KEY in the .env file
echo SECRET_KEY=$(openssl rand -hex 16) >> .env

# Set API Password to secure the API endpoints
echo API_PASSWORD=your_password >> .env

# Optional:
# Update .env with your Premiumize credentials if available
# You can obtain OAuth credentials from the https://www.premiumize.me/registerclient with free user account.
echo PREMIUMIZE_OAUTH_CLIENT_ID=your_client_id >> .env
echo PREMIUMIZE_OAUTH_CLIENT_SECRET=your_client_secret >> .env

# Open the .env file to verify the values
nano .env
```

### Windows

```powershell
# Copy .env-sample to .env
Copy-Item .env-sample .env

# Generate and update SECRET_KEY in the .env file
$guid = [System.Guid]::NewGuid().ToString("N").Substring(0, 32)
Add-Content -Path .env -Value "SECRET_KEY=$guid"

# Set API Password to secure the API endpoints
Add-Content -Path .env -Value "API_PASSWORD=your_password"

# Optional:
# Update .env with your Premiumize credentials if available
# You can obtain OAuth credentials from the https://www.premiumize.me/registerclient with free user account.
$clientId = 'your_client_id'
$clientSecret = 'your_client_secret'
Add-Content -Path .env -Value "PREMIUMIZE_OAUTH_CLIENT_ID=$clientId"
Add-Content -Path .env -Value "PREMIUMIZE_OAUTH_CLIENT_SECRET=$clientSecret"

# Open the .env file to verify the values
notepad.exe .env
```

## Generate Self-Signed SSL Certificate 🔐

Generate a self-signed SSL certificate for local HTTPS:

```bash
mkcert -install
mkcert "mediafusion.local"
```
> [!TIP]
> If you are using WSL to setup MediaFusion, You also need to run `mkcert -install` in Windows PowerShell to install the root certificate.

## Prowlarr Configuration 🔄

To configure Prowlarr, Run the script based on your OS:
> [!WARNING]
> This script will clean up the existing configuration in Prowlarr and add the new configuration.

### Linux/macOS

```bash
export FLARESOLVERR_HOST=http://flaresolverr:8191
./setup-prowlarr.sh
```

### Windows

```powershell
$env:FLARESOLVERR_HOST = "http://flaresolverr:8191"
.\setup-prowlarr.ps1
```

> [!TIP]
> This script will setup Prowlarr API key to the `.env` file, Add tested Public trackers and flaresolverr configuration in Prowlarr. 
> Additionally, You can also add or modify your own trackers and other configuration in Prowlarr by visiting the prowlarr web interface http://localhost:9696.

## Deployment 🚢

### Standard Deployment (Single PostgreSQL Instance)

Deploy MediaFusion using Docker Compose:

```bash
docker compose -f docker-compose.yml up -d
```

### High Availability Deployment (PostgreSQL with Read Replicas)

For production environments with high read workloads, use the HA configuration:

```bash
docker compose -f docker-compose.yml -f docker-compose-postgres-ha.yml up -d
```

This setup includes:
- PostgreSQL primary (read-write)
- PostgreSQL read replica (read-only)
- PgBouncer connection pooling for both primary and replica

### Development Setup (Databases Only)

For local development where you run the app separately:

```bash
docker compose -f docker-compose-minimal.yml up -d
```

> [!WARNING]
> Note: If you have lower than armv8-2 architecture, you may not be able to run the mongodb container. In that case, you can use MongoDB Atlas Cluster. 

## Database Configuration 🗄️

### PostgreSQL Configuration

The default PostgreSQL credentials are:
- **User**: `mediafusion`
- **Password**: `mediafusion`
- **Database**: `mediafusion`

To use custom credentials, update your `.env` file:

```dotenv
# Single PostgreSQL instance
POSTGRES_URI=postgresql+asyncpg://user:password@postgres:5432/mediafusion

# With read replica (optional)
POSTGRES_READ_URI=postgresql+asyncpg://user:password@postgres-replica:5432/mediafusion
```

### Configuring MongoDB Atlas Cluster (Optional) 🌐

If you want to use MongoDB Atlas Cluster instead of local MongoDB, follow the documentation [here](/deployment/mongo/README.md).

- Replace the `MONGO_URI` in the `.env` file with the connection string you copied from the previous step.
- Make sure to add the Database name in the connection string. Example Database name is `mediafusion`.
```dotenv
MONGO_URI=mongodb+srv://<username>:<password>@<cluster-url>/<database-name>?retryWrites=true&w=majority
```
- Remove the `mongodb` container and `depends_on` from the `docker-compose.yml` file.

### Managed PostgreSQL Services (Recommended for Production)

For production deployments, consider using managed PostgreSQL services:
- **AWS RDS for PostgreSQL**
- **Google Cloud SQL**
- **Azure Database for PostgreSQL**
- **DigitalOcean Managed Databases**
- **Supabase**

Update your `.env` file with the connection strings from your managed service.


## Accessing MediaFusion 🌍

Update your system's hosts file to resolve `mediafusion.local` to `127.0.0.1`:

### Windows

Open PowerShell as Administrator:

```powershell
Add-Content -Path C:\Windows\System32\drivers\etc\hosts -Value "127.0.0.1 mediafusion.local"
```

### Linux/macOS

```bash
echo "127.0.0.1 mediafusion.local" | sudo tee -a /etc/hosts
```

Now, access MediaFusion at [https://mediafusion.local](https://mediafusion.local) 🎉

> [!TIP]
> When you first access MediaFusion, scraped results may not be immediately available until background scheduled tasks are completed.
> You can manually trigger these tasks by visiting the scraper control interface at [https://mediafusion.local/scraper](https://mediafusion.local/scraper).

## Updating MediaFusion 🔄

To update MediaFusion, pull the latest changes from the repository and restart the containers:

```bash
git pull
docker compose -f docker-compose.yml down
docker compose -f docker-compose.yml pull
docker compose -f docker-compose.yml up -d
```

## Stopping MediaFusion 🛑

To stop MediaFusion, run the following command:

```bash
docker compose -f docker-compose.yml down
```

## Resetting MediaFusion 🔄

To reset MediaFusion, remove the containers and volumes:

```bash
docker compose -f docker-compose.yml down -v
```

## Troubleshooting 🛠️

- If you encounter any issues during the deployment, check the logs for the respective service using `docker compose -f docker-compose.yml logs <service-name>`.
- If you encounter any issues with the web interface, ensure that the SSL certificate is installed correctly.

## Feedback 📢

If you have any feedback, please feel free to open an issue or submit a pull request. 🙏
