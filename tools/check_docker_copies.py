"""
check_docker_copies.py - Does the API image actually contain what the API imports?

Why this exists
---------------
deploy/render/Dockerfile copies source files by NAME, one allowlist line per
package:

    COPY models/__init__.py models/markets.py models/leagues.py ... ./models/

Adding a module the API imports without adding it here produces a container
that dies on import. Render then keeps serving the PREVIOUS image, so the site
stays up, the API answers, and the new field is simply absent — the deploy
"succeeded" and the feature silently did not ship.

This has now happened three times:
  * models/leagues.py     - league_display missing in production
  * webapp/               - the mount silently did nothing; Render's root 404'd
                            for months
  * models/gradeability.py - /api/stats returned no `coverage` block

Three occurrences of one mistake is not carelessness, it is a missing check.
The rule "remember to update the Dockerfile" has been written down since the
first occurrence and did not prevent the second or the third. So check it.

The check is static on purpose — no Docker, no build, runs in a second. It
walks the first-party imports reachable from api/server.py and asserts each
one is copied. That is exactly the failure being prevented, and nothing more.

Usage:
    python tools/check_docker_copies.py        (exit 1 on a missing file)
"""

import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCKERFILE = os.path.join(ROOT, "deploy", "render", "Dockerfile")
ENTRY = os.path.join(ROOT, "api", "server.py")

# Packages whose modules are copied file-by-file. A package NOT listed here is
# either copied wholesale (webapp/) or not shipped at all.
TRACKED = ("api", "db", "models", "scrapers", "analysis")


def copied_files() -> set:
    """Every repo-relative path the Dockerfile copies."""
    with open(DOCKERFILE, encoding="utf-8") as fh:
        text = fh.read()
    # Join line continuations before parsing.
    text = re.sub(r"\\\s*\n", " ", text)
    out = set()
    for line in text.splitlines():
        line = line.strip()
        if not line.upper().startswith("COPY "):
            continue
        parts = line.split()[1:]
        parts = [p for p in parts if not p.startswith("--")]
        if len(parts) < 2:
            continue
        for src in parts[:-1]:              # last token is the destination
            out.add(src.rstrip("/"))
    return out


def first_party_imports(path: str, seen: set) -> set:
    """Modules under TRACKED packages reachable from `path`, transitively."""
    rel = os.path.relpath(path, ROOT).replace("\\", "/")
    if rel in seen or not os.path.exists(path):
        return seen
    seen.add(rel)
    try:
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
    except (OSError, SyntaxError):
        return seen

    targets = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            targets.append(node.module)
        elif isinstance(node, ast.Import):
            targets += [n.name for n in node.names]

    for mod in targets:
        top = mod.split(".")[0]
        if top not in TRACKED:
            continue
        child = os.path.join(ROOT, *mod.split(".")) + ".py"
        first_party_imports(child, seen)
    return seen


def main() -> int:
    copied = copied_files()
    needed = first_party_imports(ENTRY, set())

    missing = []
    for rel in sorted(needed):
        pkg = rel.split("/")[0]
        if pkg not in TRACKED:
            continue
        # Copied by name, or the whole directory taken wholesale.
        if rel in copied or pkg in copied or f"{pkg}/" in copied:
            continue
        missing.append(rel)

    print(f"api/server.py reaches {len(needed)} first-party modules; "
          f"Dockerfile copies {len(copied)} paths")

    if missing:
        print("\nMISSING from deploy/render/Dockerfile — the API image would "
              "crash on import, and Render would keep serving the OLD image "
              "while reporting nothing wrong:")
        for rel in missing:
            print(f"  - {rel}")
        print("\nAdd them to the matching COPY line in "
              "deploy/render/Dockerfile.")
        return 1

    print("OK - every module the API imports is present in the image.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
