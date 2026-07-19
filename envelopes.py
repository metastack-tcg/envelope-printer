"""Print TCGplayer shipping exports onto #10 envelopes.

    python envelopes.py export.csv        -> envelopes.pdf, one envelope per page
    python envelopes.py --sample          -> one page of sample data, for a test feed

Layout: brand block top-left (logo, or a type wordmark, or nothing), return
address under it, orange tick marking the recipient block. Settings come from
config.py, not from this file.
"""

import argparse
import csv
import sys
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.graphics import renderPDF
from svglib.svglib import svg2rlg
from PIL import Image

import config

INK, ACCENT = HexColor("#15130E"), HexColor("#C2410C")
W, H = 9.5 * inch, 4.125 * inch  # #10 envelope, fed short-edge-first

SAMPLE = ("Tyler Rebello", "1232 WILBUR AVE", "", "SWANSEA", "MA", "02777-2135")
TO_X, TO_Y = 3.7 * inch, 2.0 * inch


def asset(*parts):
    """Resolve a bundled file, whether running from source or frozen by PyInstaller."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base.joinpath(*parts)


# Fraunces, static cuts instantiated from the OFL variable font at opsz=14,
# SOFT=0, WONK=0. See fonts/OFL.txt — it ships with the app.
_FONTS = asset("fonts")
for _n, _f in {"regular": "Regular", "italic": "Italic", "bold": "Bold"}.items():
    pdfmetrics.registerFont(TTFont(f"Brand-{_n}", str(_FONTS / f"Fraunces-{_f}.ttf")))


class Logo:
    """A logo placed on the envelope.

    Raster (PNG/JPG) is the robust path. SVG is best-effort: svglib raises on
    gradients and silently substitutes a font for live <text>, so anything but
    flat paths is a gamble — which is why the settings page warns on SVG.
    """

    def __init__(self, path, width_in):
        self.path = Path(path)
        self.width = width_in * inch
        self.is_svg = self.path.suffix.lower() == ".svg"
        (self._load_svg if self.is_svg else self._load_raster)()

    def _load_svg(self):
        d = svg2rlg(str(self.path))
        if d is None or not d.width or not d.height:
            raise ValueError("That SVG could not be read.")
        s = self.width / d.width
        d.scale(s, s)
        d.width, d.height = d.width * s, d.height * s
        self.drawing, self.height = d, d.height
        try:
            self.ink = tuple(d.getBounds())
        except NotImplementedError:
            # gradients/filters have no computable bounds — fall back to the full box
            self.ink = (0, 0, d.width, d.height)

    def _load_raster(self):
        im = Image.open(self.path)
        self.height = self.width * im.height / im.width
        box = im.getchannel("A").getbbox() if "A" in im.getbands() else None
        if box is None:
            self.ink = (0, 0, self.width, self.height)
        else:
            px0, py0, px1, py1 = box
            # image y runs down, PDF y runs up
            self.ink = (px0 / im.width * self.width,
                        self.height - py1 / im.height * self.height,
                        px1 / im.width * self.width,
                        self.height - py0 / im.height * self.height)
        self.reader = ImageReader(str(self.path))

    def draw(self, c, x, y):
        if self.is_svg:
            renderPDF.draw(self.drawing, c, x, y)
        else:
            c.drawImage(self.reader, x, y, self.width, self.height, mask="auto")


def load_logo(cfg):
    """Returns a Logo, or None if unset/unreadable — a bad logo must not stop a print run."""
    p = (cfg.get("logo_path") or "").strip()
    if not p or not Path(p).exists():
        return None
    try:
        return Logo(p, cfg.get("logo_width_in", 2.35))
    except Exception:
        return None


def rows(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if not (r.get("Address1") or "").strip():
                continue  # trailing blank rows in TCGplayer exports
            yield (
                f"{r['FirstName'].strip()} {r['LastName'].strip()}".strip(),
                r["Address1"].strip(),
                r["Address2"].strip(),
                r["City"].strip(),
                r["State"].strip(),
                r["PostalCode"].strip(),
            )


def brand_block(c, cfg, logo):
    """Draw the top-left brand block. Returns the first return-address baseline.

    Margins are measured to the logo's actual ink, not its bounding box — art
    often carries blank padding, which would push the mark visibly off-corner.
    """
    mx, mt = cfg["margin_x_in"] * inch, cfg["margin_top_in"] * inch
    if logo:
        ix0, iy0, _, iy1 = logo.ink
        ox, oy = mx - ix0, H - mt - iy1
        logo.draw(c, ox, oy)
        if cfg.get("logo_layout") == "hang":
            # tuck the address under the wordmark, clearing an icon to its left
            return (ox + cfg["wordmark_x"] * logo.width,
                    oy + cfg["baseline_y"] * logo.height - 0.2 * inch)
        return mx, oy + iy0 - 0.18 * inch

    name = (cfg.get("brand_name") or "").strip()
    if name:  # no logo file: set the name as a type wordmark
        size = 16
        c.setFillColor(INK)
        c.setFont("Brand-regular", size)
        base = H - mt - size * 0.78
        c.drawString(mx, base, name)
        return mx, base - 0.20 * inch
    return mx, H - mt - 10


def envelope(c, addr, cfg, logo):
    x, y = brand_block(c, cfg, logo)

    c.setFillColor(INK)
    c.setFont("Brand-regular", 9.5)
    for i, line in enumerate([s for s in cfg.get("return_address", []) if s.strip()]):
        c.drawString(x, y - i * 12.8, line)

    # orange tick: clear of the USPS barcode zone and of any fold
    c.setFillColor(ACCENT)
    c.rect(TO_X - 0.22 * inch, TO_Y - 0.42 * inch, 0.045 * inch, 0.7 * inch, stroke=0, fill=1)

    name, a1, a2, city, st, zp = addr
    lines = [name, a1] + ([a2] if a2 else []) + [f"{city}, {st}  {zp}"]
    c.setFillColor(INK)
    for i, line in enumerate(lines):
        c.setFont("Brand-bold" if i == 0 else "Brand-regular", 13)
        c.drawString(TO_X, TO_Y - i * 17.9, line.upper())


def render(out, addrs, cfg=None):
    """Page is Com-10 portrait (4.125 x 9.5) to match the driver exactly: an MP tray
    maxes out around 8.5in wide, so a #10 can only feed short-edge-first. The layout
    is authored landscape and rotated onto it, so "Actual size" needs no auto-rotate
    and can't silently scale. Upside down? Turn the stack — it's a loading choice."""
    cfg = cfg or config.active(config.load())  # cfg is one flat preset, not the file
    logo = load_logo(cfg)
    c = pdfcanvas.Canvas(str(out), pagesize=(H, W))
    for a in addrs:
        c.saveState()
        c.translate(H, 0)
        c.rotate(90)
        envelope(c, a, cfg, logo)
        c.restoreState()
        c.showPage()
    c.save()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("csv", nargs="?")
    p.add_argument("--sample", action="store_true", help="one page of fake data, for a test feed")
    p.add_argument("-o", "--out")
    a = p.parse_args()

    if a.sample:
        out = Path(a.out or "sample.pdf")
        render(out, [SAMPLE])
        print(f"sample -> {out}")
        return

    if not a.csv:
        p.error("give a CSV, or --sample")
    addrs = list(rows(a.csv))
    if not addrs:
        sys.exit("no addresses in that CSV")
    out = Path(a.out or "envelopes.pdf")
    render(out, addrs)
    print(f"{len(addrs)} envelopes -> {out}")


def demo():
    tmp = Path("_demo.csv")
    tmp.write_text(
        "Order #,FirstName,LastName,Address1,Address2,City,State,PostalCode\n"
        "1,Tyler,Rebello,1232 WILBUR AVE,,SWANSEA,MA,02777-2135\n"
        ",,,,,,,\n"
    )
    got = list(rows(tmp))
    tmp.unlink()
    assert got == [SAMPLE], got

    # Logo ink bounds: art usually carries blank padding, and image y runs down
    # while PDF y runs up — get that flip wrong and every logo sits off-corner.
    png = Path("_demo.png")
    im = Image.new("RGBA", (400, 200), (0, 0, 0, 0))
    im.paste((0, 0, 0, 255), (40, 20, 360, 120))  # 10% pad left/right, 10% top, 40% bottom
    im.save(png)
    lg = Logo(png, 2.0)
    png.unlink()
    W, Hh = 2.0 * inch, 1.0 * inch
    x0, y0, x1, y1 = lg.ink
    assert abs(lg.height - Hh) < 0.01, lg.height
    assert abs(x0 - 0.1 * W) < 0.5, x0                 # left pad 40/400
    assert abs(x1 - 0.9 * W) < 0.5, x1                 # right pad 40/400
    assert abs(y1 - (Hh - 0.1 * Hh)) < 0.5, y1         # ink TOP <- image top pad
    assert abs(y0 - (Hh - 0.6 * Hh)) < 0.5, y0         # ink BOTTOM <- image bottom
    print("ok")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
    else:
        main()
