"""Developer-only promotion of an approved scientific knowledge bundle.

Inspect and preflight by default. Semantic writes require --apply.
The caller cannot select a memory backend, credentials, or SQL.
Not an MCP tool and not a Hermes skill.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cellertz.knowledge.promotion import promote_scientific_knowledge


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate an explicit scientific promotion bundle. "
            "Inspect-only unless --apply is set."
        )
    )
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Materialize the bundle after preflight succeeds.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = promote_scientific_knowledge(args.bundle, apply=args.apply)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
