# ==============================================================================
# PROXY AGGREGATOR SERVICE (WITH SQLITE PERSISTENCE)
# ==============================================================================

import asyncio
import sys
import time
from collections import deque
from contextlib import asynccontextmanager
from typing import List

import aiohttp
import aiohttp_socks
import requests
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import PlainTextResponse, StreamingResponse
from loguru import logger
from pydantic_settings import BaseSettings

# ==============================================================================
# 1. Configuration and Models (放在顶部以便其他模块导入)
# ==============================================================================


class Settings(BaseSettings):
    SOURCE_PULL_INTERVAL: int = 300
    PROXY_TEST_INTERVAL: int = 10
    PROXY_HISTORY_WINDOW_SIZE: int = 12
    API_SECRET_TOKEN: str = "change-this-secret-token"
    PROXY_MINIMUM_STABILITY: float = 0.5

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()


class Proxy:
    def __init__(self, url: str):
        self.url = url
        self.ssl_enabled_history: deque[bool] = deque(
            maxlen=settings.PROXY_HISTORY_WINDOW_SIZE
        )
        self.ssl_disabled_history: deque[bool] = deque(
            maxlen=settings.PROXY_HISTORY_WINDOW_SIZE
        )

    def record_test(self, success: bool, *, ssl_enabled: bool):
        if ssl_enabled:
            self.ssl_enabled_history.append(success)
        else:
            self.ssl_disabled_history.append(success)

    @property
    def ssl_enabled_stability(self) -> float:
        if not self.ssl_enabled_history:
            return 0.0
        return sum(self.ssl_enabled_history) / len(self.ssl_enabled_history)

    @property
    def ssl_disabled_stability(self) -> float:
        if not self.ssl_disabled_history:
            return 0.0
        return sum(self.ssl_disabled_history) / len(self.ssl_disabled_history)

    def get_stability(self, *, ssl_enabled: bool) -> float:
        return (
            self.ssl_enabled_stability if ssl_enabled else self.ssl_disabled_stability
        )

    def is_valid(self, *, ssl_enabled: bool) -> bool:
        history = self.ssl_enabled_history if ssl_enabled else self.ssl_disabled_history
        return any(history)

    def should_be_removed(self) -> bool:
        ssl_enabled_full_and_failed = len(
            self.ssl_enabled_history
        ) == settings.PROXY_HISTORY_WINDOW_SIZE and not self.is_valid(ssl_enabled=True)
        ssl_disabled_full_and_failed = len(
            self.ssl_disabled_history
        ) == settings.PROXY_HISTORY_WINDOW_SIZE and not self.is_valid(ssl_enabled=False)
        return ssl_enabled_full_and_failed and ssl_disabled_full_and_failed


# ==============================================================================
# 2. Global State and Logging
# ==============================================================================

logger.remove()
logger.add(
    sys.stderr,
    level="INFO",
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
)
logger.add(
    sys.stderr,
    level="DEBUG",
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>",
    filter=lambda record: record["level"].no < logger.level("INFO").no,
)

PROXY_SOURCES = [
    (
        "socks4",
        "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/socks4/data.json",
    ),
    (
        "socks5",
        "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/socks5/data.json",
    ),
]
TEST_URL = "https://www.cloudflare.com/cdn-cgi/trace"
SUCCESS_MARKER = "ip="

PROXY_POOL: dict[str, Proxy] = {}
source_etags: dict[str, str] = {}
pool_lock = asyncio.Lock()

# ✨ 导入数据库模块
import database

# ==============================================================================
# 3. Background Tasks (Modified for DB interaction)
# ==============================================================================


async def pull_source_proxies():
    logger.info("PULLER: Checking for new proxy lists...")
    new_urls: set[str] = set()
    # ... (requests logic is unchanged)
    for protocol, url in PROXY_SOURCES:
        headers = {"If-None-Match": source_etags.get(url, "")}
        try:
            response = requests.get(url, timeout=15, headers=headers)
            if response.status_code == 304:
                continue
            response.raise_for_status()
            source_etags[url] = response.headers.get("ETag", "")
            proxy_data = response.json()
            for item in proxy_data:
                new_urls.add(f"{protocol}://{item['ip']}:{item['port']}")
        except (requests.RequestException, ValueError):
            pass  # Simplified error handling

    if new_urls:
        added_count = 0
        tasks = []
        async with pool_lock:
            for url in new_urls:
                if url not in PROXY_POOL:
                    proxy = Proxy(url)
                    PROXY_POOL[url] = proxy
                    # ✨ 保存新代理到数据库
                    tasks.append(database.save_proxy_to_db(proxy))
                    added_count += 1

        if tasks:
            await asyncio.gather(*tasks)

        if added_count > 0:
            logger.info(
                f"PULLER: Added {added_count} new candidate proxies to the pool and database."
            )
        else:
            logger.info("PULLER: Fetched list contained no new proxies.")


async def test_single_proxy(proxy: Proxy, *, ssl_enabled: bool):
    connector = aiohttp_socks.ProxyConnector.from_url(proxy.url)
    success = False
    try:
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(TEST_URL, ssl=ssl_enabled, timeout=10) as response:
                success = (
                    response.status == 200 and SUCCESS_MARKER in await response.text()
                )
    except Exception:
        success = False  # Ensure success is False on any exception

    async with pool_lock:
        proxy.record_test(success, ssl_enabled=ssl_enabled)
        # ✨ 测试后，异步更新数据库
        await database.save_proxy_to_db(proxy)


async def test_all_proxies():
    logger.info(f"TESTER: Starting test cycle for {len(PROXY_POOL)} proxies.")
    async with pool_lock:
        proxies_to_test = list(PROXY_POOL.values())
    tasks = [test_single_proxy(p, ssl_enabled=True) for p in proxies_to_test]
    tasks.extend(test_single_proxy(p, ssl_enabled=False) for p in proxies_to_test)
    await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("TESTER: Test cycle complete.")


async def cleanup_dead_proxies():
    async with pool_lock:
        dead_proxies_urls = [
            url for url, proxy in PROXY_POOL.items() if proxy.should_be_removed()
        ]

    if dead_proxies_urls:
        db_cleanup_tasks = []
        async with pool_lock:
            for url in dead_proxies_urls:
                if url in PROXY_POOL:
                    del PROXY_POOL[url]
                    # ✨ 从数据库删除
                    db_cleanup_tasks.append(database.remove_proxy_from_db(url))

        if db_cleanup_tasks:
            await asyncio.gather(*db_cleanup_tasks)

        logger.warning(
            f"CLEANER: Removed {len(dead_proxies_urls)} dead proxies from pool and database."
        )


# ... (background_worker and background_tester are unchanged)
async def background_worker():
    while True:
        await pull_source_proxies()
        await asyncio.sleep(settings.SOURCE_PULL_INTERVAL)


async def background_tester():
    while True:
        await test_all_proxies()
        await cleanup_dead_proxies()
        await asyncio.sleep(settings.PROXY_TEST_INTERVAL)


# ==============================================================================
# 4. FastAPI Application (Modified Lifespan)
# ==============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Application starting up...")
    # ✨ 初始化数据库并加载数据
    database.initialize_database()
    await database.load_proxies_from_db()

    # Start background tasks
    asyncio.create_task(background_worker())
    asyncio.create_task(background_tester())
    yield
    logger.info("Application shutting down.")


app = FastAPI(lifespan=lifespan, title="Proxy Aggregator Service")


# ... (API Endpoints are unchanged, they read from the in-memory PROXY_POOL)
async def verify_token(x_token: str = Header(..., alias="X-Token")):
    if x_token != settings.API_SECRET_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing API Token")


async def _get_filtered_proxies(
    min_stability: float, ssl_enabled: bool, limit: int, format: str
):
    async with pool_lock:
        candidate_proxies = [
            p
            for p in PROXY_POOL.values()
            if p.get_stability(ssl_enabled=ssl_enabled) >= min_stability
        ]
        sort_key = lambda p: p.get_stability(ssl_enabled=ssl_enabled)
        sorted_proxies = sorted(candidate_proxies, key=sort_key, reverse=True)
        result_proxies = sorted_proxies[:limit]
    if not result_proxies:
        raise HTTPException(
            status_code=404, detail="No suitable proxies found for the given criteria."
        )
    if format == "json":
        response_data = []
        for p in result_proxies:
            protocol, rest = p.url.split("://")
            ip, port = rest.split(":")
            response_data.append(
                {"url": p.url, "protocol": protocol, "ip": ip, "port": port}
            )
        return response_data
    if format == "txt":
        return PlainTextResponse("\n".join([p.url for p in result_proxies]))
    if format == "csv":

        def csv_generator():
            yield "protocol,ip,port\n"
            for p in result_proxies:
                protocol, rest = p.url.split("://")
                ip, port = rest.split(":")
                yield f"{protocol},{ip},{port}\n"

        return StreamingResponse(
            csv_generator(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=proxies.csv"},
        )


@app.get(
    "/proxies",
    dependencies=[Depends(verify_token)],
    summary="Get a list of reliable proxies",
)
async def get_proxies(
    ssl_enabled: bool = Query(True),
    limit: int = Query(10, ge=1, le=20),
    format: str = Query("json", enum=["json", "txt", "csv"]),
):
    return await _get_filtered_proxies(
        min_stability=settings.PROXY_MINIMUM_STABILITY,
        ssl_enabled=ssl_enabled,
        limit=limit,
        format=format,
    )


@app.get(
    "/healthy-proxies",
    dependencies=[Depends(verify_token)],
    summary="Get a list of the most stable (100%) proxies",
)
async def get_healthy_proxies(
    ssl_enabled: bool = Query(True),
    limit: int = Query(10, ge=1, le=20),
    format: str = Query("json", enum=["json", "txt", "csv"]),
):
    return await _get_filtered_proxies(
        min_stability=1.0, ssl_enabled=ssl_enabled, limit=limit, format=format
    )


@app.get("/status", summary="Get the current status of the proxy pool")
async def get_status():
    async with pool_lock:
        total_proxies = len(PROXY_POOL)
        proxies_above_threshold = sum(
            1
            for p in PROXY_POOL.values()
            if p.ssl_enabled_stability >= settings.PROXY_MINIMUM_STABILITY
        )
        perfectly_healthy_proxies = sum(
            1 for p in PROXY_POOL.values() if p.ssl_enabled_stability == 1.0
        )
    return {
        "status": "running",
        "timestamp": time.time(),
        "total_proxies_in_pool": total_proxies,
        "proxies_above_threshold_count": proxies_above_threshold,
        "perfectly_healthy_proxies_count": perfectly_healthy_proxies,
        "minimum_stability_threshold": settings.PROXY_MINIMUM_STABILITY,
    }


# ==============================================================================
# Entry Point
# ==============================================================================
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
