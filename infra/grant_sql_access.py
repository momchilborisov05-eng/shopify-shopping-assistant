"""
Give the app's managed identity its database rights, and nothing more.

Runs as you (Azure CLI login, which must be the server's Entra admin):

    python infra/grant_sql_access.py <server>.database.windows.net <database> --identity id-shopassist

The identity gets its own schema, shopassist, where it may create and change its tables
(conversations, tickets, analytics). It has no rights on any other schema in the database.
Running it again is safe: an existing user and grants are left as they are.
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant.storage import _connect_mssql  # noqa: E402

NAME = re.compile(r"[A-Za-z0-9_-]{1,128}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("server")
    parser.add_argument("database")
    parser.add_argument("--identity", required=True, help="the managed identity's name")
    args = parser.parse_args()
    if not NAME.fullmatch(args.identity):
        sys.exit(f"Unexpected name: {args.identity}")

    conn = _connect_mssql(
        f"Server=tcp:{args.server},1433;Database={args.database};Encrypt=yes;"
        "TrustServerCertificate=no;Authentication=ActiveDirectoryDefault"
    )
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM sys.database_principals WHERE name = ?", (args.identity,))
    if not cur.fetchone():
        cur.execute(f"CREATE USER [{args.identity}] FROM EXTERNAL PROVIDER")
    cur.execute("IF SCHEMA_ID('shopassist') IS NULL EXEC('CREATE SCHEMA shopassist')")
    cur.execute(f"GRANT CREATE TABLE TO [{args.identity}]")
    cur.execute(f"GRANT ALTER, SELECT, INSERT, UPDATE, DELETE, REFERENCES ON SCHEMA::shopassist TO [{args.identity}]")
    conn.commit()
    conn.close()
    print(f"'{args.identity}' can keep its tables in schema shopassist of {args.database}.")


if __name__ == "__main__":
    main()
