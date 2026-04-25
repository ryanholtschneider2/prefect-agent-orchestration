# syntax=docker/dockerfile:1.7
#
# PO worker image — bundles `uv` + `bd` + `claude` CLI + `prefect-orchestration`
# + a configurable formula pack so a single container can serve as both a
# `prefect worker` host and an interactive `po run` driver.
#
# Build:
#   docker build -t po-worker:dev .
#   docker build -t po-worker:dev --build-arg PACK_SPEC=po-formulas-software-dev .
#
# A partial build that stops at the tools stage (faster iteration on docs):
#   docker build --target tools -t po-tools:dev .
#
# OAuth: claude expects ~/.claude/.credentials.json. The compose stack
# bind-mounts it from the host. For k8s, see engdocs/work-pools.md — that
# path is intentionally out of scope here.

ARG PYTHON_VERSION=3.13
ARG NODE_VERSION=20
ARG BD_VERSION=0.10.0

# ---------------------------------------------------------------- tools
# Single shared stage that produces the three CLI binaries (uv, bd) and the
# globally-installed Node package (claude). Kept slim so partial builds are
# fast — we copy the artifacts into the runtime stage rather than carrying
# the whole toolchain.
FROM node:${NODE_VERSION}-slim AS tools

ARG BD_VERSION
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ca-certificates curl tar git \
 && rm -rf /var/lib/apt/lists/*

# uv (Astral) — single static binary
RUN curl -fsSL https://astral.sh/uv/install.sh | sh \
 && mv /root/.local/bin/uv /usr/local/bin/uv \
 && uv --version

# bd (beads) — Go binary release
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64) goarch=amd64 ;; \
      arm64) goarch=arm64 ;; \
      *) echo "unsupported arch: $arch" >&2; exit 1 ;; \
    esac; \
    url="https://github.com/steveyegge/beads/releases/download/v${BD_VERSION}/beads_${BD_VERSION}_linux_${goarch}.tar.gz"; \
    curl -fsSL "$url" -o /tmp/bd.tgz; \
    tar -xzf /tmp/bd.tgz -C /usr/local/bin bd; \
    rm /tmp/bd.tgz; \
    bd --version

# claude CLI (Node-based, official package)
RUN npm install -g --omit=dev @anthropic-ai/claude-code \
 && claude --version

# -------------------------------------------------------------- runtime
FROM python:${PYTHON_VERSION}-slim AS runtime

ARG PACK_SPEC=po-formulas-software-dev
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    PATH=/root/.local/bin:/usr/local/bin:$PATH \
    PREFECT_API_URL=http://prefect-server:4200/api \
    PO_BACKEND=cli

# Node runtime is required at runtime by the claude CLI shim. Copy from the
# tools stage rather than reinstalling Debian's node package — saves ~150MB
# and keeps the version pinned.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ca-certificates git curl \
 && rm -rf /var/lib/apt/lists/*

COPY --from=tools /usr/local/bin/uv     /usr/local/bin/uv
COPY --from=tools /usr/local/bin/bd     /usr/local/bin/bd
COPY --from=tools /usr/local/bin/node   /usr/local/bin/node
COPY --from=tools /usr/local/bin/npm    /usr/local/bin/npm
COPY --from=tools /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf /usr/local/lib/node_modules/@anthropic-ai/claude-code/cli.js \
           /usr/local/bin/claude \
 && claude --version \
 && bd --version \
 && uv --version

# Install prefect-orchestration + the requested formula pack as one
# uv-managed tool environment. Building from the local context (rather than
# a PyPI release) so the image always tracks the working tree — packs that
# need a pinned release should override PACK_SPEC at build time.
COPY pyproject.toml README.md /src/po-core/
COPY prefect_orchestration /src/po-core/prefect_orchestration

RUN uv tool install --with "${PACK_SPEC}" /src/po-core \
 && /root/.local/bin/po --help >/dev/null \
 && /root/.local/bin/po list

# Tmux is intentionally NOT installed — software_dev.py auto-falls back to
# ClaudeCliBackend when `tmux` is absent. Setting PO_BACKEND=cli above makes
# the choice explicit; PO_BACKEND=tmux will hard-error on purpose.

WORKDIR /rig
VOLUME ["/rig"]

# Default: run as a Prefect worker against the `po` pool. Override the CMD
# (or `docker compose run`) to drop into a `po run …` invocation directly.
CMD ["prefect", "worker", "start", "--pool", "po"]
