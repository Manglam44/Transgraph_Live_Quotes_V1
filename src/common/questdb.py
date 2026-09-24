from __future__ import annotations

import logging
import threading
import time
from typing import Sequence, Tuple

import psycopg2
from psycopg2.extras import execute_values

from src.common.settings import (
    QDB_DATABASE,
    QDB_HOST,
    QDB_ILP_PORT,
    QDB_PASSWORD,
    QDB_PORT,
    QDB_USER,
    USE_ILP_INGESTION,
)

from questdb.ingress import Protocol, Sender


log = logging.getLogger(__name__)


_COLUMN_TYPES: dict[str, str] = {
    "quote_time": "TIMESTAMP",
    "ticker_time": "TIMESTAMP",
    "ticker_time_zone": "SYMBOL",
    "mode": "SYMBOL",
    "asset_class": "SYMBOL",
    "instrument_type": "SYMBOL",
    "name": "SYMBOL",
    "symbol": "SYMBOL",
    "pair": "SYMBOL",
    "exchange": "SYMBOL",
    "expiry": "SYMBOL",
    "strike": "DOUBLE",
    "right": "SYMBOL",
    "underlying": "SYMBOL",
    "contract_type": "SYMBOL",
    "local_symbol": "SYMBOL",
    "bid": "DOUBLE",
    "ask": "DOUBLE",
    "last": "DOUBLE",
    "bid_size": "DOUBLE",
    "ask_size": "DOUBLE",
    "last_size": "DOUBLE",
    "source": "SYMBOL",   # ADD THIS LINE
}


_SYMBOL_COLUMNS = frozenset(
    col
    for col, column_type in _COLUMN_TYPES.items()
    if column_type == "SYMBOL"
)


def connect_questdb() -> "psycopg2.extensions.connection":
    """
    Connect to QuestDB using the PostgreSQL wire protocol.

    Used for:
      - Schema management / DDL
      - SQL queries
      - PG fallback ingestion

    Tick ingestion normally uses the ILP writer below because
    ILP is more suitable for high-volume market-data ingestion.
    """

    return psycopg2.connect(
        host=QDB_HOST,
        port=QDB_PORT,
        user=QDB_USER,
        password=QDB_PASSWORD,
        database=QDB_DATABASE,
    )


def ensure_table(
    conn,
    table_name: str,
    columns: Sequence[str],
) -> None:
    """
    Create the QuestDB table if it doesn't exist.

    Also attempts to add any newly introduced columns to an
    already-existing table.
    """

    col_defs = ", ".join(
        f"{col} {_COLUMN_TYPES[col]}"
        for col in columns
    )

    ddl = (
        f"CREATE TABLE IF NOT EXISTS {table_name} "
        f"({col_defs}) "
        f"TIMESTAMP(quote_time) "
        f"PARTITION BY DAY WAL"
    )

    try:
        cur = conn.cursor()
        cur.execute(ddl)
        conn.commit()
        cur.close()

    except Exception as exc:
        conn.rollback()

        raise RuntimeError(
            f'Could not auto-create table "{table_name}".\n'
            f"Run this SQL in QuestDB console "
            f"(http://localhost:9000):\n\n"
            f"  {ddl};\n"
        ) from exc

    # Ensure newly-added columns exist on existing tables.
    for col in columns:

        alter_sql = (
            f"ALTER TABLE {table_name} "
            f"ADD COLUMN {col} {_COLUMN_TYPES[col]}"
        )

        try:
            cur = conn.cursor()
            cur.execute(alter_sql)
            conn.commit()
            cur.close()

        except Exception as exc:
            conn.rollback()

            if "already exists" in str(exc).lower():
                continue

            raise RuntimeError(
                f'Could not ensure column "{col}" '
                f'on table "{table_name}".\n'
                f"Run this SQL in QuestDB console "
                f"(http://localhost:9000):\n\n"
                f"  {alter_sql};\n"
            ) from exc


class _PgBatchWriter:
    """
    Synchronous psycopg2 batch-insert writer.

    Used only when:

        USE_ILP_INGESTION=false

    ILP is recommended for sustained market-data ingestion.
    """

    def __init__(
        self,
        table_name: str,
        columns: Sequence[str],
        batch_size: int,
        flush_interval_seconds: float,
    ):
        self.conn = connect_questdb()

        self.table_name = table_name
        self.columns = tuple(columns)
        self.batch_size = batch_size
        self.flush_interval_seconds = flush_interval_seconds

        self.buffer: list[Tuple[object, ...]] = []

        self.last_flush_time = time.monotonic()

        ensure_table(
            self.conn,
            table_name,
            columns,
        )

        self.cursor = self.conn.cursor()

    def add(
        self,
        row: Tuple[object, ...],
    ) -> None:

        self.buffer.append(row)

        if len(self.buffer) >= self.batch_size:
            self.flush()

    def flush_if_due(self) -> None:

        if (
            self.buffer
            and (
                time.monotonic()
                - self.last_flush_time
                >= self.flush_interval_seconds
            )
        ):
            self.flush()

    def flush(self) -> None:

        if not self.buffer:
            return

        columns_sql = ", ".join(self.columns)

        try:

            execute_values(
                self.cursor,
                (
                    f"INSERT INTO {self.table_name} "
                    f"({columns_sql}) VALUES %s"
                ),
                self.buffer,
            )

            self.conn.commit()

            # log.info(
            #     "Inserted %s rows into %s (PG writer)",
            #     len(self.buffer),
            #     self.table_name,
            # )

        except Exception:

            self.conn.rollback()

            log.exception(
                "Batch insert into %s failed -- "
                "rows dropped: %s",
                self.table_name,
                len(self.buffer),
            )

        finally:

            self.buffer.clear()

            self.last_flush_time = time.monotonic()

    def close(self) -> None:

        try:
            self.flush()

        finally:

            try:
                self.cursor.close()
            except Exception:
                pass

            try:
                self.conn.close()
            except Exception:
                pass


class _IlpBatchWriter:
    """
    QuestDB ILP batch writer.

    Compatible with questdb Python client 2.x.

    Current installed client:

        questdb==2.0.4

    Connection:

        QuestDB ILP TCP
        host = QDB_HOST
        port = QDB_ILP_PORT
    """

    def __init__(
        self,
        table_name: str,
        columns: Sequence[str],
        batch_size: int,
        flush_interval_seconds: float,
    ):
        # ---------------------------------------------------------
        # Ensure database table exists using PostgreSQL wire
        # ---------------------------------------------------------

        pg_conn = connect_questdb()

        try:

            ensure_table(
                pg_conn,
                table_name,
                columns,
            )

        finally:

            pg_conn.close()

        # ---------------------------------------------------------
        # Writer configuration
        # ---------------------------------------------------------

        self.table_name = table_name
        self.columns = tuple(columns)
        self.batch_size = batch_size
        self.flush_interval_seconds = flush_interval_seconds

        self.buffer: list[Tuple[object, ...]] = []

        self.last_flush_time = time.monotonic()

        self._lock = threading.Lock()

        # ---------------------------------------------------------
        # QuestDB ILP Sender
        # ---------------------------------------------------------

        self._sender = Sender(
            Protocol.Tcp,
            QDB_HOST,
            QDB_ILP_PORT,
        )

        self._sender.establish()

        log.info(
            "QuestDB ILP sender established: %s:%s",
            QDB_HOST,
            QDB_ILP_PORT,
        )

    def add(
        self,
        row: Tuple[object, ...],
    ) -> None:

        with self._lock:

            self.buffer.append(row)

            due = (
                len(self.buffer)
                >= self.batch_size
            )

        if due:
            self.flush()

    def flush_if_due(self) -> None:

        with self._lock:

            due = (
                bool(self.buffer)
                and (
                    time.monotonic()
                    - self.last_flush_time
                    >= self.flush_interval_seconds
                )
            )

        if due:
            self.flush()

    def flush(self) -> None:

        # ---------------------------------------------------------
        # Move buffered rows out of shared buffer
        # ---------------------------------------------------------

        with self._lock:

            if not self.buffer:
                return

            rows, self.buffer = (
                self.buffer,
                [],
            )

        staged = 0

        # ---------------------------------------------------------
        # Convert each row into QuestDB ILP format
        # ---------------------------------------------------------

        for row in rows:

            symbols: dict[str, str] = {}
            fields: dict[str, object] = {}

            at = None

            for col, value in zip(
                self.columns,
                row,
            ):

                if value is None:
                    continue

                # QuestDB timestamp
                if col == "quote_time":

                    at = value

                    continue

                # QuestDB SYMBOL columns
                if col in _SYMBOL_COLUMNS:

                    symbols[col] = str(value)

                # Numeric / other columns
                else:

                    fields[col] = value

            # -----------------------------------------------------
            # Stage row into ILP sender
            # -----------------------------------------------------

            try:

                self._sender.row(
                    self.table_name,
                    symbols=symbols,
                    columns=fields,
                    at=at,
                )

                staged += 1

            except Exception:

                log.exception(
                    "Failed to stage row for %s via ILP; "
                    "row dropped",
                    self.table_name,
                )

        # ---------------------------------------------------------
        # Flush staged rows to QuestDB
        # ---------------------------------------------------------

        try:

            self._sender.flush()

            # log.info(
            #     "Inserted %s/%s rows into %s (ILP writer)",
            #     staged,
            #     len(rows),
            #     self.table_name,
            # )

        except Exception:

            log.exception(
                "ILP flush failed for %s -- "
                "reconnecting sender, batch dropped",
                self.table_name,
            )

            self._reconnect_sender()

        finally:

            self.last_flush_time = time.monotonic()

    def _reconnect_sender(self) -> None:
        """
        Recreate QuestDB ILP sender after a connection failure.

        questdb==2.0.4 does not provide Sender.connect().
        """

        try:

            self._sender.close()

        except Exception:

            pass

        # ---------------------------------------------------------
        # Create a fresh Sender
        # ---------------------------------------------------------

        self._sender = Sender(
            Protocol.Tcp,
            QDB_HOST,
            QDB_ILP_PORT,
        )

        self._sender.establish()

        log.info(
            "QuestDB ILP sender re-established: %s:%s",
            QDB_HOST,
            QDB_ILP_PORT,
        )

    def close(self) -> None:

        try:

            self.flush()

        finally:

            try:
                self._sender.close()

            except Exception:

                pass


def get_batch_writer(
    table_name: str,
    columns: Sequence[str],
    batch_size: int,
    flush_interval_seconds: float,
):
    """
    Return the configured QuestDB writer.

    ILP is enabled by default when:

        USE_ILP_INGESTION=true
    """

    if USE_ILP_INGESTION:

        return _IlpBatchWriter(
            table_name,
            columns,
            batch_size,
            flush_interval_seconds,
        )

    return _PgBatchWriter(
        table_name,
        columns,
        batch_size,
        flush_interval_seconds,
    )
