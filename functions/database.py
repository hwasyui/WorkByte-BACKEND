import os
import sys
import json
from contextlib import contextmanager
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError, IntegrityError as _IntegrityError
from sqlalchemy.pool import QueuePool

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from functions.logger import logger


def _sanitize_log_params(params) -> dict:
    """Replace embedding vector values in params before logging to avoid huge log lines."""
    if not params:
        return params
    return {
        k: f"<vector>" if k == "vec" else v
        for k, v in (params.items() if isinstance(params, dict) else {})
    }


def _serialize_bind_value(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return value


def _build_insert(table_name, data):
    """(query, rows) for an INSERT. Shared by Database and Transaction so both
    speak identical SQL."""
    if isinstance(data, dict):
        data = [data]

    columns = data[0].keys()
    col_str = ", ".join(columns)
    val_str = ", ".join([f":{col}" for col in columns])
    query = f"INSERT INTO {table_name} ({col_str}) VALUES ({val_str})"

    rows = [{k: _serialize_bind_value(v) for k, v in row.items()} for row in data]
    return query, rows


def _build_update(table_name, data, conditions):
    """(query, params) for an UPDATE. Shared by Database and Transaction."""
    set_clauses = ", ".join([f"{col} = :{col}" for col in data.keys()])

    where_clauses = []
    params = {k: _serialize_bind_value(v) for k, v in data.items()}

    for i, (column, operator, value) in enumerate(conditions):
        key = f"cond{i}"
        where_clauses.append(f"{column} {operator} :{key}")
        params[key] = _serialize_bind_value(value)

    query = f"UPDATE {table_name} SET {set_clauses} WHERE {' AND '.join(where_clauses)}"
    return query, params


class Transaction:
    """Statement helpers bound to one connection inside an open transaction.

    Deliberately has no commit/rollback of its own - Database.transaction() owns
    the lifecycle, so callers cannot half-commit a unit of work.
    """

    def __init__(self, conn):
        self._conn = conn

    def insert_data(self, table_name, data):
        if not data:
            logger("DATABASE", "No data to insert", level="WARNING")
            return
        query, rows = _build_insert(table_name, data)
        logger("DATABASE", f"[tx] Executing insert into {table_name}", level="DEBUG")
        self._conn.execute(text(query), rows)

    def update_data(self, table_name, data, conditions):
        if not data:
            logger("DATABASE", "No data to update", level="WARNING")
            return
        query, params = _build_update(table_name, data, conditions)
        logger("DATABASE", f"[tx] Executing update: {query} | params={_sanitize_log_params(params)}", level="DEBUG")
        self._conn.execute(text(query), params)

    def execute_query(self, query, params=None):
        logger("DATABASE", f"[tx] Executing query: {query} | params={_sanitize_log_params(params)}", level="DEBUG")
        result = self._conn.execute(text(query), params or {})
        return result.mappings().all() if result.returns_rows else None


class Database:

    def __init__(self, db_user, db_password, db_host, db_port, db_name):
        try:
            self.conn_str = f"postgresql+psycopg2://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"

            self.engine = create_engine(
                self.conn_str,
                poolclass=QueuePool,
                pool_size=10,
                max_overflow=20,
                pool_pre_ping=True,
                pool_recycle=1800
            )

            logger("DATABASE", "Database engine established", level="INFO")

        except Exception:
            logger("DATABASE", "Failed to initialize database engine", level="ERROR")
            raise

    def get_connection(self):
        try:
            return self.engine.connect()
        except Exception:
            logger("DATABASE", "Database connection failed", level="ERROR")
            raise

    @contextmanager
    def transaction(self):
        """Run several statements as one unit of work.

        Every other method here opens its own connection and commits on the spot,
        so a multi-statement operation that failed partway left the earlier writes
        committed. That is what made a failed review submit unrecoverable: ratings
        and written content were already in, so the retry hit the ratings unique
        constraint and the review was stuck pending forever.

            with db.transaction() as tx:
                tx.insert_data("review_ratings", rows)
                tx.execute_query("UPDATE ...", params)

        Commits on clean exit, rolls back on any exception, and always closes.
        """
        conn = None
        trans = None
        try:
            conn = self.get_connection()
            trans = conn.begin()
            yield Transaction(conn)
            trans.commit()
            logger("DATABASE", "Transaction committed", level="DEBUG")
        except Exception as e:
            if trans is not None:
                trans.rollback()
                logger("DATABASE", f"Transaction rolled back: {str(e)}", level="ERROR")
            raise
        finally:
            if conn is not None:
                conn.close()

    def fetch_data(self, table_name, columns=None, conditions=None, limit=None, order_by=None):
        conn = None
        try:
            conn = self.get_connection()

            select_cols = ", ".join(columns) if columns else "*"
            query = f"SELECT {select_cols} FROM {table_name}"

            params = {}

            if conditions:
                where_clauses = []
                for i, (column, operator, value) in enumerate(conditions):
                    key = f"param{i}"
                    where_clauses.append(f"{column} {operator} :{key}")
                    params[key] = value

                query += " WHERE " + " AND ".join(where_clauses)

            if order_by:
                query += f" ORDER BY {order_by}"

            if limit:
                query += f" LIMIT {limit}"

            logger("DATABASE", f"Executing query: {query} | params={_sanitize_log_params(params)}", level="DEBUG")

            result = conn.execute(text(query), params)

            rows = result.mappings().all()

            logger("DATABASE", f"Fetched {len(rows)} rows from {table_name}", level="DEBUG")

            return rows

        except SQLAlchemyError as e:
            logger("DATABASE", f"Fetch database error: {str(e)}", level="ERROR")
            raise

        except Exception as e:
            logger("DATABASE", f"Fetch unexpected error: {str(e)}", level="ERROR")
            raise

        finally:
            if conn:
                conn.close()

    def insert_data(self, table_name, data):
        conn = None
        try:
            conn = self.get_connection()

            if not data:
                logger("DATABASE", "No data to insert", level="WARNING")
                return

            query, rows = _build_insert(table_name, data)

            logger("DATABASE", f"Executing insert into {table_name}", level="DEBUG")

            conn.execute(text(query), rows)
            conn.commit()

            logger("DATABASE", f"Inserted {len(rows)} rows into {table_name}", level="INFO")

        except SQLAlchemyError as e:
            logger("DATABASE", f"Insert database error: {str(e)}", level="ERROR")
            raise

        except Exception as e:
            logger("DATABASE", f"Insert unexpected error: {str(e)}", level="ERROR")
            raise

        finally:
            if conn:
                conn.close()

    def update_data(self, table_name, data, conditions):
        conn = None
        try:
            conn = self.get_connection()

            if not data:
                logger("DATABASE", "No data to update", level="WARNING")
                return

            query, params = _build_update(table_name, data, conditions)

            logger("DATABASE", f"Executing update: {query} | params={_sanitize_log_params(params)}", level="DEBUG")

            conn.execute(text(query), params)
            conn.commit()

            logger("DATABASE", f"Updated rows in {table_name}", level="INFO")

        except SQLAlchemyError as e:
            logger("DATABASE", f"Update database error: {str(e)}", level="ERROR")
            raise

        except Exception as e:
            logger("DATABASE", f"Update unexpected error: {str(e)}", level="ERROR")
            raise

        finally:
            if conn:
                conn.close()

    def delete_data(self, table_name, conditions):
        conn = None
        try:
            conn = self.get_connection()

            params = {}
            where_clauses = []

            for i, (column, operator, value) in enumerate(conditions):
                key = f"param{i}"
                where_clauses.append(f"{column} {operator} :{key}")
                params[key] = value

            query = f"DELETE FROM {table_name} WHERE {' AND '.join(where_clauses)}"

            logger("DATABASE", f"Executing delete: {query} | params={_sanitize_log_params(params)}", level="DEBUG")

            conn.execute(text(query), params)
            conn.commit()

            logger("DATABASE", f"Deleted rows from {table_name}", level="INFO")

        except SQLAlchemyError as e:
            logger("DATABASE", f"Delete database error: {str(e)}", level="ERROR")
            raise

        except Exception as e:
            logger("DATABASE", f"Delete unexpected error: {str(e)}", level="ERROR")
            raise

        finally:
            if conn:
                conn.close()

    def execute_query(self, query, params=None):
        conn = None
        try:
            conn = self.get_connection()

            logger("DATABASE", f"Executing query: {query} | params={_sanitize_log_params(params)}", level="DEBUG")

            result = conn.execute(text(query), params or {})

            # Check if this is a write operation (INSERT, UPDATE, DELETE)
            query_upper = query.strip().upper()
            is_write_query = query_upper.startswith(('INSERT', 'UPDATE', 'DELETE'))

            if result.returns_rows:
                rows = result.mappings().all()
                logger("DATABASE", f"Query returned {len(rows)} rows", level="DEBUG")

                # Commit write operations even if they return rows (e.g., INSERT ... RETURNING)
                if is_write_query:
                    conn.commit()
                    logger("DATABASE", "Write query executed and committed", level="DEBUG")
                
                return rows

            # For queries that don't return rows
            if is_write_query:
                conn.commit()
                logger("DATABASE", "Write query executed and committed", level="DEBUG")
            else:
                logger("DATABASE", "Query executed successfully", level="DEBUG")

            return None

        except _IntegrityError as e:
            if "ForeignKeyViolation" in type(e.__cause__).__name__:
                logger("DATABASE", f"Query FK violation (entity deleted): {str(e.__cause__).splitlines()[0]}", level="DEBUG")
            else:
                logger("DATABASE", f"Query database error: {str(e)}", level="ERROR")
            raise

        except SQLAlchemyError as e:
            logger("DATABASE", f"Query database error: {str(e)}", level="ERROR")
            raise

        except Exception as e:
            logger("DATABASE", f"Query unexpected error: {str(e)}", level="ERROR")
            raise

        finally:
            if conn:
                conn.close()