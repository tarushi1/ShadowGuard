"""
Database connection and session handling.
================================================================

SQLite, via SQLAlchemy 2.0's synchronous API.

Why SQLite
----------
The whole system is designed to run on one machine: the backend is
`127.0.0.1:8000` and the only client is a browser extension on the same
laptop. Postgres would add a service to install, a process to keep
running, and credentials to manage, in exchange for concurrency this
deployment cannot use.

It is also a privacy property rather than only a convenience. One file,
on the user's own disk, with nothing crossing a network — see
docs/security.md. A hosted database would make the DLP tool itself the
largest aggregation of sensitive metadata in the organisation.

Why synchronous
---------------
FastAPI runs plain `def` handlers in a thread pool, which is already
the right treatment for this workload. `async` SQLAlchemy would mean a
second dialect, an async driver, and `await` on every query, to remove
blocking from an operation that takes about a tenth of a millisecond
against a local file.

Why lazy
--------
The engine is built on first use, not at import. Importing a module
should not create files on disk — that surprises the evaluation
harness, which imports the pipeline hundreds of times and must never
touch a database, and it makes the tests' temporary databases awkward
to arrange.
"""

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from ..models.event import Base

logger = logging.getLogger(__name__)

# Where the database lives, overridable for tests and for anyone who
# wants it somewhere else.
#
# The default sits under `data/` at the project root, which .gitignore
# already excludes. A database of decisions is local user data and
# committing it would be its own small incident.
DEFAULT_DB_PATH = Path(__file__).resolve().parents[3] / "data" / "shadowguard.db"

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def database_url() -> str:
    """The URL to connect to, read from the environment at call time.

    Read at CALL time rather than captured at import, so a test can set
    `SHADOWGUARD_DB_URL` and get a fresh database without having to
    control module import order.
    """
    return os.environ.get("SHADOWGUARD_DB_URL") or f"sqlite:///{DEFAULT_DB_PATH}"


def get_engine() -> Engine:
    """Build the engine once, on first use."""
    global _engine, _session_factory

    if _engine is not None:
        return _engine

    url = database_url()

    if url.startswith("sqlite:///") and ":memory:" not in url:
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)

    _engine = create_engine(
        url,
        # SQLite refuses a connection used from a thread other than the
        # one that created it. FastAPI runs sync handlers in a thread
        # pool, so a request is served by whichever worker is free, and
        # the default would raise on roughly every second request.
        # Safe here because SQLAlchemy's pool hands one connection to
        # one thread at a time.
        connect_args={"check_same_thread": False} if url.startswith("sqlite") else {},
        # Off deliberately. With echo on, every statement is logged —
        # and this project's central rule is that logs never carry
        # user content. No column holds prompt text, so nothing WOULD
        # leak, but a default that prints the database to stdout is one
        # schema change away from doing so.
        echo=False,
    )

    if url.startswith("sqlite"):
        _enable_sqlite_pragmas(_engine)

    _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)

    return _engine


def _enable_sqlite_pragmas(engine: Engine) -> None:
    """Turn on the two SQLite defaults that should not be defaults.

    FOREIGN KEYS are OFF in SQLite unless enabled per connection. With
    them off, `ondelete="CASCADE"` on the entity table is decorative:
    deleting an event during a retention sweep would leave its entity
    rows behind as orphans, and the "deleted" data would still be
    there. For a retention policy that is the whole ballgame.

    WAL lets a reader (the dashboard) and a writer (the request path)
    proceed at once instead of the reader blocking the write.
    """

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection, _record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


def create_tables() -> None:
    """Create anything missing. Safe to call repeatedly.

    Enough for a project of this size. A deployed system would use
    Alembic migrations, because `create_all` cannot ALTER an existing
    table — it silently leaves an outdated schema in place, which
    presents as a column that exists in the code and not in the file.
    That limitation belongs in the report rather than in a surprise.
    """
    Base.metadata.create_all(get_engine())


@contextmanager
def session_scope() -> Iterator[Session]:
    """A transactional session: commit on success, roll back on error.

    The context manager exists so no caller has to remember the
    rollback. A half-written event is worse than no event, because it
    is indistinguishable from a real one when someone later counts
    them.
    """
    get_engine()  # ensures _session_factory exists
    assert _session_factory is not None

    session = _session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_for_tests() -> None:
    """Drop the cached engine so the next call re-reads the URL.

    Test-only, and named so nobody mistakes it for an operational tool.
    """
    global _engine, _session_factory

    if _engine is not None:
        _engine.dispose()

    _engine = None
    _session_factory = None
