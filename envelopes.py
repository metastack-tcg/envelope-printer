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

SAMPLE = ("Tyler Rebello", "1232 WILBUR AVE", "", "SWANSEA", "MA", "02777-2135", "US")
TO_X, TO_Y = 3.7 * inch, 2.0 * inch

# USPS wants the full country name in English as the last line. ISO codes from
# TCGplayer's Country column; an unlisted code prints as-is rather than dropping.
DOMESTIC = {"US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA", ""}
COUNTRY_NAMES = {
    "CA": "CANADA", "GB": "UNITED KINGDOM", "UK": "UNITED KINGDOM",
    "AU": "AUSTRALIA", "NZ": "NEW ZEALAND", "JP": "JAPAN", "DE": "GERMANY",
    "FR": "FRANCE", "IT": "ITALY", "ES": "SPAIN", "NL": "NETHERLANDS",
    "BE": "BELGIUM", "AT": "AUSTRIA", "CH": "SWITZERLAND", "SE": "SWEDEN",
    "NO": "NORWAY", "DK": "DENMARK", "FI": "FINLAND", "IE": "IRELAND",
    "PT": "PORTUGAL", "PL": "POLAND", "CZ": "CZECH REPUBLIC", "GR": "GREECE",
    "HU": "HUNGARY", "RO": "ROMANIA", "SG": "SINGAPORE", "HK": "HONG KONG",
    "TW": "TAIWAN", "KR": "SOUTH KOREA", "MX": "MEXICO", "BR": "BRAZIL",
    "AR": "ARGENTINA", "CL": "CHILE", "MY": "MALAYSIA", "TH": "THAILAND",
    "PH": "PHILIPPINES", "ID": "INDONESIA", "IN": "INDIA", "IL": "ISRAEL",
    "ZA": "SOUTH AFRICA", "AE": "UNITED ARAB EMIRATES",
}


def asset(*parts):
    """Resolve a bundled file, whether running from source or frozen by PyInstaller."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base.joinpath(*parts)


# Fraunces ships with the app (static cuts instantiated from the OFL variable
# font at opsz=14, SOFT=0, WONK=0 — see fonts/OFL.txt). The rest are the Windows
# faces that carry a real bold; each pair is verified before being offered.
WINDOWS_FONTS = Path(r"C:\Windows\Fonts")
# (regular, bold, italic, bold-italic) — a missing cut falls back, see use_font
FONT_FILES = {
    "Fraunces": ("Fraunces-Regular.ttf", "Fraunces-Bold.ttf", "Fraunces-Italic.ttf", None),
    "Georgia": ("georgia.ttf", "georgiab.ttf", "georgiai.ttf", "georgiaz.ttf"),
    "Times New Roman": ("times.ttf", "timesbd.ttf", "timesi.ttf", "timesbi.ttf"),
    "Garamond": ("GARA.TTF", "GARABD.TTF", "GARAIT.TTF", None),
    "Book Antiqua": ("BKANT.TTF", "ANTQUAB.TTF", None, "BKANTBI.TTF"),
    "Palatino Linotype": ("pala.ttf", "palab.ttf", "palai.ttf", "palabi.ttf"),
    "Constantia": ("constan.ttf", "constanb.ttf", "constani.ttf", "constanz.ttf"),
    "Cambria": ("cambria.ttc", "cambriab.ttf", "cambriai.ttf", "cambriaz.ttf"),
    "Arial": ("arial.ttf", "arialbd.ttf", "ariali.ttf", "arialbi.ttf"),
    "Calibri": ("calibri.ttf", "calibrib.ttf", "calibrii.ttf", "calibriz.ttf"),
    "Verdana": ("verdana.ttf", "verdanab.ttf", "verdanai.ttf", "verdanaz.ttf"),
    "Tahoma": ("tahoma.ttf", "tahomabd.ttf", None, None),
    "Segoe UI": ("segoeui.ttf", "segoeuib.ttf", "segoeuii.ttf", "segoeuiz.ttf"),
    "Trebuchet MS": ("trebuc.ttf", "trebucbd.ttf", "trebucit.ttf", "trebucbi.ttf"),
}
DEFAULT_FONT = "Fraunces"
CUTS = ("", "b", "i", "bi")     # index order matches FONT_FILES tuples
_registered = {}


def _font_path(name, i):
    f = FONT_FILES[name][i]
    if f is None:
        return None
    return asset("fonts", f) if name == DEFAULT_FONT else WINDOWS_FONTS / f


def use_font(name):
    """Register a family on demand; returns {cut: reportlab name} for '', b, i, bi.
    A cut this machine lacks falls back (bi -> b -> regular), and a family it
    lacks entirely falls back to the bundled font, so a preset always prints."""
    if name not in FONT_FILES:
        name = DEFAULT_FONT
    if name in _registered:
        return _registered[name]
    got = {}
    for i, cut in enumerate(CUTS):
        p = _font_path(name, i)
        if p is None or not p.exists():
            continue
        tag = f"F:{name}:{cut}" if cut else f"F:{name}"
        try:
            pdfmetrics.registerFont(TTFont(tag, str(p)))
            got[cut] = tag
        except Exception:
            pass
    if "" not in got:                       # no regular: this family is unusable
        if name == DEFAULT_FONT:
            raise RuntimeError("bundled font missing")
        return use_font(DEFAULT_FONT)
    got.setdefault("b", got[""])
    got.setdefault("i", got[""])
    got.setdefault("bi", got.get("b", got[""]))
    _registered[name] = got
    return got


def available_fonts():
    """Families this machine can actually render, checked once."""
    out = []
    for name in FONT_FILES:
        p = _font_path(name, 0)
        if p and p.exists():
            try:
                use_font(name)
                out.append(name)
            except Exception:
                pass
    return out


def styled(fonts, style):
    """Pick the cut for a style string like 'bi'. Underline is drawn separately."""
    cut = ("b" if "b" in style else "") + ("i" if "i" in style else "")
    return fonts.get(cut or "", fonts[""])


def draw_line(c, x, y, size, font, text, style, color):
    """One line of text, with the underline drawn by hand — reportlab has no
    underline attribute on drawString."""
    c.setFillColor(color)
    c.setFont(font, size)
    c.drawString(x, y, text)
    if "u" in style:
        w = pdfmetrics.stringWidth(text, font, size)
        c.setStrokeColor(color)
        c.setLineWidth(max(0.5, size * 0.055))
        c.line(x, y - size * 0.15, x + w, y - size * 0.15)


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
                (r.get("Country") or "US").strip(),
            )


def brand_block(c, cfg, logo, regular):
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
        c.setFont(regular, size)
        base = H - mt - size * 0.78
        c.drawString(mx, base, name)
        return mx, base - 0.20 * inch
    return mx, H - mt - 10


ACCENT_STYLES = ("none", "tick", "bracket", "rule", "band", "stripe")


def accent_color(cfg):
    try:
        return HexColor(cfg.get("accent_color") or "#C2410C")
    except (ValueError, AttributeError):
        return ACCENT  # a bad colour in the config must not stop a print run


def draw_accent(c, cfg, block_left, block_bottom, block_right):
    """The decorative mark, in the same vocabulary as the original mockups.
    Everything stays clear of the USPS barcode zone in the lower right."""
    style = cfg.get("accent_style", "tick")
    if style not in ACCENT_STYLES or style == "none":
        return
    col = accent_color(cfg)
    c.setFillColor(col)
    c.setStrokeColor(col)
    if style == "tick":                    # vertical bar beside the recipient
        c.rect(TO_X - 0.22 * inch, TO_Y - 0.42 * inch, 0.045 * inch, 0.7 * inch,
               stroke=0, fill=1)
    elif style == "bracket":               # corner bracket at the recipient
        s = 0.28 * inch
        x, y = TO_X - 0.3 * inch, TO_Y - 0.1 * inch
        c.setLineWidth(1.4)
        c.line(x, y, x, y + s)
        c.line(x, y + s, x + s, y + s)
    elif style == "rule":                  # hairline under the brand block
        c.setLineWidth(1.1)
        c.line(block_left, block_bottom - 0.16 * inch,
               max(block_right, block_left + 1.2 * inch), block_bottom - 0.16 * inch)
    elif style == "band":                  # band down the left edge
        c.rect(0, 0, 0.2 * inch, H, stroke=0, fill=1)
    elif style == "stripe":                # full-width hairline under the block
        c.setLineWidth(0.9)
        c.line(0, block_bottom - 0.16 * inch, W, block_bottom - 0.16 * inch)


def envelope(c, addr, cfg, logo):
    fonts = use_font(cfg.get("font") or DEFAULT_FONT)
    x, y = brand_block(c, cfg, logo, fonts[""])
    # user nudge: moves the return address without moving the logo
    x += cfg.get("addr_dx_in", 0) * inch
    y -= cfg.get("addr_dy_in", 0) * inch

    ret_style = cfg.get("return_style", "")
    ret_font = styled(fonts, ret_style)
    lines = [s for s in cfg.get("return_address", []) if s.strip()]
    right = x
    for i, line in enumerate(lines):
        draw_line(c, x, y - i * 12.8, 9.5, ret_font, line, ret_style, INK)
        right = max(right, x + pdfmetrics.stringWidth(line, ret_font, 9.5))
    bottom = y - (len(lines) - 1) * 12.8 if lines else y

    draw_accent(c, cfg, x, bottom, right)

    name, a1, a2, city, st, zp, *rest = addr
    country = (rest[0] if rest else "US").strip().upper()
    cityline = city + (f", {st}" if st else "") + (f"  {zp}" if zp else "")
    to = [name, a1] + ([a2] if a2 else []) + [cityline]
    if country not in DOMESTIC:
        to.append(COUNTRY_NAMES.get(country, country))
    nm_style = cfg.get("recipient_style", "b")
    to_fonts = [styled(fonts, nm_style)] + [fonts[""]] * (len(to) - 1)
    # a long address shrinks to fit rather than running off the envelope edge
    size, maxw = 13.0, W - TO_X - 0.35 * inch
    while size > 8.5 and any(
            pdfmetrics.stringWidth(l.upper(), f, size) > maxw
            for l, f in zip(to, to_fonts)):
        size -= 0.5
    lead = size * 1.38
    for i, (line, f) in enumerate(zip(to, to_fonts)):
        draw_line(c, TO_X, TO_Y - i * lead, size, f, line.upper(),
                  nm_style if i == 0 else "", INK)


def render(out, addrs, cfg=None):
    """Page is Com-10 portrait (4.125 x 9.5) to match the driver exactly: an MP tray
    maxes out around 8.5in wide, so a #10 can only feed short-edge-first. The layout
    is authored landscape and rotated onto it, so "Actual size" needs no auto-rotate
    and can't silently scale. Upside down? Turn the stack — it's a loading choice."""
    cfg = cfg or config.active(config.load())  # cfg is one flat preset, not the file
    logo = load_logo(cfg)
    # accept a path or any file-like, so previews can render straight to memory
    c = pdfcanvas.Canvas(out if hasattr(out, "write") else str(out), pagesize=(H, W))
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
        "Order #,FirstName,LastName,Address1,Address2,City,State,PostalCode,Country\n"
        "1,Tyler,Rebello,1232 WILBUR AVE,,SWANSEA,MA,02777-2135,US\n"
        "2,Aiko,Tanaka,1-2-3 Ginza,,Chuo-ku Tokyo,,104-0061,JP\n"
        ",,,,,,,,\n"
    )
    got = list(rows(tmp))
    tmp.unlink()
    assert got[0] == SAMPLE, got[0]
    assert got[1][6] == "JP" and got[1][4] == "", got[1]  # foreign: country kept, no state

    # a CSV predating the Country column defaults to US
    tmp.write_text(
        "Order #,FirstName,LastName,Address1,Address2,City,State,PostalCode\n"
        "1,Tyler,Rebello,1232 WILBUR AVE,,SWANSEA,MA,02777-2135\n")
    assert list(rows(tmp))[0][6] == "US"
    tmp.unlink()

    # absurdly long foreign address must render (autofit), not crash or clip
    import io
    render(io.BytesIO(), [("A Very Long Customer Name That Keeps Going",
                           "12345 Extraordinarily Long Boulevard Name Apt 27B",
                           "", "SOMEWHERE FAR AWAY", "BC", "V6B 4Y8", "CA")])

    # the address nudge must actually reach the page
    a, b = io.BytesIO(), io.BytesIO()
    base = {**config.PRESET_DEFAULTS, "brand_name": "X",
            "return_address": ["1 Somewhere St", "Provo, UT 84604", ""]}
    render(a, [SAMPLE], base)
    render(b, [SAMPLE], {**base, "addr_dx_in": 1.0})
    assert a.getvalue() != b.getvalue(), "addr_dx_in had no effect"

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
