"""Import poll records from the `data` branch into the local database.

Run this on the laptop whenever you want fresh data for analysis:

    .venv\\Scripts\\python scripts\\import_raw.py

What it does:
1. `git fetch origin data` to download new records from GitHub.
2. Reads every raw/... file straight from the fetched branch with
   `git archive`, so nothing is checked out and your working folder is
   untouched.
3. Loads records into data/pogoh.db. Polls already in the database are
   skipped, so it is safe to run as often as you like.

Use --no-fetch to import from what was fetched last time (works offline).
"""

import argparse
import io
import subprocess
import sys
import tarfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from pogoh.db import DEFAULT_DB_PATH, connect  # noqa: E402
from pogoh.raw import decode_record, import_records  # noqa: E402

DATA_REF = "refs/remotes/origin/data"


def git(*args, repo=PROJECT_ROOT):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True).stdout


def read_records_from_branch(ref=DATA_REF, repo=PROJECT_ROOT):
    """Yield every record stored under raw/ on the given branch."""
    tree = git("ls-tree", "--name-only", ref, repo=repo).decode().split()
    if "raw" not in tree:
        return  # branch exists but has no polls yet
    archive = git("archive", "--format=tar", ref, "raw", repo=repo)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
        for member in tar:
            if member.isfile() and member.name.endswith(".json.gz"):
                yield decode_record(tar.extractfile(member).read())


def main():
    parser = argparse.ArgumentParser(description="Import raw poll records into the local database")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="SQLite file to write to")
    parser.add_argument("--no-fetch", action="store_true", help="skip git fetch")
    args = parser.parse_args()

    if not args.no_fetch:
        git("fetch", "--quiet", "origin", "data")

    conn = connect(args.db)
    try:
        imported, skipped = import_records(conn, read_records_from_branch())
        n_ok, n_fail = conn.execute("SELECT SUM(ok), SUM(1 - ok) FROM polls").fetchone()
    finally:
        conn.close()

    print(f"Imported {imported} new polls, skipped {skipped} already stored.")
    print(f"Database now has {n_ok or 0} successful and {n_fail or 0} failed polls.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
