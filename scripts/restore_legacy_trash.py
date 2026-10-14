"""Explicit maintenance recovery for files retained by the retired trash feature."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.database import Database
from app.archive_trash import list_trash, restore_image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True, type=Path,
                        help='Existing catalog database; never inferred from a profile')
    parser.add_argument('--restore', metavar='TOKEN', help='Restore one existing retained exposure')
    args = parser.parse_args()
    if not args.database.is_file():
        parser.error('the specified database does not exist')
    db = Database(args.database)
    try:
        result = restore_image(db, args.restore) if args.restore else list_trash(db)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        db.conn.close()


if __name__ == '__main__':
    raise SystemExit(main())
