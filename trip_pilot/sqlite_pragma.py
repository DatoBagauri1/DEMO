from __future__ import annotations

import logging

from django.db.backends.signals import connection_created

logger = logging.getLogger(__name__)


def _configure_sqlite(connection) -> None:  # noqa: ANN001
    if connection.vendor != "sqlite":
        return
    with connection.cursor() as cursor:
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA busy_timeout=30000;")


def _sqlite_pragma_on_connect(sender, connection, **kwargs) -> None:  # noqa: ANN001, ARG001
    try:
        _configure_sqlite(connection)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to apply SQLite PRAGMA settings.")


connection_created.connect(_sqlite_pragma_on_connect, dispatch_uid="trip_pilot.sqlite_pragma")
