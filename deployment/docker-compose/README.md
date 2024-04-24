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
- **mkcert**: For generating self-signed SSL certificates. [Installation guide](https://github.com/FiloSottile/mkcert?tab=readme-ov-file#installation).

## Configuration 📝

Rename `.env-sample` to `.env` and update the variables.

> [!TIP]
> For more configuration options, refer to the [Configuration](/docs/configuration.md) documentation.


```bash
cp .env-sample .env
# Generate and update SECRET_KEY in the .env file
echo SECRET_KEY=$(openssl rand -hex 16) >> .env

# Update .env with your Premiumize credentials if available
# You can obtain OAuth credentials from the https://www.premiumize.me/registerclient with free user account.
echo PREMIUMIZE_OAUTH_CLIENT_ID=your_client_id >> .env
echo PREMIUMIZE_OAUTH_CLIENT_SECRET=your_client_secret >> .env

# Open the .env file to verify the values
nano .env
```

## Generate Self-Signed SSL Certificate 🔐

Generate a self-signed SSL certificate for local HTTPS:

```bash
mkcert -install
mkcert "mediafusion.local"
```

## Prowlarr Configuration 🔄

Configure Prowlarr manually to retrieve the API token and set up indexers.

1. Start Prowlarr container:
    ```bash
    docker compose -f docker-compose.yml up prowlarr
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
docker compose -f docker-compose.yml up -d
```

> [!WARNING]
> Note: If you have lower than armv8-2 architecture, you may not be able to run the mongodb container. In that case, you can use MongoDB Atlas Cluster. 

### Configuring MongoDB Atlas Cluster (Optional) (Not needed for local deployment) 🌐
If you want to use MongoDB atlas Cluster instead of local MongoDB, follow the documentation [here](/deployment/mongo/README.md).

- Replace the `MONGO_URI` in the `.env` file with the connection string you copied from the previous step.
- Make sure to add the Database name in the connection string. Example Database name is `mediafusion`.
```dotenv
MONGO_URI=mongodb+srv://<username>:<password>@<cluster-url>/<database-name>?retryWrites=true&w=majority
```
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

## Troubleshooting 🛠️

- If you encounter any issues during the deployment, check the logs for the respective service using `docker-compose -f docker-compose.yml logs <service-name>`.
- If you encounter any issues with the web interface, ensure that the SSL certificate is installed correctly.

## Feedback 📢

If you have any feedback, please feel free to open an issue or submit a pull request. 🙏
