from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    config = Config(root / "alembic.ini")
    command.upgrade(config, "head")
