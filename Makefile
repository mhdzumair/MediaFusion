# Makefile

# Image version
VERSION ?= latest

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
CONTRIBUTORS ?= $(shell git log --pretty=format:'%an' $(VERSION_OLD)..$(VERSION_NEW) | sort | uniq)

# Claude API settings
CLAUDE_MODEL ?= claude-3-5-sonnet-20241022
MAX_TOKENS ?= 1024
ANTHROPIC_VERSION ?= 2023-06-01

# Reddit post settings
SUBREDDIT ?= MediaFusion
REDDIT_POST_TITLE ?= "MediaFusion $(VERSION_NEW) Update - What's New?"

.PHONY: build tag push prompt update-version generate-notes generate-reddit-post

build:
	docker build --build-arg VERSION=$(VERSION) -t $(DOCKER_IMAGE) -f deployment/Dockerfile .

update-version:
ifndef VERSION_NEW
	@echo "Error: VERSION_NEW is not set. Please set it like: make update-version VERSION_NEW=4.1.0"
	@exit 1
endif
	@echo "Updating version to $(VERSION_NEW)..."
	# Update main addon.xml
	@sed -i -e "/<addon\s*id=\"plugin\.video\.mediafusion\"/s/version=\"[^\"]*\"/version=\"$(VERSION_NEW)\"/" kodi/plugin.video.mediafusion/addon.xml
	# Update repository addon.xml
	@sed -i -e "/<addon\s*id=\"repository\.mediafusion\"/s/version=\"[^\"]*\"/version=\"$(VERSION_NEW)\"/" kodi/repository.mediafusion/addon.xml
	# Update docker-compose.yml
	@sed -i 's|image: $(DOCKER_REPO)/$(IMAGE_NAME):[0-9.]*|image: $(DOCKER_REPO)/$(IMAGE_NAME):$(VERSION_NEW)|g' deployment/docker-compose/docker-compose.yml
	@sed -i 's|image: $(DOCKER_REPO)/$(IMAGE_NAME):[0-9.]*|image: $(DOCKER_REPO)/$(IMAGE_NAME):$(VERSION_NEW)|g' deployment/docker-compose/docker-compose-minimal.yml
	# Update k8s deployment
	@sed -i 's|image: $(DOCKER_REPO)/$(IMAGE_NAME):[0-9.]*|image: $(DOCKER_REPO)/$(IMAGE_NAME):$(VERSION_NEW)|g' deployment/k8s/local-deployment.yaml
	# Update pyproject.toml
	@sed -i -e "s/version = \"[0-9.]*\"/version = \"$(VERSION_NEW)\"/" pyproject.toml
	@echo "Version updated to $(VERSION_NEW) in all files"

build-multi:
	@if ! docker buildx ls | grep -q $(BUILDER_NAME); then \
		echo "Creating new builder $(BUILDER_NAME)"; \
		docker buildx create --name $(BUILDER_NAME) --use --driver-opt env.http_proxy=${HTTP_PROXY} --driver-opt env.https_proxy=${HTTPS_PROXY}; \
	else \
		echo "Using existing builder $(BUILDER_NAME)"; \
		docker buildx use $(BUILDER_NAME); \
	fi
	docker buildx inspect --bootstrap
	docker buildx build --platform $(PLATFORMS) --build-arg VERSION=$(VERSION) -t $(DOCKER_IMAGE) -f deployment/Dockerfile . --push
	if [ "$(VERSION)" != "beta" ]; then \
		docker buildx build --platform $(PLATFORMS) --build-arg VERSION=$(VERSION) -t $(DOCKER_REPO)/$(IMAGE_NAME):latest -f deployment/Dockerfile . --push; \
	fi

push:
	docker push $(DOCKER_IMAGE)

prompt:
ifndef VERSION_OLD
	@echo "Error: VERSION_OLD is not set. Please set it like: make prompt VERSION_OLD=x.x.x VERSION_NEW=y.y.y CONTRIBUTORS='@user1, @user2'"
	@exit 1
endif
ifndef VERSION_NEW
	@echo "Error: VERSION_NEW is not set. Please set it like: make prompt VERSION_OLD=x.x.x VERSION_NEW=y.y.y CONTRIBUTORS='@user1, @user2'"
	@exit 1
endif
	@echo "Generate a release note for MediaFusion $(VERSION_NEW) by analyzing the following changes. Organize the release note by importance rather than by commit order. highlight the most significant updates first, and streamline the content to focus on what adds the most value to the user. Ensure to dynamically create sections for New Features & Enhancements, Bug Fixes, and Documentation updates only if relevant based on the types of changes listed. Use emojis relevantly at the start of each item to enhance readability and engagement. Keep the format straightforward & shorter, provide a direct link to the detailed list of changes:\n"
	@echo "## 🚀 MediaFusion $(VERSION_NEW) Released\n"
	@echo "### Commit Messages and Descriptions:\n"
	@git log --pretty=format:'%s%n%b' $(VERSION_OLD)..$(VERSION_NEW) | awk 'BEGIN {RS="\n\n"; FS="\n"} { \
		message = $$1; \
		description = ""; \
		for (i=2; i<=NF; i++) { \
			if ($$i ~ /^\*/) description = description "  " $$i "\n"; \
			else if ($$i != "") description = description "  " $$i "\n"; \
		} \
		if (message != "") print "- " message; \
		if (description != "") printf "%s", description; \
	}'
	@echo "--- \n### 🤝 Contributors: $(CONTRIBUTORS)\n\n### 📄 Full Changelog:\nhttps://github.com/mhdzumair/MediaFusion/compare/$(VERSION_OLD)...$(VERSION_NEW)";

prompt-reddit:
ifndef VERSION_OLD
	@echo "Error: VERSION_OLD is not set. Please set it like: make prompt-reddit VERSION_OLD=x.x.x VERSION_NEW=y.y.y"
	@exit 1
endif
ifndef VERSION_NEW
	@echo "Error: VERSION_NEW is not set. Please set it like: make prompt-reddit VERSION_OLD=x.x.x VERSION_NEW=y.y.y"
	@exit 1
endif
	@echo "Create an engaging Reddit post about the MediaFusion $(VERSION_NEW) update. Focus on the user experience and benefits. The post should be informative yet conversational, perfect for the r/$(SUBREDDIT) community. Analyze these changes and create sections for major updates, improvements, and fixes. Include a TL;DR at the top for quick scanning. Add a brief note about installation/updating at the bottom. Here are the changes to analyze:\n"
	@echo "---\n"
	@git log --pretty=format:'%s%n%b' $(VERSION_OLD)..$(VERSION_NEW) | awk 'BEGIN {RS="\n\n"; FS="\n"} { \
		message = $$1; \
		description = ""; \
		for (i=2; i<=NF; i++) { \
			if ($$i ~ /^\*/) description = description "  " $$i "\n"; \
			else if ($$i != "") description = description "  " $$i "\n"; \
		} \
		if (message != "") print "- " message; \
		if (description != "") printf "%s", description; \
	}'
	@echo "\n---\nFor the complete changelog, visit: https://github.com/mhdzumair/MediaFusion/compare/$(VERSION_OLD)...$(VERSION_NEW)"

generate-notes:
ifndef VERSION_OLD
	@echo "Error: VERSION_OLD is not set"
	@exit 1
endif
ifndef VERSION_NEW
	@echo "Error: VERSION_NEW is not set"
	@exit 1
endif
ifndef ANTHROPIC_API_KEY
	@echo "Error: ANTHROPIC_API_KEY is not set"
	@exit 1
endif
	@PROMPT_CONTENT=$$(make prompt VERSION_OLD=$(VERSION_OLD) VERSION_NEW=$(VERSION_NEW) | jq -sRr @json); \
	if [ -z "$$PROMPT_CONTENT" ]; then \
	    echo "Failed to generate release notes using Claude AI, prompt content is empty"; \
	    exit 1; \
	fi; \
	temp_file=$$(mktemp); \
	curl -s https://api.anthropic.com/v1/messages \
		--header "x-api-key: $(ANTHROPIC_API_KEY)" \
		--header "anthropic-version: $(ANTHROPIC_VERSION)" \
		--header "content-type: application/json" \
		--data "{\"model\":\"$(CLAUDE_MODEL)\",\"max_tokens\":$(MAX_TOKENS),\"messages\":[{\"role\":\"user\",\"content\":$$PROMPT_CONTENT}]}" > $$temp_file; \
	jq -r '.content[] | select(.type=="text") | .text' $$temp_file || { echo "Failed to generate release notes using Claude AI, response: $$(cat $$temp_file)"; rm $$temp_file; exit 1; } ; \
	rm $$temp_file

generate-reddit-post:
ifndef VERSION_OLD
	@echo "Error: VERSION_OLD is not set"
	@exit 1
endif
ifndef VERSION_NEW
	@echo "Error: VERSION_NEW is not set"
	@exit 1
endif
ifndef ANTHROPIC_API_KEY
	@echo "Error: ANTHROPIC_API_KEY is not set"
	@exit 1
endif
	@PROMPT_CONTENT=$$(make prompt-reddit VERSION_OLD=$(VERSION_OLD) VERSION_NEW=$(VERSION_NEW) | jq -sRr @json); \
	if [ -z "$$PROMPT_CONTENT" ]; then \
	    echo "Failed to generate Reddit post using Claude AI, prompt content is empty"; \
	    exit 1; \
	fi; \
	temp_file=$$(mktemp); \
	curl -s https://api.anthropic.com/v1/messages \
		--header "x-api-key: $(ANTHROPIC_API_KEY)" \
		--header "anthropic-version: $(ANTHROPIC_VERSION)" \
		--header "content-type: application/json" \
		--data "{\"model\":\"$(CLAUDE_MODEL)\",\"max_tokens\":$(MAX_TOKENS),\"messages\":[{\"role\":\"user\",\"content\":$$PROMPT_CONTENT}]}" > $$temp_file; \
	jq -r '.content[] | select(.type=="text") | .text' $$temp_file || { echo "Failed to generate Reddit post using Claude AI, response: $$(cat $$temp_file)"; rm $$temp_file; exit 1; } ; \
	rm $$temp_file

all: build-multi