"""Prefect orchestration over Claude Code CLI — port of Gas City software-dev-pack.

The simplified single-turn primitive lives at ``prefect_orchestration.agent_step``;
import as ``from prefect_orchestration.agent_step import agent_step``. We don't
re-export it on the package root because the submodule and the function share
a name (binding the function on the package would shadow the submodule for
testing).
"""

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
