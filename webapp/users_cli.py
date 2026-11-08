#!/usr/bin/env python3
"""Accounts from the command line, for the machine's administrator (the web app does the same from its Users page).

  users_cli.py list
  users_cli.py add <name> [--admin]          asks for the password (they change it at their first sign-in)
  users_cli.py reset <name>                  a new temporary password; the user must change it at the next sign-in
  users_cli.py admin <name>                  makes the user an administrator
  users_cli.py enable <name> | disable <name>
  users_cli.py setup-code                    prints (or makes) the one-time code that creates the first administrator

The state folder is SG_WEB_STATE (default ~/.spritegen-web), the web app's own. Run it as the user the web app runs as."""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import accounts as acc  # noqa: E402


def ask_password(confirm: bool = True) -> str:
    pw = getpass.getpass("Password: ")
    if confirm and getpass.getpass("Again: ") != pw:
        raise acc.AccountError("the two passwords differ")
    return pw


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    state = Path(os.environ.get("SG_WEB_STATE", Path.home() / ".spritegen-web"))
    state.mkdir(parents=True, exist_ok=True)
    a = acc.Accounts(state)
    cmd, args = argv[0], argv[1:]
    try:
        if cmd == "list":
            for u in a.listing():
                flags = [
                    x
                    for x, on in (
                        ("admin", u["role"] == "admin"),
                        ("disabled", u["disabled"]),
                        ("must change", u["must_change"]),
                    )
                    if on
                ]
                print(f"{u['username']:<20} {', '.join(flags)}")
        elif cmd == "add" and args:
            a.create(args[0], ask_password(), role="admin" if "--admin" in args else "user", must_change=True, by="cli")
            print(f"{args[0]} added")
        elif cmd == "reset" and args:
            a.set_password(acc.clean_username(args[0]), ask_password(), must_change=True, by="cli")
            print(f"{args[0]}: password reset; they must choose a new one at the next sign-in")
        elif cmd == "admin" and args:
            a.update(acc.clean_username(args[0]), role="admin", by="cli")
            print(f"{args[0]} is an administrator")
        elif cmd in ("enable", "disable") and args:
            a.update(acc.clean_username(args[0]), disabled=cmd == "disable", by="cli")
            print(f"{args[0]} {cmd}d")
        elif cmd == "setup-code":
            code = a.setup_code()
            print(code if code else "an account exists already: no setup code")
        else:
            print(__doc__, file=sys.stderr)
            return 2
    except acc.AccountError as e:
        print(f"error: {e.message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
