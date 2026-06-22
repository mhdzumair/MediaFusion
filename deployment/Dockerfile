# Pre-built Rust binaries (built natively in CI, passed via rust-dist/<arch>/).
# The API server binary has the React SPA embedded via the embed-frontend feature;
# no separate frontend build stage is needed.
FROM scratch AS rust-builder
ARG TARGETARCH
COPY --chmod=755 rust-dist/${TARGETARCH}/mediafusion-api /mediafusion-api
COPY --chmod=755 rust-dist/${TARGETARCH}/mediafusion-worker /mediafusion-worker

# ── Single runtime image (API default, worker via CMD override) ───────────────
# debian:trixie-slim = glibc 2.41, matching the ubuntu-24.04 / ubuntu-24.04-arm CI runners.
FROM debian:trixie-slim AS runtime

WORKDIR /mediafusion

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ca-certificates \
    curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN groupadd -r mediafusion && \
    useradd --no-log-init -r -g mediafusion -m -d /home/mediafusion mediafusion

COPY --from=rust-builder --chown=mediafusion:mediafusion /mediafusion-api    /usr/local/bin/mediafusion-api
COPY --from=rust-builder --chown=mediafusion:mediafusion /mediafusion-worker /usr/local/bin/mediafusion-worker

COPY --chown=mediafusion:mediafusion . /mediafusion

ARG VERSION
ENV VERSION=${VERSION}

USER mediafusion

EXPOSE 8000

# Default: run the API server.
# Override with: command: ["/usr/local/bin/mediafusion-worker"]
CMD ["/usr/local/bin/mediafusion-api"]
