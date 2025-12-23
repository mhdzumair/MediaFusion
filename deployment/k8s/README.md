# MediaFusion Local Deployment Guide For Kubernetes 🚀

This guide provides instructions for deploying MediaFusion locally using Minikube, tailored for Windows, Linux, and macOS platforms. Follow these steps to set up MediaFusion on your local machine.

## Clone the Repository

```bash
git clone https://github.com/mhdzumair/MediaFusion
cd MediaFusion
```

## Prerequisites

Before you begin, ensure the following tools are installed on your system:

- **Minikube**: For local Kubernetes development. Install instructions are available in the [Minikube documentation](https://minikube.sigs.k8s.io/docs/start/).

- **kubectl**: The Kubernetes command-line tool. Installation guidelines can be found in the [Kubernetes documentation](https://kubernetes.io/docs/tasks/tools/install-kubectl/).

- **mkcert**: For generating self-signed SSL certificates. [Installation guide](https://github.com/FiloSottile/mkcert?tab=readme-ov-file#installation).

## Database Options 📊

MediaFusion supports both MongoDB and PostgreSQL:

| File | Description |
|------|-------------|
| `local-deployment.yaml` | Standard deployment with single PostgreSQL |
| `postgres-ha-deployment.yaml` | PostgreSQL HA with primary and read replicas |

For production, consider using managed PostgreSQL services (AWS RDS, CloudSQL, etc.) or Kubernetes operators like CloudNative-PG.

## Setting Up Secrets 🗝️

MediaFusion requires certain secrets for operation. Use the following commands to create them:

```bash
# Generate a random 32-character string for the SECRET_KEY
SECRET_KEY=$(openssl rand -hex 16)

# Generate a random API key for Prowlarr
PROWLARR_API_KEY=$(openssl rand -hex 16)

# Set a password to secure the API endpoints
API_PASSWORD="your_password"

# If using Premiumize, fill in your OAuth client ID and secret. Otherwise, leave these empty.
# You can obtain OAuth credentials from the https://www.premiumize.me/registerclient with free user account.
PREMIUMIZE_OAUTH_CLIENT_ID=""
PREMIUMIZE_OAUTH_CLIENT_SECRET=""

kubectl create secret generic mediafusion-secrets \
    --from-literal=SECRET_KEY=$SECRET_KEY \
    --from-literal=API_PASSWORD=$API_PASSWORD \
    --from-literal=PROWLARR_API_KEY=$PROWLARR_API_KEY \
    --from-literal=PREMIUMIZE_OAUTH_CLIENT_ID=$PREMIUMIZE_OAUTH_CLIENT_ID \
    --from-literal=PREMIUMIZE_OAUTH_CLIENT_SECRET=$PREMIUMIZE_OAUTH_CLIENT_SECRET
```

### PostgreSQL Secrets

Create secrets for PostgreSQL:

```bash
# PostgreSQL credentials
POSTGRES_USER="mediafusion"
POSTGRES_PASSWORD=$(openssl rand -hex 16)

kubectl create secret generic postgres-secrets \
    --from-literal=POSTGRES_USER=$POSTGRES_USER \
    --from-literal=POSTGRES_PASSWORD=$POSTGRES_PASSWORD \
    --from-literal=REPLICATION_PASSWORD=$(openssl rand -hex 16)

# PostgreSQL connection URIs for MediaFusion
kubectl create secret generic mediafusion-secrets \
    --from-literal=SECRET_KEY=$SECRET_KEY \
    --from-literal=API_PASSWORD=$API_PASSWORD \
    --from-literal=PROWLARR_API_KEY=$PROWLARR_API_KEY \
    --from-literal=PREMIUMIZE_OAUTH_CLIENT_ID=$PREMIUMIZE_OAUTH_CLIENT_ID \
    --from-literal=PREMIUMIZE_OAUTH_CLIENT_SECRET=$PREMIUMIZE_OAUTH_CLIENT_SECRET \
    --from-literal=POSTGRES_URI="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres-service:5432/mediafusion" \
    --from-literal=POSTGRES_READ_URI="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres-replica:5432/mediafusion"
```

### Updating Secrets

To update existing secrets, use the following command:

```bash
kubectl create secret generic mediafusion-secrets \
    --from-literal=SECRET_KEY=$SECRET_KEY \
    --from-literal=API_PASSWORD=$API_PASSWORD \
    --from-literal=PROWLARR_API_KEY=$PROWLARR_API_KEY \
    --from-literal=PREMIUMIZE_OAUTH_CLIENT_ID=$PREMIUMIZE_OAUTH_CLIENT_ID \
    --from-literal=PREMIUMIZE_OAUTH_CLIENT_SECRET=$PREMIUMIZE_OAUTH_CLIENT_SECRET \
    --from-literal=POSTGRES_URI="$POSTGRES_URI" \
    --from-literal=POSTGRES_READ_URI="$POSTGRES_READ_URI" \
    --dry-run=client -o yaml | kubectl apply -f -
```

## Install Required Addons

### Ingress 🌐

```bash
minikube addons enable ingress
```

### Metrics Server 📊

Required for Horizontal Pod Autoscaler (HPA) functionality:

```bash
minikube addons enable metrics-server
```

## Create SSL Certificate for Ingress 🔒

Generate and store a self-signed certificate:

```bash
mkcert -install
mkcert "mediafusion.local"

kubectl create secret tls mediafusion-tls \
    --cert=mediafusion.local.pem \
    --key=mediafusion.local-key.pem
```

## Configuring MediaFusion 🛠️

> [!TIP]
> For more configuration options, refer to the [Configuration](/docs/configuration.md) documentation.

Edit the `deployment/local-deployment.yaml` to set the required environment variables:

```yaml
          - name: HOST_URL
            value: "https://mediafusion.local"
          - name: ENABLE_TAMILMV_SEARCH_SCRAPER
            value: "false"
          - name: PROWLARR_IMMEDIATE_MAX_PROCESS
            value: "3"
          - name: PROWLARR_SEARCH_INTERVAL_HOUR
            value: "24"
          - name: IS_SCRAP_FROM_TORRENTIO
            value: "false"
```

> [!WARNING]
> Note: If you have lower than armv8-2 architecture, you may not be able to run the mongodb container. In that case, you can use MongoDB Atlas Cluster. 


### Configuring MongoDB Atlas Cluster (Optional) 🌐
If you want to use MongoDB Atlas Cluster instead of local MongoDB, follow the documentation [here](/deployment/mongo/README.md).

- Replace the `MONGO_URI` in the `deployment/k8s/local-deployment.yaml` file with the connection string you copied from the previous step.
- Make sure to add the Database name in the connection string. Example Database name is `mediafusion`.
```yaml
          - name: MONGO_URI
            value: "mongodb+srv://<username>:<password>@<cluster-url>/<database-name>?retryWrites=true&w=majority"
```
- Set `mongodb-deployment` replica to 0 in the `deployment/k8s/local-deployment.yaml` file.

### Configuring Managed PostgreSQL (Recommended for Production) 🌐

For production deployments, use managed PostgreSQL services:
- **AWS RDS for PostgreSQL**
- **Google Cloud SQL**
- **Azure Database for PostgreSQL**

Update the secrets with your managed PostgreSQL connection strings:

```bash
kubectl create secret generic mediafusion-secrets \
    --from-literal=POSTGRES_URI="postgresql+asyncpg://user:password@your-rds-endpoint:5432/mediafusion" \
    --from-literal=POSTGRES_READ_URI="postgresql+asyncpg://user:password@your-rds-read-replica:5432/mediafusion" \
    # ... other secrets
    --dry-run=client -o yaml | kubectl apply -f -
```

## Deployment 🚢

### Standard Deployment (Single PostgreSQL)

Deploy MediaFusion to your local Kubernetes cluster:

```bash
kubectl apply -f deployment/k8s/local-deployment.yaml
```

### High Availability Deployment (PostgreSQL with Read Replicas)

For production environments:

```bash
# First apply PostgreSQL HA setup
kubectl apply -f deployment/k8s/postgres-ha-deployment.yaml

# Then apply main deployment
kubectl apply -f deployment/k8s/local-deployment.yaml
```

## Accessing MediaFusion 🌍

Add an entry to your system's hosts file to resolve `mediafusion.local` to the Minikube IP:

### Windows

Open PowerShell as Administrator:

```powershell
Add-Content -Path C:\Windows\System32\drivers\etc\hosts -Value "$(minikube ip) mediafusion.local"
```

### Linux/macOS

```bash
echo "$(minikube ip) mediafusion.local" | sudo tee -a /etc/hosts
```

Now, you can access MediaFusion at [https://mediafusion.local](https://mediafusion.local) 🎉

> [!TIP]
> When you first access MediaFusion, scraped results may not be immediately available until background scheduled tasks are completed.
> You can manually trigger these tasks by visiting the scraper control interface at [https://mediafusion.local/scraper](https://mediafusion.local/scraper).
