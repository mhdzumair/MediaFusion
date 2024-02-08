# Makefile

# Image tag
TAG ?= beta

# Docker image name
IMAGE_NAME = mediafusion

# Docker repository
DOCKER_REPO = mhdzumair

# Docker image with tag
DOCKER_IMAGE = $(DOCKER_REPO)/$(IMAGE_NAME):$(TAG)

.PHONY: build tag push

build:
	docker build --build-arg GIT_REV=$(TAG) -t $(IMAGE_NAME):$(TAG) -f deployment/Dockerfile .

tag:
	docker tag $(IMAGE_NAME):$(TAG) $(DOCKER_IMAGE)

push:
	docker push $(DOCKER_IMAGE)

all: build tag push