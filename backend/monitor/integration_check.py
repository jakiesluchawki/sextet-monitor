"""Create, test and remove one isolated database. Never truncate the live database."""
from __future__ import annotations
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from .config import Settings
from .db import load_countries


def main():
    settings = Settings.from_env()
    base_url = make_url(settings.database_url)
    name = "monitor_test_" + secrets.token_hex(6)
    admin = create_engine(base_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text('CREATE DATABASE "' + name + '"'))
    test_url = base_url.set(database=name)
    original_url = os.environ.get("DATABASE_URL")
    try:
        os.environ["DATABASE_URL"] = test_url.render_as_string(hide_password=False)
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
        command.upgrade(config, "head")
        test_engine = create_engine(test_url)
        with test_engine.begin() as conn:
            load_countries(conn, Path(settings.data_dir) / "countries.geojson")
        test_engine.dispose()
        env = os.environ.copy()
        env["TEST_DATABASE_URL"] = test_url.render_as_string(hide_password=False)
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=short",
             "tests/test_storage.py", "tests/test_identity_review.py", "tests/test_ingestion.py"],
            cwd=Path(__file__).resolve().parents[1], env=env,
        )
        print(json.dumps({"isolated_postgis_tests": result.returncode == 0, "database": name,
                          "live_database_modified": False}), flush=True)
        if result.returncode:
            raise SystemExit(result.returncode)
    finally:
        if original_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = original_url
        with admin.connect() as conn:
            conn.execute(text('DROP DATABASE "' + name + '" WITH (FORCE)'))
        admin.dispose()


if __name__ == "__main__":
    main()
