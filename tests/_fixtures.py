"""Module-defined Prefect flow for deployment tests.

`flow.to_deployment` refuses flows declared interactively / in `__main__`,
so test fixtures that need a real `RunnerDeployment` import from here.
"""

from __future__ import annotations

from prefect import flow


@flow
def sample_flow(x: int = 1) -> int:
    return x + 1
