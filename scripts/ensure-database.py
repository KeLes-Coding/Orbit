from __future__ import annotations

import asyncio
import sys

import asyncpg
from sqlalchemy.engine import make_url

from app.core.config import settings


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


async def main() -> int:
    url = make_url(settings.database_url)
    database = url.database

    if not database:
        print("Database name is missing from ORBIT_DATABASE_URL.", file=sys.stderr)
        return 1

    maintenance_database = "postgres" if database != "postgres" else "template1"

    connection = await asyncpg.connect(
        user=url.username,
        password=url.password,
        host=url.host or "127.0.0.1",
        port=url.port or 5432,
        database=maintenance_database,
    )

    try:
        exists = await connection.fetchval(
            "select 1 from pg_database where datname = $1",
            database,
        )
        if exists:
            return 0

        await connection.execute(f"create database {quote_identifier(database)}")
        print(f"Created database: {database}")
        return 0
    finally:
        await connection.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
