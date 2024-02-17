# MediaFusion Deployment Guide 🚀

Welcome to the deployment guide for MediaFusion! This document will help you navigate through the different deployment methods available for MediaFusion. Depending on your preference or environment constraints, you can choose between Kubernetes-based deployment or Docker Compose.

## Deployment Options 🛠️

MediaFusion supports multiple deployment strategies to cater to different infrastructure needs and preferences. You can deploy MediaFusion using:

- [Kubernetes](./k8s/README.md) (recommended for scalable and production environments)
- [Docker Compose](./docker-compose/README.md) (suitable for simple or local development environments)

Each method has its own set of instructions and configurations. Please follow the links above to access the detailed guide for each deployment strategy.

## Kubernetes Deployment 🌐

For those using Kubernetes, we provide a detailed guide for deploying MediaFusion with Minikube, which is ideal for local development and testing. The Kubernetes deployment guide includes instructions on setting up secrets, generating SSL certificates, and configuring services.

👉 [Kubernetes Deployment Guide](./k8s/README.md)

## Docker Compose Deployment 🐳

If you're looking for a quick and straightforward deployment, Docker Compose might be the right choice for you. Our Docker Compose guide outlines the steps for setting up MediaFusion on your local machine without the complexity of Kubernetes.

👉 [Docker Compose Deployment Guide](./docker-compose/README.md)

## Prerequisites 📋

Before proceeding with any deployment method, make sure you have the required tools installed on your system:

- Docker and Docker Compose for container management and orchestration.
- Kubernetes CLI (kubectl) if you are deploying with Kubernetes.
- Python 3.11 or higher, which is necessary for certain setup scripts and tools.

## Configuration 📝

Both deployment methods require you to configure environment variables that are crucial for the operation of MediaFusion. These variables include API keys, database URIs, and other sensitive information which should be kept secure.

## Support and Contributions 💡

Should you encounter any issues during deployment or have suggestions for improvement, please feel free to open an issue or pull request in our GitHub repository.

We welcome contributions and feedback to make MediaFusion better for everyone!

Happy Deploying! 🎉
