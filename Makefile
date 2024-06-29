# Makefile

# Image version
VERSION ?= latest

# Last commit ID
GIT_REV ?= $(shell git rev-parse --short HEAD)

# Builder name
BUILDER_NAME ?= mediafusion-builder

# Docker image name
IMAGE_NAME = mediafusion

# Docker repository
DOCKER_REPO = mhdzumair

# Platforms to build for
PLATFORMS = linux/amd64,linux/arm64

# Docker image with version as tag
DOCKER_IMAGE = $(DOCKER_REPO)/$(IMAGE_NAME):$(VERSION)

# Proxy settings
HTTP_PROXY = http://172.17.0.1:1081
HTTPS_PROXY = http://172.17.0.1:1081

# Variables to hold version tags and contributor names
VERSION_OLD ?=
VERSION_NEW ?=
CONTRIBUTORS ?=

.PHONY: build tag push prompt

build:
	docker build --build-arg GIT_REV=$(GIT_REV) -t $(DOCKER_IMAGE) -f deployment/Dockerfile .

build-multi:
	@if ! docker buildx ls | grep -q $(BUILDER_NAME); then \
		echo "Creating new builder $(BUILDER_NAME)"; \
		docker buildx create --name $(BUILDER_NAME) --use --driver-opt env.http_proxy=${HTTP_PROXY} --driver-opt env.https_proxy=${HTTPS_PROXY}; \
	else \
		echo "Using existing builder $(BUILDER_NAME)"; \
		docker buildx use $(BUILDER_NAME); \
	fi
	docker buildx inspect --bootstrap
	docker buildx build --platform $(PLATFORMS) --build-arg GIT_REV=$(GIT_REV) -t $(DOCKER_IMAGE) -f deployment/Dockerfile . --push
	if [ "$(VERSION)" != "beta" ]; then \
		docker buildx build --platform $(PLATFORMS) --build-arg GIT_REV=$(GIT_REV) -t $(DOCKER_REPO)/$(IMAGE_NAME):latest -f deployment/Dockerfile . --push; \
	fi
push:
	docker push $(DOCKER_IMAGE)

prompt:
# Check if necessary variables are set and generate prompt
ifndef VERSION_OLD
	@echo "Error: VERSION_OLD is not set. Please set it like: make prompt VERSION_OLD=x.x.x VERSION_NEW=y.y.y CONTRIBUTORS='@user1, @user2'"
	@exit 1
endif
ifndef VERSION_NEW
	@echo "Error: VERSION_NEW is not set. Please set it like: make prompt VERSION_OLD=x.x.x VERSION_NEW=y.y.y CONTRIBUTORS='@user1, @user2'"
	@exit 1
endif
ifndef CONTRIBUTORS
	@echo "Warning: CONTRIBUTORS not set. Continuing without contributors."
endif

	@echo "Generate a release note for MediaFusion $(VERSION_NEW) by analyzing the following changes. Organize the release note by importance rather than by commit order. highlight the most significant updates first, and streamline the content to focus on what adds the most value to the user. Ensure to dynamically create sections for New Features & Enhancements, Bug Fixes, and Documentation updates only if relevant based on the types of changes listed. Use emojis relevantly at the start of each item to enhance readability and engagement. Keep the format straightforward & shorter, List down the contributors, and provide a direct link to the detailed list of changes:\n"
	@echo "## 🚀 MediaFusion $(VERSION_NEW) Released\n"
	@echo "### Commit Messages:\n"
	@echo "$$(git log --pretty=format:'- %s' $(VERSION_OLD)..$(VERSION_NEW))\n"
	@echo "### 🤝 Contributors: $(CONTRIBUTORS)\n"
	@echo "### 📄 Full Changelog:\n- https://github.com/mhdzumair/MediaFusion/compare/$(VERSION_OLD)...$(VERSION_NEW)"


all: build-multi
