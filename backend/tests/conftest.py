"""Point the app at a fresh throwaway database (rag_test) before anything imports app.db."""
import os
import pathlib

import psycopg
from cryptography.fernet import Fernet
from dotenv import dotenv_values

env = dotenv_values(pathlib.Path(__file__).parents[2] / ".env")
admin_url = env["DATABASE_URL"]
base = admin_url.rsplit("/", 1)[0]
TEST_DB = os.environ.get("TEST_DB_NAME", "rag_test")  # parallel test runs need different names
os.environ["DATABASE_URL"] = f"{base}/{TEST_DB}"
os.environ["READER_DATABASE_URL"] = env["READER_DATABASE_URL"].rsplit("/", 1)[0] + f"/{TEST_DB}"
os.environ["SECRETS_KEY"] = Fernet.generate_key().decode()
os.environ["SYNC_INTERVAL_MINUTES"] = "0"
os.environ["SCHEDULES_DISABLED"] = "1"

with psycopg.connect(admin_url, autocommit=True) as conn:
    conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
    conn.execute(f'CREATE DATABASE "{TEST_DB}"')

from app.db import migrate  # noqa: E402

migrate()
