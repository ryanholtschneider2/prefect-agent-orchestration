"""Prefect orchestration over Claude Code CLI — port of Gas City software-dev-pack."""

from prefect_orchestration.identity import (
    Identity,
    IdentityLoadError,
    format_self_block,
    identity_vars,
    load_identity,
)
from prefect_orchestration.secrets import (
    DEFAULT_PREFIXES,
    ChainSecretProvider,
    DotenvSecretProvider,
    EnvSecretProvider,
    SecretProvider,
    resolve_role_env,
    role_env_key,
)

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_PREFIXES",
    "ChainSecretProvider",
    "DotenvSecretProvider",
    "EnvSecretProvider",
    "Identity",
    "IdentityLoadError",
    "SecretProvider",
    "format_self_block",
    "identity_vars",
    "load_identity",
    "resolve_role_env",
    "role_env_key",
]
