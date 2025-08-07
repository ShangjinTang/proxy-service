# Proxy Aggregator Service

A self-maintaining proxy aggregator and testing service built with FastAPI. It continuously finds, tests, and serves reliable proxies through a clean, fast API. State is persisted in a local SQLite database for instant availability after restarts.

## Features

- **Automatic Sourcing**: Fetches proxies from multiple sources.
- **Continuous Health Checks**: Scores proxies based on stability and SSL support.
- **Persistent State**: Uses SQLite to remember proxies across restarts.
- **Fast & Secure API**: Built with FastAPI, protected by a secret token.
- **Flexible Output**: Provides proxies in `JSON`, `TXT`, or `CSV`.

## Quickstart

### 1. Prerequisites

- Python 3.8+
- [uv](https://github.com/astral-sh/uv) (a fast Python package installer)

### 2. Setup

```bash
# Create a virtual environment using uv
uv venv

# Activate the environment
# On macOS/Linux:
source .venv/bin/activate
# On Windows:
# .venv\Scripts\activate

# Install dependencies using uv
uv pip install -r requirements.txt
```

### 3. Configuration

Copy the example environment file and set your own secret token.

```bash
cp .env.example .env
# Now, edit .env and change the API_SECRET_TOKEN
```

**`.env` file:**

```dotenv
SOURCE_PULL_INTERVAL=300
PROXY_TEST_INTERVAL=10
PROXY_HISTORY_WINDOW_SIZE=12
PROXY_MINIMUM_STABILITY=0.5
API_SECRET_TOKEN="change-this-super-secret-token"
```

### 4. Run the Service

Use `uv run` to start the web server. It will automatically use the virtual environment.

```bash
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

The service is now running at `http://127.0.0.1:8000`. The interactive API docs are at `http://127.0.0.1:8000/docs`.

## API Usage

All data endpoints require an `X-Token` header.

**Header:** `X-Token: <your-secret-token>`

---

### `GET /proxies`

Get reliable proxies (stability >= `PROXY_MINIMUM_STABILITY`, default 0.5).

**Example:** Get 5 reliable SSL proxies in `txt` format.

```bash
curl "http://127.0.0.1:8000/proxies?limit=5&format=txt" \
     -H "X-Token: change-this-super-secret-token"
```

---

### `GET /healthy-proxies`

Get perfectly stable proxies (stability == 1.0).

**Example:** Get the top 3 healthy proxies.

```bash
curl "http://127.0.0.1:8000/healthy-proxies?limit=3" \
     -H "X-Token: change-this-super-secret-token"
```

---

### `GET /status`

Get a quick overview of the service. No token required.

**Example:**

```bash
curl http://127.0.0.1:8000/status
```

## How It Works

1. **Hydrate**: On startup, loads proxy states from `proxies.db`.
2. **Pull**: A background task fetches new proxies and adds them to the pool.
3. **Test**: Another task continuously tests proxies for stability and SSL support.
4. **Persist**: All state changes (new proxies, test results, removals) are saved to the SQLite database in real-time.
5. **Serve**: The API reads from the fast in-memory pool to serve requests instantly.

## License

MIT
