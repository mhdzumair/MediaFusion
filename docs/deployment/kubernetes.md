# Kubernetes Deployment

For production environments that need horizontal scaling and high availability. This guide uses Minikube for local testing; adapt the secrets and manifests for your production cluster.

## Prerequisites

- [Minikube](https://minikube.sigs.k8s.io/docs/start/) (for local testing) or an existing cluster
- [kubectl](https://kubernetes.io/docs/tasks/tools/install-kubectl/)
- [mkcert](https://github.com/FiloSottile/mkcert#installation) (for local SSL)

## Step 1: Clone the repository

```bash
git clone https://github.com/mhdzumair/MediaFusion
cd MediaFusion
```

## Step 2: Create Kubernetes secrets

```bash
SECRET_KEY=$(openssl rand -hex 16)
API_PASSWORD="your_strong_password"
PROWLARR_API_KEY=$(openssl rand -hex 16)
POSTGRES_USER="mediafusion"
POSTGRES_PASSWORD=$(openssl rand -hex 16)

kubectl create secret generic mediafusion-secrets \
  --from-literal=SECRET_KEY=$SECRET_KEY \
  --from-literal=API_PASSWORD=$API_PASSWORD \
  --from-literal=PROWLARR_API_KEY=$PROWLARR_API_KEY \
  --from-literal=PREMIUMIZE_OAUTH_CLIENT_ID="" \
  --from-literal=PREMIUMIZE_OAUTH_CLIENT_SECRET="" \
  --from-literal=POSTGRES_URI="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres-service:5432/mediafusion" \
  --from-literal=POSTGRES_READ_URI="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres-replica:5432/mediafusion"

kubectl create secret generic postgres-secrets \
  --from-literal=POSTGRES_USER=$POSTGRES_USER \
  --from-literal=POSTGRES_PASSWORD=$POSTGRES_PASSWORD \
  --from-literal=REPLICATION_PASSWORD=$(openssl rand -hex 16)
```

## Step 3: Enable required Minikube addons

```bash
minikube addons enable ingress
minikube addons enable metrics-server
```

## Step 4: Create the TLS secret

```bash
mkcert -install
mkcert "mediafusion.local"

kubectl create secret tls mediafusion-tls \
  --cert=mediafusion.local.pem \
  --key=mediafusion.local-key.pem
```

## Step 5: Deploy

### Standard deployment

```bash
kubectl apply -f deployment/k8s/local-deployment.yaml
```

### High-availability deployment (PostgreSQL with read replicas)

```bash
kubectl apply -f deployment/k8s/postgres-ha-deployment.yaml
kubectl apply -f deployment/k8s/local-deployment.yaml
```

## Step 6: Update your hosts file

=== "Linux / macOS"

    ```bash
    echo "$(minikube ip) mediafusion.local" | sudo tee -a /etc/hosts
    ```

=== "Windows (PowerShell — run as Administrator)"

    ```powershell
    Add-Content -Path C:\Windows\System32\drivers\etc\hosts -Value "$(minikube ip) mediafusion.local"
    ```

MediaFusion is now available at [https://mediafusion.local](https://mediafusion.local).

---

## Updating secrets

To update a secret without recreating it:

```bash
kubectl create secret generic mediafusion-secrets \
  --from-literal=SECRET_KEY=$NEW_SECRET_KEY \
  # ... all other values ...
  --dry-run=client -o yaml | kubectl apply -f -
```

## Production recommendations

- Use a **managed PostgreSQL** service (AWS RDS, Google Cloud SQL, Azure Database, Supabase) instead of in-cluster Postgres.
- Use a **managed Redis** service (ElastiCache, Cloud Memorystore) for resilience.
- Use a proper ingress controller with cert-manager for TLS, not mkcert.
- Set resource requests and limits on the MediaFusion deployment.

## Manifests reference

| File | Description |
|---|---|
| `deployment/k8s/local-deployment.yaml` | Standard deployment, single PostgreSQL |
| `deployment/k8s/postgres-ha-deployment.yaml` | PostgreSQL HA with primary + read replica |
