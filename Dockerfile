# syntax=docker/dockerfile:1.7
#
# PO worker base image — `ubuntu:24.04` + node 22 + tmux + git + uv + bd +
# `@anthropic-ai/claude-code` + `prefect-orchestration` (core only).
# A non-root `coder` user is required because Claude Code refuses
# `--dangerously-skip-permissions` when running as root.
#
# Build (core only, no formula pack):
#   docker build -t po-worker:base .
#
# To bake in a formula pack on top, use the per-pack overlay:
#   docker build -t po-worker:software-dev \
#       --build-arg BASE=po-worker:base \
#       --build-arg PACK_SPEC=po-formulas-software-dev \
#       -f Dockerfile.pack .
#
# To bake in a sibling-repo pack at base-build time (no overlay needed):
#   docker build --build-context pack=packs/po-formulas-software-dev \
#                -t po-worker:dev .
#
# Auth: workers expect ANTHROPIC_API_KEY at runtime. The entrypoint
# bootstraps `~/.claude.json` so Claude Code skips onboarding and
# accepts the API key without a TTY prompt.

ARG BD_VERSION=1.0.3

# ----------------------------------------------------------------- pack
# Default stage so `COPY --from=pack` always resolves. Users supply a real
# pack source dir by overriding this stage at build time:
#   docker build --build-context pack=packs/po-formulas-software-dev …
# When unset, the runtime stage detects the empty pack and installs core
# only.
FROM scratch AS pack

# -------------------------------------------------------- claude-context
# Default stage so `COPY --from=claude-context` always resolves. Users
# populate it via `scripts/sync-claude-context.sh` and pass:
#   docker build --build-context claude-context=./claude-context …
# When unset, the runtime stage skips the bake silently — image still
# builds, pod just won't have ~/.claude/CLAUDE.md, prompts/, skills/, …
# (matches the pre-tyf.2 behavior exactly).
FROM scratch AS claude-context

# ---------------------------------------------------------------- tools
# Slim stage that produces the `uv` and `bd` binaries. node + claude come
# from the apt/npm install in the runtime stage so we keep one node
# install rather than copying node_modules between stages.
FROM debian:bookworm-slim AS tools

ARG BD_VERSION
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ca-certificates curl tar \
 && rm -rf /var/lib/apt/lists/*

# uv (Astral) — single static binary, written to ~/.local/bin/uv.
RUN curl -fsSL https://astral.sh/uv/install.sh | sh \
 && mv /root/.local/bin/uv /usr/local/bin/uv \
 && uv --version

# bd (beads) — Go binary release from gastownhall/beads.
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64) goarch=amd64 ;; \
      arm64) goarch=arm64 ;; \
      *) echo "unsupported arch: $arch" >&2; exit 1 ;; \
    esac; \
    url="https://github.com/gastownhall/beads/releases/download/v${BD_VERSION}/beads_${BD_VERSION}_linux_${goarch}.tar.gz"; \
    curl -fsSL "$url" -o /tmp/bd.tgz; \
    tar -xzf /tmp/bd.tgz -C /usr/local/bin bd; \
    chmod +x /usr/local/bin/bd; \
    rm /tmp/bd.tgz; \
    bd --version

# -------------------------------------------------------------- runtime
FROM ubuntu:24.04 AS runtime

ARG PACK_SPEC=
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PATH=/home/coder/.local/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin \
    PREFECT_API_URL=http://prefect-server:4200/api \
    PO_BACKEND=cli

# System deps. Includes tmux so a human can `kubectl exec -it` and lurk a
# session manually; backend selection at runtime decides whether tmux is
# actually used (TTY required — see prefect_orchestration/backend_select.py).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ca-certificates curl git openssh-client gnupg sudo jq tmux \
        python3 python3-venv python3-pip \
 && rm -rf /var/lib/apt/lists/* \
 && ln -sf /usr/bin/python3 /usr/local/bin/python

# Node 22 (NodeSource) — required for Claude Code.
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/* \
 && node --version

# Claude Code — install globally, will be invoked by the agent backends.
RUN npm install -g @anthropic-ai/claude-code \
 && claude --version

# uv + bd from the tools stage.
COPY --from=tools /usr/local/bin/uv /usr/local/bin/uv
COPY --from=tools /usr/local/bin/bd /usr/local/bin/bd
RUN uv --version && bd --version

# Non-root user: Claude Code refuses --dangerously-skip-permissions as
# root. `coder` gets passwordless sudo for laptop dev convenience; in k8s
# the SecurityContext can drop sudo entirely.
#
# Pin UID/GID to 1000 (matches the typical host user on Linux laptops)
# so bind-mounted rigs read/write cleanly. ubuntu:24.04 ships a default
# `ubuntu` user at UID 1000 — delete it before creating `coder`.
RUN if id ubuntu >/dev/null 2>&1; then userdel -r ubuntu 2>/dev/null || true; fi \
 && groupadd -g 1000 coder \
 && useradd -m -s /bin/bash -u 1000 -g 1000 -G sudo coder \
 && echo "coder ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers \
 && mkdir -p /workspace /rig \
 && chown -R coder:coder /workspace /rig \
 && mkdir -p /home/coder/.claude \
 && chown -R coder:coder /home/coder/.claude

# Bake the user's CLAUDE.md / prompts / skills / commands / settings.json
# into /home/coder/.claude/. Source is the `claude-context` build stage
# (default `FROM scratch`), populated by:
#   scripts/sync-claude-context.sh && \
#   docker build --build-context claude-context=./claude-context …
# Skipping the build-context arg is fine — COPY from an empty stage is a
# no-op. Issue: prefect-orchestration-tyf.2.
COPY --from=claude-context --chown=coder:coder . /home/coder/.claude/

# Install prefect-orchestration (and optionally a sibling-repo pack) into
# `coder`'s uv-tool environment. Copy core source into /src/po-core; copy
# the pack build context (defaults to scratch via FROM scratch AS pack)
# into /src/pack and branch on whether it has a real pyproject.toml.
COPY --chown=coder:coder pyproject.toml README.md /src/po-core/
COPY --chown=coder:coder prefect_orchestration /src/po-core/prefect_orchestration
COPY --from=pack --chown=coder:coder . /src/pack/

USER coder
WORKDIR /home/coder

RUN set -eux; \
    mkdir -p /home/coder/.local/bin; \
    if [ -f /src/pack/pyproject.toml ]; then \
        echo ">>> installing core + local pack from /src/pack"; \
        # `pip install` with the local sibling pack: pip recognizes the\
        # already-installed prefect-orchestration in the venv and won't \
        # try to resolve it from PyPI.\
        python3 -m venv /home/coder/.local/po-venv; \
        /home/coder/.local/po-venv/bin/pip install --quiet --upgrade pip; \
        /home/coder/.local/po-venv/bin/pip install --quiet /src/po-core; \
        /home/coder/.local/po-venv/bin/pip install --quiet /src/pack; \
        ln -sf /home/coder/.local/po-venv/bin/po /home/coder/.local/bin/po; \
        ln -sf /home/coder/.local/po-venv/bin/prefect /home/coder/.local/bin/prefect; \
    elif [ -n "${PACK_SPEC}" ]; then \
        echo ">>> installing core + ${PACK_SPEC}"; \
        uv tool install --with "${PACK_SPEC}" /src/po-core; \
    else \
        echo ">>> installing core only (no pack)"; \
        uv tool install /src/po-core; \
    fi; \
    /home/coder/.local/bin/po --help >/dev/null; \
    /home/coder/.local/bin/po list

# Entrypoint bootstraps Claude Code config from ANTHROPIC_API_KEY before
# `exec`ing the CMD.
COPY --chown=coder:coder docker/entrypoint.sh /usr/local/bin/po-entrypoint
USER root
RUN chmod +x /usr/local/bin/po-entrypoint
USER coder

WORKDIR /rig
VOLUME ["/rig"]

ENTRYPOINT ["/usr/local/bin/po-entrypoint"]
CMD ["prefect", "worker", "start", "--pool", "po"]
