"""Download the SumatraPDF print engine into tools/.

Kept out of git: it's a 20MB GPLv3 binary, and vendoring it would both bloat the
history permanently and put us in the business of redistributing it from the repo.

    python fetch_tools.py
"""

import io
import urllib.request
import zipfile
from pathlib import Path

VERSION = "3.6.1"
ZIP = f"https://www.sumatrapdfreader.org/dl/rel/{VERSION}/SumatraPDF-{VERSION}-64.zip"
LICENSE = "https://raw.githubusercontent.com/sumatrapdfreader/sumatrapdf/master/COPYING"
TOOLS = Path(__file__).parent / "tools"

NOTICE = f"""SumatraPDF {VERSION} — https://www.sumatrapdfreader.org/
Copyright Krzysztof Kowalczyk and contributors.

Licensed under the GNU General Public License v3 (see SumatraPDF-COPYING.txt).
Source: https://github.com/sumatrapdfreader/sumatrapdf

This application calls SumatraPDF.exe as a separate program to send a PDF to a
printer. It is redistributed unmodified.
"""


def get(url, timeout=120):
    # the download host 403s urllib's default User-Agent
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def main():
    TOOLS.mkdir(exist_ok=True)
    print(f"downloading SumatraPDF {VERSION}…")
    z = zipfile.ZipFile(io.BytesIO(get(ZIP)))
    name = next(n for n in z.namelist() if n.lower().endswith(".exe"))
    (TOOLS / "SumatraPDF.exe").write_bytes(z.read(name))

    # GPLv3 obliges us to convey the licence and point at the source
    (TOOLS / "SumatraPDF-COPYING.txt").write_bytes(get(LICENSE, 60))
    (TOOLS / "SumatraPDF-NOTICE.txt").write_text(NOTICE, encoding="utf-8")

    size = (TOOLS / "SumatraPDF.exe").stat().st_size
    print(f"tools/SumatraPDF.exe  ({size / 1e6:.1f} MB)")
    print("tools/SumatraPDF-COPYING.txt, tools/SumatraPDF-NOTICE.txt")


if __name__ == "__main__":
    main()
