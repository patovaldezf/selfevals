"""Storage layer errors. Distinct from Pydantic ValidationError."""

from __future__ import annotations


class StorageError(Exception):
    """Base for all storage failures."""


class EntityNotFoundError(StorageError):
    """Asked for an entity by id but it doesn't exist (in this workspace)."""

    def __init__(self, entity_type: str, entity_id: str, workspace_id: str) -> None:
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.workspace_id = workspace_id
        super().__init__(f"{entity_type} {entity_id!r} not found in workspace {workspace_id!r}")


class ObjectNotFoundError(StorageError):
    """Object store pointer does not resolve to a stored blob."""

    def __init__(self, pointer: str) -> None:
        self.pointer = pointer
        super().__init__(f"object store pointer {pointer!r} not found")


class OptimisticConcurrencyError(StorageError):
    """Update attempted against a stale version."""

    def __init__(self, entity_type: str, entity_id: str, expected: int, found: int) -> None:
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.expected = expected
        self.found = found
        super().__init__(
            f"optimistic concurrency on {entity_type} {entity_id!r}: "
            f"caller had version {expected}, store has {found}"
        )


class WorkspaceMismatchError(StorageError):
    """Caller's workspace_id does not match the entity's workspace_id."""

    def __init__(self, expected: str, found: str) -> None:
        self.expected = expected
        self.found = found
        super().__init__(
            f"workspace isolation violated: expected {expected!r}, entity belongs to {found!r}"
        )


class PointerHashMismatchError(StorageError):
    """An object's stored bytes hash doesn't match its recorded content_hash."""

    def __init__(self, pointer: str, expected_hash: str, actual_hash: str) -> None:
        self.pointer = pointer
        self.expected_hash = expected_hash
        self.actual_hash = actual_hash
        super().__init__(
            f"pointer {pointer!r} integrity violation: expected {expected_hash}, got {actual_hash}"
        )


class IntegrityViolationError(StorageError):
    """Cross-cutting integrity violation (uniqueness, FK-shaped invariant, etc.)."""
