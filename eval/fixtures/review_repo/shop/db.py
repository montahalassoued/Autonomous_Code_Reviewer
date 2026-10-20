import sqlite3

DATABASE_PATH = "shop.db"


def get_connection():
    return sqlite3.connect(DATABASE_PATH)


def fetch_all(query, params=()):
    with get_connection() as connection:
        return connection.execute(query, params).fetchall()


def fetch_one(query, params=()):
    rows = fetch_all(query, params)
    return rows[0] if rows else None
