"""Engine/session construction for the metadata catalog.

Every function in `navigraph_catalog.api` takes a `Session` as a dependency
-- it never creates one internally. `session_scope` is the one place that
owns commit/rollback/close, so callers get a single, consistent pattern
regardless of which API function they're calling.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from navigraph_catalog.settings import MetadataCatalogSettings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine(settings: MetadataCatalogSettings | None = None) -> Engine:
    """Create (or return a cached) SQLAlchemy engine for the catalog database.

    Only caches when `settings` is omitted (i.e. the default, env-derived
    configuration), so tests that pass an explicit `settings` always get a
    fresh engine for that configuration.
    """

    global _engine

    if settings is not None:
        return create_engine(settings.sqlalchemy_url)

    if _engine is None:
        _engine = create_engine(MetadataCatalogSettings().sqlalchemy_url)
    return _engine


def get_session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    """Create (or return a cached) session factory bound to `engine`.

    Only caches when `engine` is omitted, so passing an explicit engine
    (e.g. a test's in-memory engine) always yields a factory bound to that
    exact engine.
    """

    global _session_factory

    if engine is not None:
        return sessionmaker(bind=engine, expire_on_commit=False)

    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _session_factory


@contextmanager
def session_scope(
    session_factory: sessionmaker[Session] | None = None,
) -> Generator[Session, None, None]:
    """Yield a `Session`, committing on success and rolling back on error.

    This is the one place in the package that commits or rolls back a
    transaction -- every function in `navigraph_catalog.api` takes an
    already-open `Session` and leaves commit/rollback to its caller, which
    in real usage is this context manager.
    """

    factory = session_factory or get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
