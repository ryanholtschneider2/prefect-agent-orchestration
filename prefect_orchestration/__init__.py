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
from prefect_orchestration.test_cache import (
    cache_get,
    cache_key,
    cache_put,
    compute_collection_hash,
    compute_scope_hash,
    compute_source_hash,
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
    "cache_get",
    "cache_key",
    "cache_put",
    "compute_collection_hash",
    "compute_scope_hash",
    "compute_source_hash",
    "format_self_block",
    "identity_vars",
    "load_identity",
    "resolve_role_env",
    "role_env_key",
]
