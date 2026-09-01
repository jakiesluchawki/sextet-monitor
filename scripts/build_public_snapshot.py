"""Build new public data in a disposable DB. Never export the private database."""
from __future__ import annotations

import json
import subprocess
import sys

from manage import ROOT, admin_url, compose


def main():
    destination = ROOT / "web/public/snapshot.json"
    try:
        result = compose(
            "run", "--rm", "--no-deps", "--env", "PUBLIC_BUILD_ADMIN_URL", "api",
            "python", "-m", "monitor.public_snapshot", capture=True,
            extra_env={"PUBLIC_BUILD_ADMIN_URL": admin_url("postgres")},
        )
        payload = result.stdout
        if not payload or len(payload) > 16 * 1024 * 1024:
            raise ValueError("Public snapshot size is outside the reviewed limit.")
        parsed = json.loads(payload)
        if parsed.get("format") != 1:
            raise ValueError("Invalid public snapshot format.")
        temporary = destination.with_suffix(".json.building")
        temporary.write_bytes(payload)
        temporary.replace(destination)
        print(json.dumps({"public_snapshot": str(destination.relative_to(ROOT)), "bytes": len(payload),
                          "events": len(parsed["events"]), "generated_at": parsed["generated_at"],
                          "sources": [{"id": item["id"], "status": item["status"]} for item in parsed["sources"]],
                          "private_database_exported": False}, ensure_ascii=False))
    except subprocess.CalledProcessError:
        print("Nie udało się zebrać publicznych źródeł w nowej bazie. Poprzedni plik pozostaje bez zmian.", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
