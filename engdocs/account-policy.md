# Coding Account Policy

Issue: `prefect-orchestration-p3fn`

## Goal

Select the correct provider account for every coding-agent process without
copying OAuth tokens into PO or Orchestra configuration.

The registry is machine-local. It stores account metadata and provider-owned
state directories only. Codex, Claude Code, Cursor, and future tools continue
to own their credentials.

## Ownership

- PO owns the registry schema, resolver, CLI, and JSON contract.
- PO applies the resolved environment to every `AgentSession`.
- Orchestra calls the resolver for direct agent launches.
- Product rigs such as Soloco select an account class; they do not manage
  credentials.

## Configuration

Default path: `~/.config/po/accounts.toml`

```toml
version = 1

[accounts.codex-personal]
provider = "codex"
class = "personal"
home = "~/.codex-accounts/personal"

[accounts.claude-work]
provider = "claude"
class = "work"
home = "~/.claude-accounts/work"
email = "ryan@example.com"

[accounts.cursor-personal]
provider = "cursor"
class = "personal"

[[rules]]
path = "~/src/personal"
class = "personal"

[[rules]]
path = "~/src/work"
class = "work"
```

Accounts identify provider authentication contexts. Classes express policy
without coupling a project to a specific provider. A project classified as
`work` resolves to the work account for whichever provider launches.

An account may declare `config_source = "<account-handle>"`. Running
`po account sync-config` links the provider's static configuration from that
source account while leaving credentials and runtime state isolated. Claude
shares global instructions, slash commands, skills, agents, hooks, scripts,
prompts, packs, workflows, settings, MCP configuration, and statusline code.
Codex shares `AGENTS.md`, `config.toml`, rules, skills, agents, hooks, and
references. Authentication files, history, projects, sessions, caches, and
plugin state are never linked.

Cursor currently has no supported isolated home-directory contract. Cursor
accounts can still be registered and selected for policy validation, but the
resolver returns no provider environment until Cursor exposes a stable
account-isolation mechanism.

## Resolution

Inputs:

- provider
- working directory
- optional explicit account handle
- optional explicit account class

Precedence:

1. Explicit account handle
2. Explicit account class
3. `PO_ACCOUNT`
4. `PO_ACCOUNT_CLASS`
5. Longest matching directory rule
6. Unique account for the provider

Resolution fails when no account matches or when a class maps to multiple
accounts for the same provider. It never silently falls back across classes.

Explicit account overrides are allowed for ad-hoc use. Directory-derived
policy is fail-closed: a requested class that conflicts with the longest
matching directory rule is rejected unless the caller explicitly requested a
specific account handle.

## Provider Environment

| Provider | Environment |
|---|---|
| Codex | `CODEX_HOME=<account.home>` |
| Claude Code | `CLAUDE_CONFIG_DIR=<account.home>` |
| Cursor | no provider-specific variable |

When `account.user_home` is configured, all providers also receive
`HOME=<account.user_home>`. This is intended for clean-room accounts that must
not discover user-global prompts, skills, rules, or state. Cursor uses this
mechanism because its CLI stores global configuration under `~/.cursor` and
does not expose a dedicated configuration-directory variable.

Local coding-agent launches remove model-provider API-key environment
variables before starting the child process. Interactive accounts therefore
use their provider's subscription login rather than silently switching to API
billing.

For isolated Codex accounts, each home should use file credential storage so
authentication is scoped under `CODEX_HOME`.

## CLI Contract

Human-readable commands:

```bash
po account list
po account status --provider codex --cwd .
po account add codex-personal --provider codex --class personal \
  --home ~/.codex-accounts/personal
```

Machine contract:

```bash
po account resolve --provider codex --cwd /repo --json
```

Success:

```json
{
  "handle": "codex-personal",
  "provider": "codex",
  "class": "personal",
  "home": "/home/user/.codex-accounts/personal",
  "source": "directory-rule",
  "environment": {
    "CODEX_HOME": "/home/user/.codex-accounts/personal"
  }
}
```

Errors are emitted to stderr and return non-zero. Secret values are never
included in output.

Direct terminal launches use the same contract:

```bash
po agent claude -- --version
po agent codex -- --help
po agent cursor --account cursor-personal -- --yolo --help
```

`po agent` resolves the current directory, overlays the provider environment,
and replaces itself with the real CLI process. Shell functions may route the
ordinary `claude`, `codex`, and `cursor` commands through this launcher.
Providers without an account in the selected class fail closed.

Cursor launches `cursor-agent` (or `agent`) rather than the IDE shim. The
recommended shell wrapper routes `cursor agent …` through the same account
policy as `claude` / `codex`, injects `--yolo` by default, and maps
`--account-class` / `--account-type personal` to the explicit handle
`cursor-personal` so work-directory rules can be overridden ad hoc.

## Session Safety

PO resolves the account immediately before each agent subprocess is launched
and overlays the returned provider environment through the existing
`extra_env` path. A resumed session therefore continues to use the same
directory-derived or explicit policy.

Run artifacts should record the non-secret account handle in a later
observability increment. The first implementation keeps the execution contract
small and does not implement quota rotation, token copying, keychain mutation,
or global symlink switching.
