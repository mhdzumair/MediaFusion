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
GEMINI_MODEL ?= gemini-3.1-flash-lite
MAX_TOKENS ?= 4096

# Reddit post settings
SUBREDDIT ?= MediaFusion
REDDIT_POST_TITLE ?= "MediaFusion $(VERSION_NEW) Update - What's New?"

# Worker job variables
JOB ?=
JOB_ARGS ?=

# Ignore file events during build/run and debounce rapid saves to avoid
# overlapping restarts that race for the same port (ADDRINUSE).
CARGO_WATCH_FLAGS = --watch-when-idle -d 1

# Parity harness hosts (override on command line or in qa/.env)
RUST_HOST  ?= http://localhost:8000
PYTHON_HOST ?= http://localhost:8001
PYTHON_PORT ?= 8001

.PHONY: build build-multi tag push prompt update-version generate-notes generate-reddit-post generate-baseline frontend-install frontend-build frontend-dev frontend-lint frontend-fmt dev backend-dev python-lint python-fmt python-test rust-build rust-dev rust-test rust-fmt rust-lint lint fmt test worker-list-jobs worker-run-job worker-run-sport-video exception-videos python-golden parity-seed parity-check parity-e2e parity grafana-dev grafana-dev-stop

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
	curl -sf --retry 3 --retry-delay 5 --retry-all-errors \
		"https://generativelanguage.googleapis.com/v1beta/models/$(GEMINI_MODEL):generateContent" \
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
	curl -sf --retry 3 --retry-delay 5 --retry-all-errors \
		"https://generativelanguage.googleapis.com/v1beta/models/$(GEMINI_MODEL):generateContent" \
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
	cd clients/frontend && pnpm ci

frontend-build: frontend-install
	cd clients/frontend && pnpm run build

frontend-dev:
	cd clients/frontend && pnpm run dev

frontend-lint:
	cd clients/frontend && pnpm run lint

frontend-fmt:
	cd clients/frontend && pnpm run format

dev:
	@set -e; \
	cleanup() { kill $$(jobs -p) 2>/dev/null || true; }; \
	trap cleanup INT TERM EXIT; \
	echo "Starting backend and frontend in development mode..."; \
	if cargo watch -h >/dev/null 2>&1; then \
		cd backend && cargo watch $(CARGO_WATCH_FLAGS) -x 'run --bin mediafusion-api' & \
	else \
		echo "Note: cargo-watch not installed — run: cargo install cargo-watch"; \
		echo "Backend will not auto-reload on file changes."; \
		cd backend && cargo run --bin mediafusion-api & \
	fi; \
	cd clients/frontend && pnpm run dev; \
	wait

backend-dev: rust-dev

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
	@if cargo watch -h >/dev/null 2>&1; then \
		cd backend && cargo watch $(CARGO_WATCH_FLAGS) -x 'run --bin mediafusion-api'; \
	else \
		echo "Note: cargo-watch not installed — run: cargo install cargo-watch"; \
		cd backend && cargo run --bin mediafusion-api; \
	fi

rust-test:
	cd backend && cargo test

rust-fmt:
	cd backend && cargo fmt --all

rust-lint:
	cd backend && cargo clippy --all-targets -- -D warnings

install-hooks:  ## Install version-controlled git hooks (run once per clone)
	git config core.hooksPath .githooks
	chmod +x .githooks/pre-commit
	@echo "Git hooks installed from .githooks/ — cargo check will now run on staged Rust files."

exception-videos:
	python3 python-deprecated/utils/exception_video.py

# ── Python golden reference server ────────────────────────────────────────────
#
# Starts the deprecated Python API on PYTHON_PORT (default 8001) so the parity
# harness can diff Rust vs Python responses side by side.
#
# How it works:
#   1. Creates python-deprecated/reference -> api symlink (gitignored).
#      This restores the import alias that was removed when migrating to Rust.
#   2. Reads credentials from the repo-root .env (same file the Rust server uses).
#   3. Runs uvicorn from the python-deprecated/ directory.
#
# Prerequisites:
#   uv sync           (installs Python deps into .venv)
#   Postgres + Redis  (already running for the Rust server)
#
python-golden:
	@echo "→ Setting up Python golden reference server on port $(PYTHON_PORT)..."
	@if [ ! -e python-deprecated/reference ]; then \
		ln -sf api python-deprecated/reference; \
		echo "  Created python-deprecated/reference -> api symlink"; \
	fi
	@if [ ! -e python-deprecated/resources ]; then \
		ln -sf ../resources python-deprecated/resources; \
		echo "  Created python-deprecated/resources -> ../resources symlink"; \
	fi
	@echo "  Loading .env and starting uvicorn (Ctrl-C to stop)..."
	@PYTHONPATH="$(PWD)/python-deprecated" \
		HOST_URL="http://127.0.0.1:$(PYTHON_PORT)" \
		POSTER_HOST_URL="http://127.0.0.1:$(PYTHON_PORT)" \
		STREAM_RS_PORT=$(PYTHON_PORT) \
		uv run uvicorn api.main:app --host 0.0.0.0 --port $(PYTHON_PORT) --reload --app-dir python-deprecated

# ── Parity harness targets ─────────────────────────────────────────────────────
#
# Requires:
#   - Rust server running at RUST_HOST (default :8000), e.g. via `make rust-dev`
#   - Python golden running at PYTHON_HOST (default :8001), e.g. via `make python-golden`
#   - qa/.env populated from qa/.env.example
#
# Usage:
#   make parity                  # full run: seed → parity-check → e2e
#   make parity-seed             # warm Redis cache only
#   make parity-check            # route/status parity (Rust vs Python)
#   make parity-e2e              # structural response parity
#
#   RUST_HOST=http://myhost:8000 make parity-check   # override hosts inline

parity-seed:
	@echo "→ Pre-warming stream cache on $(RUST_HOST)..."
	uv run python qa/seed_cache.py --host $(RUST_HOST)

parity-check:
	@echo "→ Running parity check: Rust=$(RUST_HOST)  Python=$(PYTHON_HOST)..."
	uv run python qa/parity_test.py \
		--rust $(RUST_HOST) \
		--python $(PYTHON_HOST)

parity-e2e:
	@echo "→ Running e2e structural parity: Rust=$(RUST_HOST)  Python=$(PYTHON_HOST)..."
	uv run python qa/e2e_verify.py \
		--rust $(RUST_HOST) \
		--python $(PYTHON_HOST)

parity: parity-seed parity-check parity-e2e

# ── Grafana / Prometheus dev observability ─────────────────────────────────────
#
# Starts standalone Prometheus + Grafana containers that scrape the Rust server
# running on your local machine (host.docker.internal:8001).
#
# Grafana:    http://localhost:3001  (anonymous viewer, no login needed)
# Prometheus: http://localhost:9091
#
# Dashboards are auto-provisioned from qa/dashboards/ — no manual import needed.
# The "MediaFusion — Rust Backend" dashboard loads automatically.
#
grafana-dev:
	@echo "→ Starting Prometheus + Grafana for local dev..."
	docker compose -f deployment/docker-compose/docker-compose-grafana-dev.yml up -d
	@echo ""
	@echo "  Grafana:    http://localhost:3001"
	@echo "  Prometheus: http://localhost:9091"
	@echo ""
	@echo "  Dashboards auto-load from qa/dashboards/."
	@echo "  Make sure the Rust server is running: make rust-dev"

grafana-dev-stop:
	docker compose -f deployment/docker-compose/docker-compose-grafana-dev.yml down

# Aggregate targets
lint: rust-lint frontend-lint

fmt: rust-fmt frontend-fmt

test: rust-test

all: build-multi