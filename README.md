# Proxy Aggregator Service

[![Python](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-green.svg)](https://fastapi.tiangolo.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A robust, self-maintaining proxy aggregator and testing service built with FastAPI. This service continuously pulls proxy lists, tests their health and stability, and exposes them through a clean API. With **SQLite persistence**, the service remembers proxy states across restarts, ensuring high availability.

## Features

-   **Automatic Proxy Sourcing**: Periodically fetches proxy lists from multiple pre-defined sources.
-   **Continuous Health Checking**: Asynchronously tests all proxies to measure their reliability.
-   **Stability Scoring**: Each proxy is given a stability score based on its recent test history.
-   **SSL/Non-SSL Testing**: Differentiates between proxies that can handle verified SSL connections (HTTPS) and those that cannot.
-   **Data Persistence**: Uses a local **SQLite** database to save the state of the proxy pool, enabling "hot restarts" without losing data.
-   **Dead Proxy Cleanup**: Automatically removes proxies that consistently fail health checks from memory and the database.
-   **Secure API Endpoints**: All data-providing endpoints are protected by a secret token.
-   **Flexible Output Formats**: Get proxies in `JSON`, `TXT`, or `CSV` format.
-   **Configurable via Environment**: Easily configure settings using a `.env` file.

## Project Structure

```
.
├── main.py             # The main FastAPI application file
├── database.py         # Module for all SQLite database operations
├── requirements.txt    # Python dependencies
├── .env.example        # Example environment file
├── .gitignore          # Git ignore configuration
├── proxies.db          # (Generated, ignored by Git) The SQLite database file
└── README.md           # This file
```

## Setup and Installation

Follow these steps to get the service running locally.

### 1. Prerequisites

-   Python 3.8+
-   A virtual environment tool (like `venv`)

### 2. Clone and Setup Environment

```bash
# Clone the repository (if you haven't already)
git clone <your-repo-url>
cd <your-repo-folder>

# Create and activate a Python virtual environment
python -m venv venv
# On Windows:
# .\venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate
```

### 3. Install Dependencies

Install all the required Python packages using the `requirements.txt` file.

```bash
pip install -r requirements.txt
```

The `requirements.txt` file should contain:
```txt
# Web Framework
fastapi
uvicorn[standard]

# HTTP Clients
requests
aiohttp
aiohttp-socks

# Configuration & Logging
pydantic-settings
python-dotenv
loguru
```

### 4. Configure the Service

Copy the example environment file to a new `.env` file. This file will hold your local configuration.

```bash
cp .env.example .env
```

Now, open the `.env` file and customize the settings. **It is crucial to change the `API_SECRET_TOKEN` to a secure, private value.**

**.env file contents:**
```dotenv
# -- Application Settings --
# How often to check for new proxy lists, in seconds.
SOURCE_PULL_INTERVAL=300

# How often to test all proxies in the pool, in seconds.
PROXY_TEST_INTERVAL=10

# Number of test results to keep for calculating stability.
PROXY_HISTORY_WINDOW_SIZE=12

# Minimum stability score (0.0 to 1.0) for a proxy to be included in the /proxies endpoint.
PROXY_MINIMUM_STABILITY=0.5

# -- Security --
# Secret token for accessing protected API endpoints. CHANGE THIS!
API_SECRET_TOKEN="change-this-super-secret-token"
```

### 5. Run the Application

Use `uvicorn` to run the FastAPI application. The `--reload` flag is useful for development as it automatically restarts the server when you change the code.

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```
The first time you run the service, it will create a `proxies.db` file to store its state.

## API Documentation

Once the server is running, you can access the interactive API documentation (powered by Swagger UI) at:

**`http://127.0.0.1:8000/docs`**

This interface allows you to explore and test all API endpoints directly from your browser.

## API Endpoints

All data endpoints require an `X-Token` header for authentication.

**Authentication Header:** `X-Token: <your-secret-token>`

---

### 1. Get Reliable Proxies

`GET /proxies`

Returns a list of proxies with a stability score greater than or equal to the `PROXY_MINIMUM_STABILITY` value set in your `.env` file (default is 0.5). Proxies are sorted from most to least stable.

#### Query Parameters:

-   `ssl_enabled` (boolean, **default: `true`**):
    -   `true`: Return proxies that successfully handle SSL-verified connections (HTTPS).
    -   `false`: Return proxies that work without SSL verification.
-   `limit` (integer, **default: `10`**, min: `1`, max: `20`): The maximum number of proxies to return.
-   `format` (string, **default: `json`**): The output format. Can be `json`, `txt`, or `csv`.

#### Example Request (using curl):

```bash
curl -X GET "http://127.0.0.1:8000/proxies?ssl_enabled=true&limit=5&format=json" \
     -H "X-Token: change-this-super-secret-token"
```

---

### 2. Get Perfectly Healthy Proxies

`GET /healthy-proxies`

Returns a list of proxies that have a perfect stability score of `1.0`. These are the most reliable proxies currently in the pool.

#### Query Parameters:

-   Same as `/proxies`.

#### Example Request:

```bash
# Get the top 3 most stable SSL proxies in plain text format
curl -X GET "http://127.0.0.1:8000/healthy-proxies?limit=3&format=txt" \
     -H "X-Token: change-this-super-secret-token"
```

---

### 3. Get Service Status

`GET /status`

Provides a quick overview of the service's health and the state of the proxy pool. This endpoint does **not** require an authentication token.

#### Example Request:

```bash
curl -X GET "http://127.0.0.1:8000/status"
```

## How It Works

The service operates on a continuous cycle of pulling, testing, and cleaning, with all states persisted in a SQLite database.

1.  **Startup & Hydration**: On launch, the service connects to the `proxies.db` file. It loads all previously saved proxies and their test histories into the in-memory `PROXY_POOL`. This ensures the service is immediately "hot" and ready to serve reliable proxies.
2.  **Puller**: The `background_worker` task fetches proxy lists every `SOURCE_PULL_INTERVAL` seconds. Any new proxy is added to both the in-memory pool and the SQLite database.
3.  **Tester**: The `background_tester` task tests each proxy's health every `PROXY_TEST_INTERVAL` seconds. After each test, the proxy's updated state (its new test history) is **saved back to the SQLite database**.
4.  **Scoring**: The in-memory proxy objects calculate a stability score based on their test history, which reflects the most up-to-date health status.
5.  **Cleaner**: After each test cycle, `cleanup_dead_proxies` removes any proxy that has consistently failed. The removal happens in **both the in-memory pool and the SQLite database**, ensuring the system stays clean.
6.  **API**: The FastAPI endpoints query the in-memory `PROXY_POOL` for maximum performance, filtering and sorting proxies based on their live stability scores to fulfill user requests.

This hybrid in-memory/database approach provides the best of both worlds: the high speed of in-memory reads for the API, and the reliability of database persistence for long-term state management.

## License

This project is licensed under the MIT License.

