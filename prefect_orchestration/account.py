"""Machine-local coding-account registry and provider environment resolver."""

from __future__ import annotations

import json
import os
import shutil
import tomllib
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping

import typer

CONFIG_PATH = Path.home() / ".config" / "po" / "accounts.toml"
SUPPORTED_PROVIDERS = {"claude", "codex", "cursor"}
PROVIDER_EXECUTABLES = {
    "claude": ("claude",),
    "codex": ("codex",),
    "cursor": ("cursor-agent", "agent"),
}
PROVIDER_ALIASES = {
    "claude": "claude",
    "claude-code": "claude",
    "codex": "codex",
    "cursor": "cursor",
}
SHARED_CONFIG_PATHS = {
    "claude": (
        "CLAUDE.md",
        "commands",
        "skills",
        "agents",
        "hooks",
        "scripts",
        "prompts",
        "packs",
        "workflows",
        "settings.json",
        "settings.local.json",
        ".mcp.json",
        "statusline.sh",
    ),
    "codex": (
        "AGENTS.md",
        "config.toml",
        "rules",
        "skills",
        "agents",
        "hooks",
        "references",
    ),
    "cursor": (
        "cli-config.json",
        "mcp.json",
        "hooks.json",
        "hooks",
        "rules",
        "commands",
        "argv.json",
    ),
}


class AccountError(ValueError):
    """Raised when account configuration or resolution is invalid."""


@dataclass(frozen=True)
class Account:
    handle: str
    provider: str
    account_class: str
    home: str | None = None
    email: str | None = None
    description: str | None = None
    config_source: str | None = None


@dataclass(frozen=True)
class DirectoryRule:
    path: str
    account_class: str


@dataclass(frozen=True)
class Registry:
    accounts: dict[str, Account]
    rules: tuple[DirectoryRule, ...]
    path: Path = CONFIG_PATH


@dataclass(frozen=True)
class Resolution:
    handle: str
    provider: str
    account_class: str
    home: str | None
    email: str | None
    source: str
    environment: dict[str, str]

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["class"] = data.pop("account_class")
        return data


def normalize_provider(provider: str) -> str:
    normalized = PROVIDER_ALIASES.get(provider.strip().lower())
    if normalized is None:
        supported = ", ".join(sorted(SUPPORTED_PROVIDERS))
        raise AccountError(
            f"unsupported provider {provider!r}; expected one of: {supported}"
        )
    return normalized


def _expand_path(value: str) -> Path:
    return Path(os.path.expandvars(value)).expanduser().resolve()


def load_registry(path: Path = CONFIG_PATH) -> Registry:
    if not path.exists():
        raise AccountError(
            f"account registry not found: {path}; add one with `po account add`"
        )
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise AccountError(f"invalid account registry {path}: {exc}") from exc

    version = data.get("version", 1)
    if version != 1:
        raise AccountError(f"unsupported account registry version: {version!r}")

    raw_accounts = data.get("accounts", {})
    if not isinstance(raw_accounts, dict):
        raise AccountError("[accounts] must be a table")

    accounts: dict[str, Account] = {}
    for handle, raw in raw_accounts.items():
        if not isinstance(raw, dict):
            raise AccountError(f"accounts.{handle} must be a table")
        provider = normalize_provider(str(raw.get("provider", "")))
        account_class = str(raw.get("class", "")).strip()
        if not account_class:
            raise AccountError(f"accounts.{handle}.class is required")
        home = str(raw["home"]).strip() if raw.get("home") else None
        if provider in {"claude", "codex"} and not home:
            raise AccountError(f"accounts.{handle}.home is required for {provider}")
        accounts[handle] = Account(
            handle=handle,
            provider=provider,
            account_class=account_class,
            home=home,
            email=str(raw["email"]).strip() if raw.get("email") else None,
            description=(
                str(raw["description"]).strip() if raw.get("description") else None
            ),
            config_source=(
                str(raw["config_source"]).strip() if raw.get("config_source") else None
            ),
        )

    for account in accounts.values():
        if account.config_source and account.config_source not in accounts:
            raise AccountError(
                f"accounts.{account.handle}.config_source references unknown account "
                f"{account.config_source!r}"
            )
        if account.config_source:
            source = accounts[account.config_source]
            if source.provider != account.provider:
                raise AccountError(
                    f"accounts.{account.handle}.config_source must use provider "
                    f"{account.provider!r}"
                )

    raw_rules = data.get("rules", [])
    if not isinstance(raw_rules, list):
        raise AccountError("[[rules]] entries must be an array of tables")
    rules: list[DirectoryRule] = []
    for index, raw in enumerate(raw_rules):
        if not isinstance(raw, dict):
            raise AccountError(f"rules[{index}] must be a table")
        rule_path = str(raw.get("path", "")).strip()
        account_class = str(raw.get("class", "")).strip()
        if not rule_path or not account_class:
            raise AccountError(f"rules[{index}] requires path and class")
        rules.append(DirectoryRule(path=rule_path, account_class=account_class))

    return Registry(accounts=accounts, rules=tuple(rules), path=path)


def _matching_rule(registry: Registry, cwd: Path) -> DirectoryRule | None:
    resolved_cwd = cwd.expanduser().resolve()
    matches: list[tuple[int, DirectoryRule]] = []
    for rule in registry.rules:
        rule_path = _expand_path(rule.path)
        if resolved_cwd == rule_path or rule_path in resolved_cwd.parents:
            matches.append((len(rule_path.parts), rule))
    if not matches:
        return None
    matches.sort(key=lambda item: item[0], reverse=True)
    return matches[0][1]


def _environment(account: Account) -> dict[str, str]:
    if not account.home:
        return {}
    home = str(_expand_path(account.home))
    if account.provider == "codex":
        return {"CODEX_HOME": home}
    if account.provider == "claude":
        return {"CLAUDE_CONFIG_DIR": home}
    return {}


def resolve_account(
    registry: Registry,
    *,
    provider: str,
    cwd: Path,
    account: str | None = None,
    account_class: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> Resolution:
    normalized_provider = normalize_provider(provider)
    env = environ if environ is not None else os.environ
    rule = _matching_rule(registry, cwd)

    selected_handle = account.strip() if account else None
    selected_class = account_class.strip() if account_class else None
    source = "explicit-account" if selected_handle else ""

    if not selected_handle and not selected_class:
        env_account = env.get("PO_ACCOUNT", "").strip()
        env_class = env.get("PO_ACCOUNT_CLASS", "").strip()
        if env_account:
            selected_handle = env_account
            source = "environment-account"
        elif env_class:
            selected_class = env_class
            source = "environment-class"

    if selected_handle:
        selected = registry.accounts.get(selected_handle)
        if selected is None:
            raise AccountError(f"account {selected_handle!r} is not registered")
        if selected.provider != normalized_provider:
            raise AccountError(
                f"account {selected_handle!r} uses provider {selected.provider!r}, "
                f"not {normalized_provider!r}"
            )
    else:
        if selected_class:
            if rule and rule.account_class != selected_class:
                raise AccountError(
                    f"account class {selected_class!r} conflicts with directory rule "
                    f"{rule.path!r} -> {rule.account_class!r}; use an explicit "
                    "account handle to override"
                )
            source = source or "explicit-class"
        elif rule:
            selected_class = rule.account_class
            source = "directory-rule"

        candidates = [
            candidate
            for candidate in registry.accounts.values()
            if candidate.provider == normalized_provider
            and (selected_class is None or candidate.account_class == selected_class)
        ]
        if not candidates:
            class_note = (
                f" in class {selected_class!r}" if selected_class is not None else ""
            )
            raise AccountError(
                f"no {normalized_provider} account registered{class_note}"
            )
        if len(candidates) > 1:
            handles = ", ".join(sorted(candidate.handle for candidate in candidates))
            raise AccountError(
                f"multiple {normalized_provider} accounts match: {handles}; "
                "select one explicitly"
            )
        selected = candidates[0]
        if not source:
            source = "unique-provider-account"

    return Resolution(
        handle=selected.handle,
        provider=selected.provider,
        account_class=selected.account_class,
        home=str(_expand_path(selected.home)) if selected.home else None,
        email=selected.email,
        source=source,
        environment=_environment(selected),
    )


def resolve_environment_for_backend(
    backend: object,
    *,
    cwd: Path,
    account: str | None = None,
    account_class: str | None = None,
    config_path: Path = CONFIG_PATH,
) -> Resolution | None:
    """Resolve an account for a known backend, preserving legacy behavior without config."""
    if not config_path.exists():
        return None
    name = type(backend).__name__.lower()
    if "codex" in name:
        provider = "codex"
    elif "cursor" in name:
        provider = "cursor"
    elif "claude" in name:
        provider = "claude"
    else:
        return None
    return resolve_account(
        load_registry(config_path),
        provider=provider,
        cwd=cwd,
        account=account,
        account_class=account_class,
    )


def _resolve_provider_executable(provider: str) -> str:
    """Return the first matching provider CLI executable on PATH."""
    normalized_provider = normalize_provider(provider)
    candidates = PROVIDER_EXECUTABLES.get(normalized_provider, (normalized_provider,))
    for executable_name in candidates:
        executable = shutil.which(executable_name)
        if executable is not None:
            return executable
    if len(candidates) == 1:
        missing = candidates[0]
    else:
        missing = " or ".join(repr(name) for name in candidates)
    raise AccountError(f"{missing} executable not found on PATH")


def launch_agent(
    provider: str,
    args: list[str],
    *,
    cwd: Path,
    account: str | None = None,
    account_class: str | None = None,
    config_path: Path = CONFIG_PATH,
) -> None:
    """Replace this process with a provider CLI under the resolved account."""
    normalized_provider = normalize_provider(provider)
    executable = _resolve_provider_executable(normalized_provider)

    resolution = resolve_account(
        load_registry(config_path),
        provider=normalized_provider,
        cwd=cwd,
        account=account,
        account_class=account_class,
    )
    environment = os.environ.copy()
    environment.update(resolution.environment)
    environment["PO_ACCOUNT"] = resolution.handle
    environment["PO_ACCOUNT_CLASS"] = resolution.account_class
    os.execvpe(executable, [executable, *args], environment)


def sync_shared_config(registry: Registry) -> list[tuple[Path, Path]]:
    """Link provider configuration while preserving account-owned state."""
    linked: list[tuple[Path, Path]] = []
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for account in registry.accounts.values():
        if not account.config_source:
            continue
        source = registry.accounts[account.config_source]
        if not source.home or not account.home:
            raise AccountError(
                f"account {account.handle!r} and its config source require homes"
            )
        source_home = _expand_path(source.home)
        target_home = _expand_path(account.home)
        target_home.mkdir(parents=True, exist_ok=True)
        backup_home = target_home / "backups" / f"shared-config-{timestamp}"

        for name in SHARED_CONFIG_PATHS.get(account.provider, ()):
            source_path = source_home / name
            target_path = target_home / name
            if not source_path.exists() and not source_path.is_symlink():
                continue
            if (
                target_path.is_symlink()
                and target_path.resolve() == source_path.resolve()
            ):
                continue
            if target_path.exists() or target_path.is_symlink():
                backup_home.mkdir(parents=True, exist_ok=True)
                shutil.move(str(target_path), str(backup_home / name))
            target_path.symlink_to(
                source_path, target_is_directory=source_path.is_dir()
            )
            linked.append((target_path, source_path))
    return linked


def _toml_string(value: str) -> str:
    return json.dumps(value)


def save_registry(registry: Registry) -> None:
    lines = ["version = 1", ""]
    for handle in sorted(registry.accounts):
        account = registry.accounts[handle]
        lines.append(f"[accounts.{_toml_string(handle)}]")
        lines.append(f"provider = {_toml_string(account.provider)}")
        lines.append(f"class = {_toml_string(account.account_class)}")
        if account.home:
            lines.append(f"home = {_toml_string(account.home)}")
        if account.email:
            lines.append(f"email = {_toml_string(account.email)}")
        if account.description:
            lines.append(f"description = {_toml_string(account.description)}")
        if account.config_source:
            lines.append(f"config_source = {_toml_string(account.config_source)}")
        lines.append("")
    for rule in registry.rules:
        lines.append("[[rules]]")
        lines.append(f"path = {_toml_string(rule.path)}")
        lines.append(f"class = {_toml_string(rule.account_class)}")
        lines.append("")
    registry.path.parent.mkdir(parents=True, exist_ok=True)
    registry.path.write_text("\n".join(lines))
    registry.path.chmod(0o600)


account_app = typer.Typer(
    no_args_is_help=True,
    help="Manage machine-local coding-agent accounts and directory policy.",
)


@account_app.command("list")
def list_accounts(
    output_json: bool = typer.Option(False, "--json", help="Output JSON."),
    config: Path = typer.Option(CONFIG_PATH, "--config", hidden=True),
) -> None:
    """List registered account metadata. Credentials are never displayed."""
    try:
        registry = load_registry(config)
    except AccountError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    rows = [
        {
            "handle": account.handle,
            "provider": account.provider,
            "class": account.account_class,
            "home": account.home,
            "email": account.email,
        }
        for account in sorted(registry.accounts.values(), key=lambda item: item.handle)
    ]
    if output_json:
        typer.echo(json.dumps(rows))
        return
    for row in rows:
        details = [row["provider"], row["class"]]
        if row["email"]:
            details.append(str(row["email"]))
        typer.echo(f"{row['handle']}: {' | '.join(details)}")


@account_app.command("resolve")
def resolve(
    provider: str = typer.Option(..., "--provider"),
    cwd: Path = typer.Option(Path.cwd(), "--cwd"),
    account: str | None = typer.Option(None, "--account"),
    account_class: str | None = typer.Option(None, "--account-class", "--account-type"),
    output_json: bool = typer.Option(False, "--json", help="Output JSON."),
    config: Path = typer.Option(CONFIG_PATH, "--config", hidden=True),
) -> None:
    """Resolve the provider account and environment for a working directory."""
    try:
        result = resolve_account(
            load_registry(config),
            provider=provider,
            cwd=cwd,
            account=account,
            account_class=account_class,
        )
    except AccountError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    if output_json:
        typer.echo(json.dumps(result.to_dict()))
        return
    typer.echo(f"{result.handle} ({result.provider}/{result.account_class})")
    typer.echo(f"source: {result.source}")
    for key, value in result.environment.items():
        typer.echo(f"{key}={value}")


@account_app.command("status")
def status(
    provider: str = typer.Option(..., "--provider"),
    cwd: Path = typer.Option(Path.cwd(), "--cwd"),
    account: str | None = typer.Option(None, "--account"),
    account_class: str | None = typer.Option(None, "--account-class"),
    output_json: bool = typer.Option(False, "--json", help="Output JSON."),
    config: Path = typer.Option(CONFIG_PATH, "--config", hidden=True),
) -> None:
    """Alias for `po account resolve`."""
    resolve(provider, cwd, account, account_class, output_json, config)


@account_app.command("add")
def add_account(
    handle: str = typer.Argument(...),
    provider: str = typer.Option(..., "--provider"),
    account_class: str = typer.Option(..., "--class"),
    home: str | None = typer.Option(None, "--home"),
    email: str | None = typer.Option(None, "--email"),
    description: str | None = typer.Option(None, "--description"),
    config_source: str | None = typer.Option(None, "--config-source"),
    config: Path = typer.Option(CONFIG_PATH, "--config", hidden=True),
) -> None:
    """Register account metadata and its provider-owned state directory."""
    try:
        registry = (
            load_registry(config)
            if config.exists()
            else Registry(accounts={}, rules=(), path=config)
        )
        if handle in registry.accounts:
            raise AccountError(f"account {handle!r} is already registered")
        normalized_provider = normalize_provider(provider)
        if normalized_provider in {"claude", "codex"} and not home:
            raise AccountError(f"--home is required for {normalized_provider}")
        accounts = dict(registry.accounts)
        accounts[handle] = Account(
            handle=handle,
            provider=normalized_provider,
            account_class=account_class,
            home=home,
            email=email,
            description=description,
            config_source=config_source,
        )
        save_registry(Registry(accounts=accounts, rules=registry.rules, path=config))
    except AccountError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"registered {handle}")


@account_app.command("sync-config")
def sync_config(
    config: Path = typer.Option(CONFIG_PATH, "--config", hidden=True),
) -> None:
    """Apply declarative shared-config links without sharing credentials or state."""
    try:
        links = sync_shared_config(load_registry(config))
    except AccountError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    if not links:
        typer.echo("shared account configuration is already synchronized")
        return
    for target, source in links:
        typer.echo(f"linked {target} -> {source}")


@account_app.command("rule-add")
def add_rule(
    path: str = typer.Argument(...),
    account_class: str = typer.Option(..., "--class"),
    config: Path = typer.Option(CONFIG_PATH, "--config", hidden=True),
) -> None:
    """Add a longest-path-wins directory classification rule."""
    try:
        registry = load_registry(config)
        rules = (*registry.rules, DirectoryRule(path=path, account_class=account_class))
        save_registry(
            Registry(accounts=registry.accounts, rules=rules, path=registry.path)
        )
    except AccountError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"registered rule {path} -> {account_class}")
