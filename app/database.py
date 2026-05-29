import mysql.connector
from mysql.connector import pooling
from flask import current_app
import os

# This will hold our connection pool (created once when app starts)
_pool = None


def init_db(app):
    """
    Called once at app startup.
    Reads config from environment and creates a connection pool.
    """
    global _pool

    pool_config = {
        "pool_name":       "track_act_pool",
        "pool_size":       5,          # Max 5 simultaneous DB connections
        "pool_reset_session": True,
        "host":            app.config["DB_HOST"],
        "port":            int(app.config["DB_PORT"]),
        "database":        app.config["DB_NAME"],
        "user":            app.config["DB_USER"],
        "password":        app.config["DB_PASSWORD"],
    }

    _pool = mysql.connector.pooling.MySQLConnectionPool(**pool_config)
    print(f"[DB] Connection pool created → {app.config['DB_NAME']} on {app.config['DB_HOST']}")


def get_db():
    """
    Borrow a connection from the pool.
    Always use this inside a 'with' block so it's returned automatically.
    """
    if _pool is None:
        raise RuntimeError("Database pool is not initialised. Did you call init_db()?")
    return _pool.get_connection()


def query(sql, params=None, fetchone=False, commit=False):
    """
    A universal helper to run any SQL statement.

    - sql      : your SQL string, use %s for placeholders
    - params   : tuple of values to safely inject (prevents SQL injection)
    - fetchone : True → return one row dict, False → return list of row dicts
    - commit   : True → for INSERT / UPDATE / DELETE
    """
    connection = get_db()
    try:
        cursor = connection.cursor(dictionary=True)  # rows come back as dicts
        cursor.execute(sql, params or ())

        if commit:
            connection.commit()
            return cursor.rowcount        # how many rows were affected

        if fetchone:
            return cursor.fetchone()      # single dict or None
        return cursor.fetchall()          # list of dicts

    except mysql.connector.Error as e:
        if commit:
            connection.rollback()         # undo partial writes on error
        raise e

    finally:
        cursor.close()
        connection.close()               # returns connection back to pool