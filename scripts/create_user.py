"""Create a login (use this instead of the demo seed on a real deployment).

    python scripts/create_user.py alice --role admin
"""
import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.db.session import Database  # noqa: E402
from app.errors import ServiceError  # noqa: E402
from app.services import auth_service  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("username")
parser.add_argument("--role", choices=["admin", "viewer"], default="viewer")
args = parser.parse_args()

password = getpass.getpass("Password: ")
if password != getpass.getpass("Repeat password: "):
    sys.exit("Passwords do not match.")

db = Database(get_settings())
try:
    with db.session() as session:
        user = auth_service.create_user(session, args.username, password, args.role)
        print(f"Created {user.role} '{user.username}'.")
except ServiceError as exc:
    sys.exit(exc.detail)
finally:
    db.dispose()
