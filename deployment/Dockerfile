FROM python:3.13-slim-bookworm AS builder

WORKDIR /mediafusion

# Install build dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends git curl build-essential && \
    pip install --upgrade pip && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Create the mediafusion user with a specified home directory
RUN groupadd -r mediafusion && \
    useradd --no-log-init -r -g mediafusion -m -d /home/mediafusion mediafusion

RUN chown -R mediafusion:mediafusion /mediafusion
USER mediafusion

# Set the PATH environment variable to include the local bin directory
ENV PATH="/home/mediafusion/.local/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# Install uv and Python dependencies
COPY --from=ghcr.io/astral-sh/uv:0.6.3 /uv /uvx /bin/
RUN --mount=type=cache,target=/mediafusion/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --compile-bytecode

FROM python:3.13-slim-bookworm AS runtime

ARG VERSION

WORKDIR /mediafusion

# Install runtime dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Create the mediafusion user with a specified home directory
RUN groupadd -r mediafusion && \
    useradd --no-log-init -r -g mediafusion -m -d /home/mediafusion mediafusion

# Copy the Python environment and other necessary files from the builder stage
COPY --from=builder --chown=mediafusion:mediafusion /mediafusion/.venv /mediafusion/.venv

ENV PATH="/mediafusion/.venv/bin:$PATH"
ENV VERSION=${VERSION}

COPY --chown=mediafusion:mediafusion . /mediafusion

USER mediafusion

EXPOSE 8000
EXPOSE 9191

CMD ["/mediafusion/deployment/startup.sh"]
