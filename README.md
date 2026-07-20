# Envelope Printer

Prints addresses onto #10 business envelopes from a TCGplayer shipping export
(or typed by hand), with your logo and return address.

## First run

Windows will warn that it doesn't recognise the app — it's unsigned. Click
**More info → Run anyway**.

Settings opens automatically, with a **live preview** beside the form — the
envelope redraws as you type or drag, so logo size and margins aren't guesswork.
Drag the sliders until it looks right, or type an exact value.

Fill in:

- **Business name** — used as the printed name if you have no logo file.
- **Return address** — up to three lines.
- **Logo** — **PNG with a transparent background works best.** SVG is accepted
  but support is limited: gradients and un-outlined text may not render.
- **Address sits** — *Below logo* suits most marks. *Under wordmark* tucks the
  address beside a logo whose icon sits left of its text.
- **Font** — Fraunces ships with the app; the rest are Windows faces that carry a
  real bold. Only families this machine can actually render are offered.
- **Accent** — the decorative mark, in any of six shapes: *None*, *Bar* beside
  the recipient, *Bracket*, *Rule under logo*, *Left edge band*, or
  *Full-width line*. Click the swatch to change its colour.
- **Address style / Recipient name** — **B** / **I** / **U** for the return
  address and the recipient's name line. Bold-italic uses a real bold-italic cut
  where the font has one, and falls back to bold where it doesn't. The remaining
  address lines stay regular, which is what USPS scanners read best.
- **Printer** — see below.

Settings live in `%APPDATA%\Envelope Printer\config.json`.

## Presets

Branding is saved as named **presets**, so one install can print for more than one
business. Switch between them with the **Brand** dropdown on the main window — the
preview and the next print run follow immediately.

In Settings, **Editing** picks which preset the form is showing and **Name**
renames it. Switching with unsaved edits asks before discarding them.

- **Save** — keeps your changes. Changing the name renames the preset.
- **Save as new preset →** — copies the current values under the new name and
  leaves the original untouched. Change the name first, or it will refuse.
- **Delete preset →** — removes it. There is always at least one.

The **printer** is deliberately *not* part of a preset: it belongs to the computer,
so it stays put when you switch brands.

## The printer queue (do this once)

Envelopes need settings your normal printing shouldn't have. In Settings, click
**Set up an envelope printer queue →**, pick your printer, and Create. That makes
a *second* queue on the same printer, fixed to Com-10 and single-sided. Your
normal printing is untouched.

The driver dialog then opens for the two settings Windows won't let an app set:

- **Paper source** → your manual / multi-purpose tray
- **Media type** → Envelopes

Without these, envelopes jam or the toner rubs off.

If the queue can't be created, run the app as administrator and retry.

## Printing

1. Choose a CSV, or add addresses by hand.
2. Arrow through the previews. Click a recipient to jump to it.
3. **Print all.**

Load envelopes in the manual/multi-purpose tray, **printing surface up** — most
trays hold about 10, so a big batch is several loads.

**If envelopes come out creased:** most laser printers have two envelope levers
behind the back cover that relieve fuser pressure. Set them before printing, and
put them back afterwards or normal paper prints badly.

Print at **100% / actual size**. Any "fit to page" shifts the address out of the
zone USPS scans.

## Postage

Leave the top-right corner clear and apply a stamp. Forever stamps are the
simplest: no date, no expiry, and they survive rate increases.

Printed PC Postage (Stamps.com, Endicia, Orange Mailer) is **dated** — USPS
requires a First-Class piece to be mailed the same day its indicia is printed,
so it only works if you print and mail on one day.

## Building from source

    pip install -r requirements.txt
    python fetch_tools.py          # downloads the SumatraPDF print engine
    python envelopes.py --demo     # self-check

    python -m PyInstaller --noconfirm --onefile --windowed \
      --name "Envelope Printer" --icon "assets/brand/app.ico" \
      --add-data "fonts;fonts" --add-data "tools;tools" --add-data "assets;assets" \
      gui.py

`fetch_tools.py` is a separate step because SumatraPDF is a 20MB GPLv3 binary,
kept out of git rather than vendored.

`envelopes.py` also runs standalone: `python envelopes.py export.csv`.

## Credits

Type is [Fraunces](https://fonts.google.com/specimen/Fraunces), SIL Open Font
License — see `fonts/OFL.txt`.

Printing uses [SumatraPDF](https://www.sumatrapdfreader.org/), GPLv3, invoked as
a separate program and redistributed unmodified. Its licence and source link are
placed in `tools/` by `fetch_tools.py`.
