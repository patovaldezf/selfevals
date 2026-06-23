"""Per-entity relational mappers.

Each persistent entity type has an :class:`EntityMapper` that knows how to
decompose its Pydantic model into typed rows (main table + child tables) and
reassemble rows back into the model — with no generic catch-all ``entities``
table. Genuinely schemaless fields (free-form ``dict[str, Any]`` config spaces,
provider metadata, JSON schemas) are stored as JSONB columns, validated by
Pydantic at the application layer.

``MAPPERS`` is the registry the storage scope dispatches through, keyed by the
entity class name (the same tag the old generic table used).
"""

from __future__ import annotations

# Importing the entity modules registers their mappers as a side effect.
from selfevals.storage.postgres.mappers import agent as _agent  # noqa: F401
from selfevals.storage.postgres.mappers import agent_fleet as _agent_fleet  # noqa: F401
from selfevals.storage.postgres.mappers import annotation as _annotation  # noqa: F401
from selfevals.storage.postgres.mappers import dataset as _dataset  # noqa: F401
from selfevals.storage.postgres.mappers import dataset_baseline as _dataset_baseline  # noqa: F401
from selfevals.storage.postgres.mappers import decision_record as _decision_record  # noqa: F401
from selfevals.storage.postgres.mappers import eval_case as _eval_case  # noqa: F401
from selfevals.storage.postgres.mappers import experiment as _experiment  # noqa: F401
from selfevals.storage.postgres.mappers import failure_mode as _failure_mode  # noqa: F401
from selfevals.storage.postgres.mappers import feature_registry as _feature_registry  # noqa: F401
from selfevals.storage.postgres.mappers import grader_card as _grader_card  # noqa: F401
from selfevals.storage.postgres.mappers import iteration_record as _iteration_record  # noqa: F401
from selfevals.storage.postgres.mappers import risk_registry as _risk_registry  # noqa: F401
from selfevals.storage.postgres.mappers import run_job as _run_job  # noqa: F401
from selfevals.storage.postgres.mappers import tool as _tool  # noqa: F401
from selfevals.storage.postgres.mappers import trace as _trace  # noqa: F401
from selfevals.storage.postgres.mappers import workspace as _workspace  # noqa: F401
from selfevals.storage.postgres.mappers.base import EntityMapper, register_mapper
from selfevals.storage.postgres.mappers.registry import MAPPERS, mapper_for, mapper_for_name

__all__ = [
    "MAPPERS",
    "EntityMapper",
    "mapper_for",
    "mapper_for_name",
    "register_mapper",
]
