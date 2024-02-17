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

.PHONY: build tag push

build:
	docker build --build-arg GIT_REV=$(GIT_REV) -t $(DOCKER_IMAGE) -f deployment/Dockerfile .

build-multi:
	@if ! docker buildx ls | grep -q $(BUILDER_NAME); then \
		echo "Creating new builder $(BUILDER_NAME)"; \
		docker buildx create --name $(BUILDER_NAME) --use; \
	else \
		echo "Using existing builder $(BUILDER_NAME)"; \
		docker buildx use $(BUILDER_NAME); \
	fi
	docker buildx inspect --bootstrap
	docker buildx build --platform $(PLATFORMS) --build-arg GIT_REV=$(GIT_REV) -t $(DOCKER_IMAGE) -f deployment/Dockerfile . --push
push:
	docker push $(DOCKER_IMAGE)

all: build-multi
