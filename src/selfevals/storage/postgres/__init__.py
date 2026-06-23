"""Postgres storage backend (relational-canonical).

Public surface is :class:`PostgresStorage`; importing this package keeps the
historical ``from selfevals.storage.postgres import PostgresStorage`` path
working after the monolithic module was split into a package.
"""

from selfevals.storage.postgres.storage import PostgresStorage

__all__ = ["PostgresStorage"]
