"""Drop privileges after making the mounted SQLite directory writable."""

from __future__ import annotations

import os
import sys

DATA_DIRECTORY = "/data"
APP_UID = 10001
APP_GID = 10001


def main() -> None:
    os.makedirs(DATA_DIRECTORY, exist_ok=True)
    if os.geteuid() == 0:
        # A new Docker volume is root-owned. Only its top-level directory needs
        # ownership changed; database files are created after the privilege drop.
        os.chown(DATA_DIRECTORY, APP_UID, APP_GID)
        os.setgid(APP_GID)
        os.setuid(APP_UID)
    if len(sys.argv) < 2:
        raise SystemExit("an application command is required")
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
