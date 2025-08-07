import json
import sqlite3
from contextlib import contextmanager
from typing import Dict, List, Optional

from loguru import logger

# 你的 Proxy 类也需要在这里被引用，或者移到一个公共的 model 文件中
# 为了简单，我们就在 main.py 中传递 Proxy 对象
from main import PROXY_POOL, Proxy, pool_lock

DATABASE_FILE = "proxies.db"


@contextmanager
def get_db_connection():
    """Context manager for handling database connections."""
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_FILE)
        yield conn
    finally:
        if conn:
            conn.close()


def initialize_database():
    """Creates the proxies table if it doesn't exist."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS proxies (
                url TEXT PRIMARY KEY,
                ssl_enabled_history TEXT NOT NULL,
                ssl_disabled_history TEXT NOT NULL
            )
        """)
        conn.commit()
    logger.info("DATABASE: Database initialized successfully.")


async def load_proxies_from_db():
    """Loads all proxies from the database into the in-memory pool at startup."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT url, ssl_enabled_history, ssl_disabled_history FROM proxies"
        )
        rows = cursor.fetchall()

        async with pool_lock:
            loaded_count = 0
            for row in rows:
                url, ssl_enabled_history_json, ssl_disabled_history_json = row
                if url not in PROXY_POOL:
                    proxy = Proxy(url)
                    # json.loads 将 JSON 字符串转换回 Python list
                    proxy.ssl_enabled_history.extend(
                        json.loads(ssl_enabled_history_json)
                    )
                    proxy.ssl_disabled_history.extend(
                        json.loads(ssl_disabled_history_json)
                    )
                    PROXY_POOL[url] = proxy
                    loaded_count += 1

    if loaded_count > 0:
        logger.success(f"DATABASE: Loaded {loaded_count} proxies from the database.")
    else:
        logger.info("DATABASE: No proxies found in the database to load.")


async def save_proxy_to_db(proxy: Proxy):
    """Saves or updates a single proxy's state in the database."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # json.dumps 将 deque (在转换为 list 后) 序列化为 JSON 字符串
        ssl_enabled_history_json = json.dumps(list(proxy.ssl_enabled_history))
        ssl_disabled_history_json = json.dumps(list(proxy.ssl_disabled_history))

        cursor.execute(
            """
            INSERT INTO proxies (url, ssl_enabled_history, ssl_disabled_history)
            VALUES (?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                ssl_enabled_history = excluded.ssl_enabled_history,
                ssl_disabled_history = excluded.ssl_disabled_history
        """,
            (proxy.url, ssl_enabled_history_json, ssl_disabled_history_json),
        )
        conn.commit()


async def remove_proxy_from_db(proxy_url: str):
    """Removes a proxy from the database."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM proxies WHERE url = ?", (proxy_url,))
        conn.commit()
