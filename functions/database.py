import os
import sys
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import QueuePool

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from functions.logger import logger


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

            logger("DATABASE", f"Executing query: {query} | params={params}", level="DEBUG")

            result = conn.execute(text(query), params)

            rows = result.mappings().all()

            logger("DATABASE", f"Fetched {len(rows)} rows from {table_name}", level="INFO")

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

            # allow single dict
            if isinstance(data, dict):
                data = [data]

            columns = data[0].keys()
            col_str = ", ".join(columns)
            val_str = ", ".join([f":{col}" for col in columns])

            query = f"INSERT INTO {table_name} ({col_str}) VALUES ({val_str})"

            logger("DATABASE", f"Executing insert into {table_name}", level="DEBUG")

            conn.execute(text(query), data)
            conn.commit()

            logger("DATABASE", f"Inserted {len(data)} rows into {table_name}", level="INFO")

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

            set_clauses = ", ".join([f"{col} = :{col}" for col in data.keys()])

            where_clauses = []
            params = dict(data)

            for i, (column, operator, value) in enumerate(conditions):
                key = f"cond{i}"
                where_clauses.append(f"{column} {operator} :{key}")
                params[key] = value

            query = f"UPDATE {table_name} SET {set_clauses} WHERE {' AND '.join(where_clauses)}"

            logger("DATABASE", f"Executing update: {query} | params={params}", level="DEBUG")

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

            logger("DATABASE", f"Executing delete: {query} | params={params}", level="DEBUG")

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

            logger("DATABASE", f"Executing query: {query} | params={params}", level="DEBUG")

            result = conn.execute(text(query), params or {})

            if result.returns_rows:
                rows = result.mappings().all()
                logger("DATABASE", f"Query returned {len(rows)} rows", level="INFO")
                return rows

            conn.commit()

            logger("DATABASE", "Query executed successfully", level="INFO")

            return None

        except SQLAlchemyError as e:
            logger("DATABASE", f"Query database error: {str(e)}", level="ERROR")
            raise

        except Exception as e:
            logger("DATABASE", f"Query unexpected error: {str(e)}", level="ERROR")
            raise

        finally:
            if conn:
                conn.close()