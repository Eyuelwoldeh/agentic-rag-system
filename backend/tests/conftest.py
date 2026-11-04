"""Point the app at a fresh throwaway database (rag_test) before anything imports app.db."""
import os
import pathlib

import psycopg
from cryptography.fernet import Fernet
from dotenv import dotenv_values

env = dotenv_values(pathlib.Path(__file__).parents[2] / ".env")
admin_url = env["DATABASE_URL"]
base = admin_url.rsplit("/", 1)[0]
os.environ["DATABASE_URL"] = f"{base}/rag_test"
os.environ["READER_DATABASE_URL"] = env["READER_DATABASE_URL"].rsplit("/", 1)[0] + "/rag_test"
os.environ["SECRETS_KEY"] = Fernet.generate_key().decode()
os.environ["SYNC_INTERVAL_MINUTES"] = "0"

with psycopg.connect(admin_url, autocommit=True) as conn:
    conn.execute("DROP DATABASE IF EXISTS rag_test WITH (FORCE)")
    conn.execute("CREATE DATABASE rag_test")

from app.db import migrate  # noqa: E402

migrate()
