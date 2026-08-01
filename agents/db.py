"""Single ClickHouse client for the whole agents package.

All queries go through q() with server-side parameter binding — no string
interpolation of agent-provided values, ever.
"""

import os
from functools import lru_cache

import clickhouse_connect
from dotenv import load_dotenv

load_dotenv()


def configured() -> bool:
    return bool(os.getenv("CLICKHOUSE_HOST"))


@lru_cache(maxsize=1)
def client():
    return clickhouse_connect.get_client(
        host=os.environ["CLICKHOUSE_HOST"],
        port=int(os.getenv("CLICKHOUSE_PORT", "8443")),
        username=os.getenv("CLICKHOUSE_USER", "default"),
        password=os.environ["CLICKHOUSE_PASSWORD"],
    )


def q(sql: str, params: dict | None = None) -> list[dict]:
    """Run a query, return rows as list of dicts."""
    res = client().query(sql, parameters=params or {})
    return [dict(zip(res.column_names, row)) for row in res.result_rows]


def command(sql: str) -> None:
    client().command(sql)
