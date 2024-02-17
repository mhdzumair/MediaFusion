# MediaFusion Local Deployment Guide For Docker Compose 🐳

This guide outlines the steps for deploying MediaFusion locally using Docker Compose. It is an alternative to Kubernetes-based deployment for users who prefer a simpler setup or have constraints running Kubernetes on their machines.

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
- **Python 3.11**: For mkcert & development. [Installation guide](https://www.python.org/downloads/).

## Configuration 📝

Rename `.env-sample` to `.env` and update the variables.


```bash
cp .env-sample .env
# Generate and update SECRET_KEY in the .env file
echo SECRET_KEY=$(openssl rand -hex 16) >> .env

# Update .env with your Premiumize credentials if available
echo PREMIUMIZE_OAUTH_CLIENT_ID=your_client_id >> .env
echo PREMIUMIZE_OAUTH_CLIENT_SECRET=your_client_secret >> .env

# Open the .env file to verify the values
nano .env
```

## Generate Self-Signed SSL Certificate 🔐

Generate a self-signed SSL certificate for local HTTPS:

```bash
pip install mkcert
mkcert -install
mkcert "mediafusion.local"
```

## Prowlarr Configuration 🔄

Configure Prowlarr manually to retrieve the API token and set up indexers.

1. Start Prowlarr container:
    ```bash
    docker-compose -f docker-compose.yml up prowlarr
    ```
2. Retrieve the Prowlarr API token from the settings page at [http://127.0.0.1:9696/settings/general](http://127.0.0.1:9696/settings/general) and update the `.env` file.
3. Configure indexers like TheRARBG, Torlock, etc., through Prowlarr's UI. or alternatively, you can use the following command to add indexers:
   ```bash
   # Open a new terminal window and run the following commands
   # Replace YOUR_PROWLARR_API_KEY with the API token obtained from Prowlarr  
   export PROWLARR_API_KEY="YOUR_PROWLARR_API_KEY"  
   until curl -o prowlarr-indexers.json https://raw.githubusercontent.com/mhdzumair/MediaFusion/main/resources/json/prowlarr-indexers.json; do
     echo "Failed to download indexers file. Retrying...";
     sleep 3;
   done;
   jq -c '.[]' prowlarr-indexers.json | while read indexer; do
     echo "Adding indexer named: $(echo $indexer | jq -r '.name')";
     curl -H "Content-Type: application/json" -H "X-API-KEY: $PROWLARR_API_KEY" -X POST http://localhost:9696/api/v1/indexer -d "$indexer";
   done;
   echo "Indexers setup complete.";
   ```
4. Stop the Prowlarr container by pressing `Ctrl+C` in the terminal window where it was started.

## Deployment 🚢

Deploy MediaFusion using Docker Compose:

```bash
docker-compose -f docker-compose.yml up -d
```

> Note: If you have lower than armv8-2 architecture, you may not be able to run the mongodb container. In that case, you can use MongoDB Atlas Cluster. 

### Configuring MongoDB Atlas Cluster (Optional) (Not needed for local deployment) 🌐
If you want to use MongoDB atlas Cluster instead of local MongoDB, follow the documentation [here](/deployment/mongo/README.md).

- Replace the `MONGO_URI` in the `.env` file with the connection string you copied from the previous step.
- Remove the `mongodb` container and `depends_on` from the `docker-compose.yml` file.


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
