"""
csp.py - write the site's Content-Security-Policy into webapp/_headers.

The CSP says which scripts a browser may run on our pages. Without one, any
script that got injected into a page (a team name carrying HTML, a poisoned
feed) would run with full access to the signed-in session. With it, only our
own files, the two CDNs we load from, and the exact inline scripts below run.

Each page keeps its main script inline; the policy allows each one by its
SHA-256, so editing a page script changes its hash. This tool recomputes them,
and deploy-webapp.yml runs it on every deploy (after the stamping step, which
only touches src= attributes), so a stale hash cannot ship. Inline event
handlers (onclick="...") can't be hashed safely, so the tool REFUSES to run
while any exist - use a delegated listener (see api.js) instead.

    python tools/csp.py          rewrite webapp/_headers
    python tools/csp.py --check  exit 1 if the committed _headers is stale
"""

import base64
import hashlib
import re
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "webapp"
HEADERS = WEB / "_headers"
MARK = "  Content-Security-Policy:"

INLINE_SCRIPT = re.compile(r"<script>(.*?)</script>", re.S)
INLINE_HANDLER = re.compile(r"""<[^>]*\son[a-z]+\s*=\s*["']""", re.I)

POLICY = [
    "default-src 'self'",
    "script-src 'self' {hashes} https://cdn.jsdelivr.net https://static.cloudflareinsights.com",
    # Tailwind classes need nothing, but api.js injects <style> (dark mode) and a
    # few elements set style="": inline styles can't run code, so allowed.
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data: https:",
    "connect-src 'self' https://potentialpro-api.onrender.com https://*.supabase.co "
    "wss://*.supabase.co https://cloudflareinsights.com",
    "worker-src 'self'",
    "manifest-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
]


def policy() -> str:
    hashes, handlers = set(), []
    for page in sorted(WEB.glob("*.html")) + sorted((WEB / "js").glob("*.js")):
        # Git checks files out with LF on the deploy runner; normalise so a
        # Windows working copy (CRLF) produces the same hashes as production.
        text = page.read_text(encoding="utf-8").replace("\r\n", "\n")
        handlers += [f"{page.name}: {m.group(0)[:60]}" for m in INLINE_HANDLER.finditer(text)]
        if page.suffix == ".html":
            for body in INLINE_SCRIPT.findall(text):
                digest = base64.b64encode(hashlib.sha256(body.encode("utf-8")).digest()).decode()
                hashes.add(f"'sha256-{digest}'")
    if handlers:
        sys.exit("inline event handlers are blocked by the CSP - use a delegated listener:\n  "
                 + "\n  ".join(handlers))
    return "; ".join(POLICY).format(hashes=" ".join(sorted(hashes)))


def main():
    text = HEADERS.read_text(encoding="utf-8")
    lines = text.split("\n")
    idx = [i for i, l in enumerate(lines) if l.startswith(MARK)]
    assert len(idx) == 1, f"expected one '{MARK.strip()}' line in {HEADERS}"
    lines[idx[0]] = f"{MARK} {policy()}"
    new = "\n".join(lines)
    # Cloudflare Pages ignores _headers lines over 2,000 characters - silently.
    assert len(lines[idx[0]]) < 1900, "CSP line near Cloudflare's 2,000-char limit - move a page script to a file"
    if "--check" in sys.argv:
        if new != text:
            sys.exit("webapp/_headers CSP is stale - run: python tools/csp.py")
        print("CSP up to date")
        return
    HEADERS.write_text(new, encoding="utf-8", newline="\n")
    print(f"CSP written: {new.count('sha256-')} inline script hashes")


if __name__ == "__main__":
    main()
