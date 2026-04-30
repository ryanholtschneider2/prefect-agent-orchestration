# PO — Prefect Orchestration core
# One-line install for `po` CLI + agent skill.
#
# Quick start:
#   make install                  # install everything PO needs + skill for all detected agents
#   make install AGENT=claude     # install skill only for Claude Code (others: cursor, aider, all, none)
#   make uninstall                # remove the editable install + skill symlinks
#   make doctor                   # `po doctor` health check
#   make test                     # full test suite
#
# Override locations:
#   make install PREFIX=$HOME/.local

REPO_DIR        := $(abspath $(lastword $(MAKEFILE_LIST))/..)
REPO_DIR        := $(patsubst %/,%,$(dir $(abspath $(firstword $(MAKEFILE_LIST)))))
PREFIX          ?= $(HOME)/.local
SKILL_NAME      := po
AGENT           ?= all

# Per-agent skill install dirs. Each agent reads skills from its own dir;
# we symlink `<skill-dir>/po` to this repo's `skills/` so the SKILL.md +
# evals/ stay one source of truth.
CLAUDE_SKILLS_DIR := $(HOME)/.claude/skills
CURSOR_SKILLS_DIR := $(HOME)/.cursor/skills
AIDER_SKILLS_DIR  := $(HOME)/.aider/skills

LOG_DIR         := $(REPO_DIR)/.planning/logs

# Internal: run CMD, tee full output to LOG_DIR/NAME.log.
# Print "  ✓ NAME" on success; last 30 log lines + exit on failure.
define _run_logged
	@mkdir -p $(LOG_DIR)
	@if $(2) > $(LOG_DIR)/$(1).log 2>&1; then \
	  echo "  ✓ $(1)"; \
	else \
	  echo "  ✗ $(1) — last 30 lines (full log: $(LOG_DIR)/$(1).log):"; \
	  tail -30 $(LOG_DIR)/$(1).log; \
	  exit 1; \
	fi
endef

.PHONY: help install install-cli install-skill uninstall uninstall-skill doctor test test-unit test-e2e lint format clean

help:
	@echo "PO install targets (run \`make install\` for all-in-one):"
	@echo ""
	@echo "  make install              CLI + skill for all detected agents"
	@echo "  make install AGENT=claude   CLI + skill for Claude Code only"
	@echo "  make install AGENT=cursor   CLI + skill for Cursor only"
	@echo "  make install AGENT=aider    CLI + skill for Aider only"
	@echo "  make install AGENT=none     CLI only, no skill"
	@echo ""
	@echo "  make install-cli          editable \`po\` install (uv tool)"
	@echo "  make install-skill        skill symlink only (AGENT=...)"
	@echo "  make uninstall            remove CLI + all skill symlinks"
	@echo "  make doctor               run \`po doctor\` health check"
	@echo "  make test                 full test suite"
	@echo "  make lint                 ruff check+fix, ruff format, tsc (logs → .planning/logs/)"
	@echo "  make test-unit            pytest unit layer (excludes tests/e2e/)"
	@echo "  make test-e2e             pytest e2e layer only (slow; ~2-3 min)"
	@echo "  make format               ruff format (Python)"
	@echo ""
	@echo "Detected coding agents (will get skill on AGENT=all):"
	@$(MAKE) -s _detect-agents

_detect-agents:
	@if command -v claude >/dev/null 2>&1 || [ -d $(HOME)/.claude ]; then echo "  ✓ Claude Code  ($(CLAUDE_SKILLS_DIR))"; else echo "  ✗ Claude Code"; fi
	@if [ -d $(HOME)/.cursor ]; then echo "  ✓ Cursor       ($(CURSOR_SKILLS_DIR))"; else echo "  ✗ Cursor"; fi
	@if command -v aider >/dev/null 2>&1 || [ -d $(HOME)/.aider ]; then echo "  ✓ Aider        ($(AIDER_SKILLS_DIR))"; else echo "  ✗ Aider"; fi

install: install-cli install-skill
	@echo ""
	@echo "✓ PO installed."
	@echo "  CLI:        $(PREFIX)/bin/po  (try: po list)"
	@echo "  Skill:      $(SKILL_NAME) (AGENT=$(AGENT))"
	@echo ""
	@echo "Next steps:"
	@echo "  - prefect server start              (if not already running)"
	@echo "  - po packs install --editable <pack>   (add a formula pack)"
	@echo "  - po doctor                          (verify wiring)"

install-cli:
	@command -v uv >/dev/null 2>&1 || { echo "ERROR: uv not on PATH. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }
	@echo "→ installing \`po\` CLI from $(REPO_DIR) (editable)"
	uv tool install --editable $(REPO_DIR) --force
	@echo "✓ \`po\` installed at $$(uv tool dir)/prefect-orchestration/bin/po"

install-skill:
	@case "$(AGENT)" in \
	  all) \
	    echo "→ installing skill for all detected agents"; \
	    if command -v claude >/dev/null 2>&1 || [ -d $(HOME)/.claude ]; then \
	      $(MAKE) -s _link-skill SKILL_DIR=$(CLAUDE_SKILLS_DIR) AGENT_LABEL="Claude Code"; \
	    fi; \
	    if [ -d $(HOME)/.cursor ]; then \
	      $(MAKE) -s _link-skill SKILL_DIR=$(CURSOR_SKILLS_DIR) AGENT_LABEL="Cursor"; \
	    fi; \
	    if command -v aider >/dev/null 2>&1 || [ -d $(HOME)/.aider ]; then \
	      $(MAKE) -s _link-skill SKILL_DIR=$(AIDER_SKILLS_DIR) AGENT_LABEL="Aider"; \
	    fi ;; \
	  claude)  $(MAKE) -s _link-skill SKILL_DIR=$(CLAUDE_SKILLS_DIR) AGENT_LABEL="Claude Code" ;; \
	  cursor)  $(MAKE) -s _link-skill SKILL_DIR=$(CURSOR_SKILLS_DIR) AGENT_LABEL="Cursor" ;; \
	  aider)   $(MAKE) -s _link-skill SKILL_DIR=$(AIDER_SKILLS_DIR)  AGENT_LABEL="Aider" ;; \
	  none)    echo "→ skipping skill install (AGENT=none)" ;; \
	  *) echo "ERROR: unknown AGENT=$(AGENT). Try: all | claude | cursor | aider | none"; exit 1 ;; \
	esac

# Internal: symlink $(REPO_DIR)/skills → $(SKILL_DIR)/$(SKILL_NAME).
# If a target already exists, replace (rm -rf for dir, rm for symlink).
_link-skill:
	@mkdir -p $(SKILL_DIR)
	@if [ -L $(SKILL_DIR)/$(SKILL_NAME) ] || [ -e $(SKILL_DIR)/$(SKILL_NAME) ]; then \
	  rm -rf $(SKILL_DIR)/$(SKILL_NAME); \
	fi
	@ln -s $(REPO_DIR)/skills $(SKILL_DIR)/$(SKILL_NAME)
	@echo "  ✓ $(AGENT_LABEL): $(SKILL_DIR)/$(SKILL_NAME) → $(REPO_DIR)/skills"

uninstall: uninstall-skill
	@echo "→ removing \`po\` CLI"
	-uv tool uninstall prefect-orchestration 2>/dev/null || true
	@echo "✓ uninstalled"

uninstall-skill:
	@for d in $(CLAUDE_SKILLS_DIR) $(CURSOR_SKILLS_DIR) $(AIDER_SKILLS_DIR); do \
	  if [ -L $$d/$(SKILL_NAME) ] || [ -e $$d/$(SKILL_NAME) ]; then \
	    rm -rf $$d/$(SKILL_NAME); \
	    echo "  removed $$d/$(SKILL_NAME)"; \
	  fi; \
	done

doctor:
	po doctor

test:
	uv run python -m pytest tests/ -v

lint:
	$(call _run_logged,ruff-check,uv run ruff check --fix prefect_orchestration tests)
	$(call _run_logged,ruff-format,uv run ruff format prefect_orchestration tests)
	$(call _run_logged,tsc,cd tui && bun run typecheck)

test-unit:
	$(call _run_logged,pytest-unit,uv run python -m pytest tests/ --ignore=tests/e2e -q)

test-e2e:
	$(call _run_logged,pytest-e2e,uv run python -m pytest tests/e2e -q)

format:
	uv run ruff format prefect_orchestration/ tests/

clean:
	rm -rf .pytest_cache .ruff_cache **/__pycache__ build dist *.egg-info
