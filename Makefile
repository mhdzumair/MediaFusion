# Makefile

# Image version
VERSION ?= latest

# Builder name
BUILDER_NAME ?= mediafusion-builder

# Docker image name (single image — worker runs via CMD override)
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

# Gemini API settings
GEMINI_MODEL ?= gemini-3-flash-preview
MAX_TOKENS ?= 4096

# Reddit post settings
SUBREDDIT ?= MediaFusion
REDDIT_POST_TITLE ?= "MediaFusion $(VERSION_NEW) Update - What's New?"

# Worker job variables
JOB ?=
JOB_ARGS ?=

.PHONY: build build-multi tag push prompt update-version generate-notes generate-reddit-post generate-baseline frontend-install frontend-build frontend-dev frontend-lint frontend-fmt dev backend-dev python-lint python-fmt python-test rust-build rust-dev rust-test rust-fmt rust-lint lint fmt test worker-list-jobs worker-run-job worker-run-sport-video

build:
	docker build --build-arg VERSION=$(VERSION) -t $(DOCKER_IMAGE) -f deployment/Dockerfile .

update-version:
ifndef VERSION_NEW
	@echo "Error: VERSION_NEW is not set. Please set it like: make update-version VERSION_NEW=4.1.0"
	@exit 1
endif
	@echo "Updating version to $(VERSION_NEW)..."
	# Update main addon.xml
	@sed -i -e "/<addon\s*id=\"plugin\.video\.mediafusion\"/s/version=\"[^\"]*\"/version=\"$(VERSION_NEW)\"/" clients/kodi/plugin.video.mediafusion/addon.xml
	# Update repository addon.xml
	@sed -i -e "/<addon\s*id=\"repository\.mediafusion\"/s/version=\"[^\"]*\"/version=\"$(VERSION_NEW)\"/" clients/kodi/repository.mediafusion/addon.xml
	# Update deployment manifests that reference the docker images
	@for file in \
		deployment/docker-compose/docker-compose.yml \
		deployment/docker-compose/docker-compose-minimal.yml \
		deployment/docker-compose/docker-compose-postgres-ha.yml \
		deployment/k8s/local-deployment.yaml; do \
		if [ -f "$$file" ]; then \
			sed -i 's|image: $(DOCKER_REPO)/$(IMAGE_NAME):[^[:space:]]*|image: $(DOCKER_REPO)/$(IMAGE_NAME):$(VERSION_NEW)|g' "$$file"; \
		fi; \
	done
	# Update pyproject.toml
	@sed -i -e "s/^version = \"[^\"]*\"/version = \"$(VERSION_NEW)\"/" pyproject.toml
	# Update Rust Cargo.toml
	@sed -i -e "s/^version = \"[^\"]*\"/version = \"$(VERSION_NEW)\"/" backend/Cargo.toml
	# Refresh uv.lock so project and lock versions stay in sync
	@uv lock
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
	@echo "You are a technical writer for MediaFusion, an open-source streaming addon for Stremio and Kodi.\n"
	@echo "Generate a concise GitHub release note for version $(VERSION_NEW). Follow these rules:\n"
	@echo "1. Start with: ## 🚀 MediaFusion $(VERSION_NEW) Released\n"
	@echo "2. Organize by importance, NOT commit order. Lead with the most impactful changes.\n"
	@echo "3. Only include sections that are relevant (pick from: New Features & Enhancements, Bug Fixes, Performance, Documentation). Do not create empty sections.\n"
	@echo "4. Each item: start with a relevant emoji, write a single clear sentence explaining the user-facing change. Omit internal refactors unless they affect performance or reliability.\n"
	@echo "5. End with a Contributors section and a Full Changelog link (both provided below).\n"
	@echo "6. Output ONLY the release note in markdown. No preamble, no commentary.\n"
	@echo "\n### Commit Messages and Descriptions:\n"
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
	@echo "You are writing a Reddit post for r/$(SUBREDDIT) announcing MediaFusion $(VERSION_NEW).\n"
	@echo "Follow these rules:\n"
	@echo "1. Start with a **TL;DR** (2-3 bullet points summarizing the biggest changes).\n"
	@echo "2. Write in a conversational, community-friendly tone. Avoid marketing speak.\n"
	@echo "3. Organize into clear sections (e.g., What's New, Improvements, Bug Fixes) — only include sections that apply.\n"
	@echo "4. Focus on user-facing benefits, not implementation details.\n"
	@echo "5. End with a short note on how to update/install and a link to the full changelog.\n"
	@echo "6. Output ONLY the Reddit post body in markdown. No preamble, no commentary.\n"
	@echo "\n---\n"
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
ifndef GEMINI_API_KEY
	@echo "Error: GEMINI_API_KEY is not set"
	@exit 1
endif
	@PROMPT_CONTENT=$$(make prompt VERSION_OLD=$(VERSION_OLD) VERSION_NEW=$(VERSION_NEW) | jq -sRr @json); \
	if [ -z "$$PROMPT_CONTENT" ]; then \
	    echo "Failed to generate release notes using Gemini AI, prompt content is empty"; \
	    exit 1; \
	fi; \
	temp_file=$$(mktemp); \
	curl -s "https://generativelanguage.googleapis.com/v1beta/models/$(GEMINI_MODEL):generateContent" \
		--header "x-goog-api-key: $(GEMINI_API_KEY)" \
		--header "content-type: application/json" \
		--data "{\"contents\":[{\"parts\":[{\"text\":$$PROMPT_CONTENT}]}],\"generationConfig\":{\"maxOutputTokens\":$(MAX_TOKENS)}}" > $$temp_file; \
	RESULT=$$(jq -r '[.candidates[0].content.parts[] | select(.thought != true) | .text] | join("")' $$temp_file 2>/dev/null); \
	if [ -z "$$RESULT" ] || [ "$$RESULT" = "null" ]; then \
	    echo "Failed to generate release notes using Gemini AI, response: $$(cat $$temp_file)"; rm $$temp_file; exit 1; \
	fi; \
	echo "$$RESULT"; \
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
ifndef GEMINI_API_KEY
	@echo "Error: GEMINI_API_KEY is not set"
	@exit 1
endif
	@PROMPT_CONTENT=$$(make prompt-reddit VERSION_OLD=$(VERSION_OLD) VERSION_NEW=$(VERSION_NEW) | jq -sRr @json); \
	if [ -z "$$PROMPT_CONTENT" ]; then \
	    echo "Failed to generate Reddit post using Gemini AI, prompt content is empty"; \
	    exit 1; \
	fi; \
	temp_file=$$(mktemp); \
	curl -s "https://generativelanguage.googleapis.com/v1beta/models/$(GEMINI_MODEL):generateContent" \
		--header "x-goog-api-key: $(GEMINI_API_KEY)" \
		--header "content-type: application/json" \
		--data "{\"contents\":[{\"parts\":[{\"text\":$$PROMPT_CONTENT}]}],\"generationConfig\":{\"maxOutputTokens\":$(MAX_TOKENS)}}" > $$temp_file; \
	RESULT=$$(jq -r '[.candidates[0].content.parts[] | select(.thought != true) | .text] | join("")' $$temp_file 2>/dev/null); \
	if [ -z "$$RESULT" ] || [ "$$RESULT" = "null" ]; then \
	    echo "Failed to generate Reddit post using Gemini AI, response: $$(cat $$temp_file)"; rm $$temp_file; exit 1; \
	fi; \
	echo "$$RESULT"; \
	rm $$temp_file

generate-baseline:
	@echo "Generating backend/migrations/0001_baseline.up.sql from Alembic revision d826df80371b..."
	./scripts/generate_sqlx_baseline.sh

# Frontend build targets
frontend-install:
	cd clients/frontend && npm ci

frontend-build: frontend-install
	cd clients/frontend && npm run build

frontend-dev:
	cd clients/frontend && npm run dev

frontend-lint:
	cd clients/frontend && npm run lint

frontend-fmt:
	cd clients/frontend && npm run format:check

# Python targets
python-lint:
	uv run ruff check .

python-fmt:
	uv run ruff format --check .

python-test:
	uv run pytest

# Development targets
backend-dev:
	uvicorn api.main:app --reload --port 8000

dev:
	@echo "Starting backend and frontend in development mode..."
	@echo "Run 'make backend-dev' in one terminal and 'make frontend-dev' in another"

# Worker job targets
worker-list-jobs:
	cd backend && cargo run --bin mediafusion-worker -- --list-jobs

worker-run-job:
ifndef JOB
	@echo "Error: JOB is not set. Usage: make worker-run-job JOB=spider_sport_video"
	@echo "       Optionally pass args: make worker-run-job JOB=spider_registry_crawl JOB_ARGS='{\"indexer\":\"nyaa\"}'"
	@exit 1
endif
	cd backend && cargo run --bin mediafusion-worker -- --run-job $(JOB) $(if $(JOB_ARGS),--args '$(JOB_ARGS)',)

worker-run-sport-video:
	cd backend && cargo run --bin mediafusion-worker -- --run-job spider_sport_video

# Rust targets
rust-build:
	cd backend && cargo build --release --bin mediafusion-api --bin mediafusion-worker

rust-dev:
	cargo run --manifest-path backend/Cargo.toml --bin mediafusion-api

rust-test:
	cd backend && cargo test

rust-fmt:
	cd backend && cargo fmt --check

rust-lint:
	cd backend && cargo clippy --all-targets -- -D warnings

# Aggregate targets
lint: python-lint rust-lint frontend-lint

fmt: python-fmt rust-fmt frontend-fmt

test: python-test rust-test

all: build-multi