#!/usr/bin/env python
"""Create or reset a login for the approval page and dashboard.

  python scripts/create_user.py <username> [--role approver|viewer]

Prompts for the password (min 12 characters, not echoed). Stored as a scrypt hash in audit.db."""
import getpass, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()
import auth

def main():
    args = sys.argv[1:]
    if not args or args[0].startswith("-"):
        sys.exit(__doc__)
    role = args[args.index("--role") + 1] if "--role" in args else "approver"
    pw = getpass.getpass(f"Password for {args[0]} (min {auth.MIN_PASSWORD} chars): ")
    if pw != getpass.getpass("Repeat password: "):
        sys.exit("passwords do not match")
    try:
        auth.create_user(args[0], pw, role)
    except ValueError as e:
        sys.exit(str(e))
    print(f"user {args[0].strip().lower()} saved with role {role}")

if __name__ == "__main__":
    main()
