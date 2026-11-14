"""
tools/icon_subset.py - load only the Material Symbols the site actually uses.

WHY (8 Oct 2026). Every page linked the WHOLE Material Symbols Outlined
variable font: 1,137,972 bytes, the heaviest thing on the site by far, for the
~40 icons we show. On a Nigerian mobile connection that is seconds of a blank
icon slot on every first visit. Google Fonts can subset it: `&icon_names=a,b,c`
returns a font with only those glyphs. With the SAME axes as before (wght
100..700, FILL 0..1, so nothing renders differently) it is ~70 KB: 94% less.

THE RISK, and why this is a script and not a hand-edited list: an icon missing
from the subset renders as its ligature TEXT ("check_circle"). A list someone
has to remember to update will eventually be wrong (bug #15 - three times).
So the list is DERIVED, never written by hand:

    every token in webapp/*.html + webapp/js/*.js
      INTERSECT  the official icon names (tools/material_symbols_names.txt)

That is a superset - "home" or "check" in prose gets included too, costing a
few hundred bytes each - but it cannot MISS an icon whose name appears in the
source, including the ones built in JS (`${won ? 'check_circle' : 'cancel'}`).
The one thing it cannot see is a name assembled from pieces ("trending_" + x);
like the Tailwind rule in docs/history.md, never build an icon name from parts.

deploy-webapp.yml runs this on every deploy (and warns if the committed HTML
was stale), the same pattern as the app.css rebuild.

Usage:
    python tools/icon_subset.py           # rewrite the font links in webapp/*.html
    python tools/icon_subset.py --check   # exit 1 if any page is out of date

Refresh the official name list (only needed to use an icon newer than it):
    curl -sL "https://raw.githubusercontent.com/google/material-design-icons/master/variablefont/MaterialSymbolsOutlined%5BFILL%2CGRAD%2Copsz%2Cwght%5D.codepoints" | cut -d' ' -f1 | sort -u > tools/material_symbols_names.txt
"""

import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEBAPP = os.path.join(ROOT, "webapp")
NAMES = os.path.join(ROOT, "tools", "material_symbols_names.txt")

# Matches the stylesheet link in every page, with or without an existing
# icon_names= list. The axes are kept exactly as they were. Pages spell the
# separator both ways ("&amp;" and a bare "&"); each keeps its own.
LINK = re.compile(
    r"(https://fonts\.googleapis\.com/css2\?family=Material\+Symbols\+Outlined:"
    r"wght,FILL@100\.\.700,0\.\.1)(?:(&(?:amp;)?)icon_names=[a-z0-9_,]*)?((&(?:amp;)?)display=[a-z]+)")


def used_icons() -> list:
    official = set(open(NAMES, encoding="utf-8").read().split())
    tokens = set()
    for path in glob.glob(os.path.join(WEBAPP, "*.html")) + glob.glob(os.path.join(WEBAPP, "js", "*.js")):
        # Drop our own previous list first, or every icon it names would count
        # as "used" forever and the subset could only ever grow.
        text = re.sub(r"icon_names=[a-z0-9_,]*", "", open(path, encoding="utf-8").read())
        tokens |= set(re.findall(r"[a-z][a-z0-9_]*[a-z0-9]", text))
    # Google requires the list sorted.
    return sorted(tokens & official)


def main() -> int:
    check = "--check" in sys.argv
    names = ",".join(used_icons())
    stale = []
    for path in sorted(glob.glob(os.path.join(WEBAPP, "*.html"))):
        text = open(path, encoding="utf-8").read()
        new = LINK.sub(lambda m: f"{m.group(1)}{m.group(4)}icon_names={names}{m.group(3)}", text)
        if new != text:
            stale.append(os.path.basename(path))
            if not check:
                with open(path, "w", encoding="utf-8", newline="") as fh:
                    fh.write(new)
    print(f"{names.count(',') + 1} icons; "
          + (f"{'out of date' if check else 'updated'}: {', '.join(stale)}" if stale else "all pages current"))
    return 1 if (check and stale) else 0


if __name__ == "__main__":
    sys.exit(main())
