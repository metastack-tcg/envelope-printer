"""Envelope printer — load a CSV or type addresses, preview, print.

    python gui.py

Branding, return address and printer come from config.py (%APPDATA%), so the
app ships neutral and each user sets their own.
"""

import ctypes
import io
import os
import re
import shutil
import subprocess
import threading
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox

import fitz
from PIL import Image, ImageTk

import config
import envelopes
from envelopes import asset

# --- house style tokens (light) ---------------------------------------------
PAPER, INK = "#FAF8F2", "#15130E"
MUTED, FAINT, HAIRLINE = "#6B6353", "#9A8E78", "#E4E0D6"
ACCENT, ON_ACCENT, ACCENT_TINT = "#C2410C", "#FAF8F2", "#F6ECE4"

SUMATRA = asset("tools", "SumatraPDF.exe")
ICON = asset("assets", "brand", "app.ico")
OUT = Path(os.environ.get("TEMP", ".")) / "envelope-printer-batch.pdf"
SNAP = OUT.with_name("envelope-printer-job.pdf")  # frozen copy handed to the printer
PREVIEW_DPI = 110
APP_ID = "EnvelopePrinter.App.1"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def load_fonts():
    """Register Fraunces for this process only — no system install."""
    FR_PRIVATE = 0x10
    for f in ("Fraunces-Regular.ttf", "Fraunces-Italic.ttf", "Fraunces-Bold.ttf"):
        p = asset("fonts", f)
        if p.exists():
            ctypes.windll.gdi32.AddFontResourceExW(str(p), FR_PRIVATE, 0)


def claim_taskbar_identity():
    """Give the process its own taskbar identity rather than inheriting the host's.
    Must run before any window exists."""
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)


def set_window_icon(root):
    """Tk's iconbitmap only sets the window *class* icon — the title bar uses that,
    but the taskbar reads the per-window icon via WM_SETICON and falls back to Tk's
    feather when it's unset. Set both sizes explicitly."""
    if not ICON.exists():
        return
    u = ctypes.windll.user32
    IMAGE_ICON, LR_LOADFROMFILE, WM_SETICON = 1, 0x0010, 0x0080
    root.update_idletasks()
    hwnd = u.GetParent(root.winfo_id()) or root.winfo_id()
    for which, metric in ((1, 11), (0, 49)):  # ICON_BIG/CXICON, ICON_SMALL/CXSMICON
        px = u.GetSystemMetrics(metric)
        h = u.LoadImageW(None, str(ICON), IMAGE_ICON, px, px, LR_LOADFROMFILE)
        if h:
            u.SendMessageW(hwnd, WM_SETICON, which, h)


def serif(size, weight="normal"):
    return ("Fraunces" if "Fraunces" in tkfont.families() else "Georgia", size, weight)


def sans(size, weight="normal"):
    return ("Segoe UI", size, weight)


def psq(s):
    """Quote for PowerShell — printer names can contain apostrophes."""
    return "'" + str(s).replace("'", "''") + "'"


def powershell(script, timeout=60):
    return subprocess.run(["powershell", "-NoProfile", "-Command", script],
                          capture_output=True, text=True, timeout=timeout,
                          creationflags=NO_WINDOW)


def printers():
    try:
        r = powershell("Get-Printer | Select-Object -ExpandProperty Name", timeout=20)
        return [x.strip() for x in r.stdout.splitlines() if x.strip()]
    except Exception:
        return []


# --- house-style widgets ----------------------------------------------------

def hairline(parent):
    return tk.Frame(parent, height=1, bg=HAIRLINE)


def kicker(parent, text):
    # tk has no letter-spacing, so space the glyphs to fake the +2 tracking
    return tk.Label(parent, text=" ".join(text.upper()), font=sans(8),
                    fg=FAINT, bg=PAPER, anchor="w")


class UnderlineAction(tk.Frame):
    def __init__(self, parent, text, command):
        super().__init__(parent, bg=PAPER)
        self.lbl = tk.Label(self, text=text, font=sans(9), fg=ACCENT, bg=PAPER, cursor="hand2")
        self.lbl.pack(anchor="w")
        self.rule = tk.Frame(self, height=2, bg=ACCENT)
        self.rule.pack(fill="x")
        self.lbl.bind("<Button-1>", lambda e: command())

    def enable(self, on):
        self.lbl.config(fg=ACCENT if on else FAINT, cursor="hand2" if on else "arrow")
        self.rule.config(bg=ACCENT if on else HAIRLINE)


def entry(parent, var, width=26):
    return tk.Entry(parent, textvariable=var, font=sans(10), fg=INK, bg=PAPER,
                    relief="solid", bd=1, highlightthickness=1,
                    highlightbackground=HAIRLINE, highlightcolor=ACCENT,
                    insertbackground=INK, width=width)


def ledger_row(parent, label, width=12):
    """Label left, control right, hairline above."""
    hairline(parent).pack(fill="x", pady=(8, 0))
    row = tk.Frame(parent, bg=PAPER)
    row.pack(fill="x")
    # pady 4: the settings form is 11 rows tall, and 6 pushed the dialog past
    # the ~768px a small laptop screen can show
    tk.Label(row, text=label, font=sans(9), fg=FAINT, bg=PAPER,
             anchor="w", width=width).pack(side="left", pady=4)
    return row


class Segmented(tk.Frame):
    """Small exclusive set — house style prefers this over a dropdown for 2-4."""

    def __init__(self, parent, var, options, on_change=None):
        super().__init__(parent, bg=PAPER)
        self.var, self.btns, self.cb = var, {}, on_change
        for value, label in options:
            b = tk.Label(self, text=label, font=sans(9), padx=10, pady=3,
                         cursor="hand2", bd=1, relief="solid")
            b.pack(side="left")
            b.bind("<Button-1>", lambda e, v=value: self.pick(v))
            self.btns[value] = b
        var.trace_add("write", lambda *a: self.paint())
        self.paint()

    def pick(self, v):
        self.var.set(v)
        self.paint()
        if self.cb:
            self.cb()

    def paint(self):
        for v, b in self.btns.items():
            on = self.var.get() == v
            b.config(bg=ACCENT_TINT if on else PAPER, fg=ACCENT if on else MUTED,
                     highlightbackground=ACCENT if on else HAIRLINE)


class Dialog(tk.Toplevel):
    """Shared chrome: kicker + serif title + ink rule."""

    def __init__(self, parent, kick, title):
        super().__init__(parent, bg=PAPER)
        self.title(title)
        self.result = None
        self.transient(parent)
        self.resizable(False, False)
        self.box = tk.Frame(self, bg=PAPER)
        self.box.pack(fill="both", expand=True, padx=22, pady=18)
        kicker(self.box, kick).pack(fill="x")
        tk.Label(self.box, text=title, font=serif(20), fg=INK, bg=PAPER,
                 anchor="w").pack(fill="x", pady=(2, 8))
        tk.Frame(self.box, height=2, bg=INK).pack(fill="x")
        self.bind("<Escape>", lambda e: self.destroy())

    def actions(self, primary, command):
        self.err = tk.Label(self.box, text="", font=sans(9), fg=ACCENT, bg=PAPER,
                            anchor="w", wraplength=400, justify="left")
        self.err.pack(fill="x", pady=(10, 0))
        acts = tk.Frame(self.box, bg=PAPER)
        acts.pack(fill="x", pady=(12, 0))
        b = tk.Label(acts, text=primary, font=sans(10), fg=ON_ACCENT, bg=ACCENT,
                     padx=20, pady=8, cursor="hand2")
        b.pack(side="right")
        b.bind("<Button-1>", lambda e: command())
        c = tk.Label(acts, text="Cancel", font=sans(9), fg=MUTED, bg=PAPER, cursor="hand2")
        c.pack(side="right", padx=16)
        c.bind("<Button-1>", lambda e: self.destroy())
        self.bind("<Return>", lambda e: command())

    def center_on(self, parent, dy=60):
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + dy
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.grab_set()


class AddressDialog(Dialog):
    FIELDS = [("Name", "name"), ("Street", "a1"), ("Apt, suite", "a2"),
              ("City", "city"), ("State", "st"), ("ZIP", "zp"), ("Country", "ct")]

    def __init__(self, parent):
        super().__init__(parent, "recipient", "Add address")
        self.vars = {}
        for label, key in self.FIELDS:
            row = ledger_row(self.box, label)
            v = tk.StringVar(value="US" if key == "ct" else "")
            entry(row, v, 30).pack(side="right", ipady=3)
            self.vars[key] = v
        self.actions("Add", self.save)
        self.center_on(parent, 80)

    def save(self):
        v = {k: self.vars[k].get().strip() for _, k in self.FIELDS}
        # validated because a bad address here costs a stamp and an envelope
        missing = [lab for lab, k in self.FIELDS
                   if k in ("name", "a1", "city", "ct") and not v[k]]
        if missing:
            self.err.config(text="Still needed: " + ", ".join(missing).lower())
            return
        # US formats are enforced only for US mail — foreign addresses have
        # their own postal codes and often no state at all
        if v["ct"].upper() in ("US", "USA"):
            if not re.fullmatch(r"[A-Za-z]{2}", v["st"]):
                self.err.config(text="State should be the two-letter code, e.g. UT.")
                return
            if not re.fullmatch(r"\d{5}(-\d{4})?", v["zp"]):
                self.err.config(text="ZIP should be 12345 or 12345-6789.")
                return
        self.result = (v["name"], v["a1"], v["a2"], v["city"], v["st"].upper(),
                       v["zp"], v["ct"].upper())
        self.destroy()


class Slider(tk.Canvas):
    """Drag control for a spatial value. Tk's native Scale is too heavy for the
    house style, and a bare number tells you nothing about what 2.35in looks like."""

    def __init__(self, parent, var, lo, hi, width=126, on_change=None):
        super().__init__(parent, width=width, height=20, bg=PAPER,
                         highlightthickness=0, bd=0, cursor="sb_h_double_arrow")
        self.var, self.lo, self.hi, self.w, self.cb = var, lo, hi, width, on_change
        self.bind("<Button-1>", self._drag)
        self.bind("<B1-Motion>", self._drag)
        var.trace_add("write", lambda *a: self.paint())
        self.paint()

    def _frac(self):
        try:
            v = float(self.var.get())
        except ValueError:
            return 0.0
        return min(max((v - self.lo) / (self.hi - self.lo), 0.0), 1.0)

    def _drag(self, e):
        f = min(max((e.x - 4) / (self.w - 8), 0.0), 1.0)
        self.var.set(f"{self.lo + f * (self.hi - self.lo):.2f}")
        if self.cb:
            self.cb()

    def paint(self):
        self.delete("all")
        y = 10
        x = 4 + self._frac() * (self.w - 8)
        self.create_line(4, y, self.w - 4, y, fill=HAIRLINE, width=1)
        self.create_line(4, y, x, y, fill=ACCENT, width=2)
        self.create_rectangle(x - 3, y - 6, x + 3, y + 6, fill=ACCENT, outline="")


class StyleToggles(tk.Frame):
    """B / I / U, the shape everyone already knows from a text toolbar.
    The value is a string of flags like 'bi' so it stores as plain JSON."""

    LABELS = [("b", "B", "bold"), ("i", "I", "italic"), ("u", "U", "normal")]

    def __init__(self, parent, var, on_change=None):
        super().__init__(parent, bg=PAPER)
        self.var, self.cb, self.btns = var, on_change, {}
        for flag, text, weight in self.LABELS:
            f = ("Georgia", 10, weight) if flag != "u" else ("Georgia", 10, "normal")
            b = tk.Label(self, text=text, font=f, width=2, pady=2,
                         cursor="hand2", bd=1, relief="solid")
            b.pack(side="left")
            b.bind("<Button-1>", lambda e, fl=flag: self.toggle(fl))
            self.btns[flag] = b
        # repaint on external writes too, or loading another preset leaves the
        # buttons showing the previous preset's state
        var.trace_add("write", lambda *a: self.paint())
        self.paint()

    def toggle(self, flag):
        cur = set(self.var.get())
        cur.symmetric_difference_update({flag})
        self.var.set("".join(f for f, _, _ in self.LABELS if f in cur))
        self.paint()
        if self.cb:
            self.cb()

    def paint(self):
        cur = self.var.get()
        for flag, b in self.btns.items():
            on = flag in cur
            b.config(bg=ACCENT_TINT if on else PAPER, fg=ACCENT if on else MUTED,
                     highlightbackground=ACCENT if on else HAIRLINE)


class Swatch(tk.Frame):
    """Colour well — click to open the OS picker."""

    def __init__(self, parent, var, on_change=None):
        super().__init__(parent, bg=HAIRLINE, bd=0)
        self.var, self.cb = var, on_change
        self.chip = tk.Label(self, width=4, height=1, bg=self._safe(), cursor="hand2")
        self.chip.pack(padx=1, pady=1)
        self.chip.bind("<Button-1>", lambda e: self.pick())
        var.trace_add("write", lambda *a: self.chip.config(bg=self._safe()))

    def _safe(self):
        v = (self.var.get() or "").strip()
        try:
            self.winfo_rgb(v)
            return v
        except tk.TclError:
            return ACCENT

    def pick(self):
        rgb, name = colorchooser.askcolor(color=self._safe(), parent=self,
                                          title="Accent bar colour")
        if name:
            self.var.set(name)
            if self.cb:
                self.cb()


class SettingsDialog(Dialog):
    """Edits the active preset, with a live preview — every spatial setting here
    (logo size, margins, where the address sits) is guesswork without one."""

    def __init__(self, parent, cfg):
        super().__init__(parent, "setup", "Settings")
        self.cfg = {"presets": dict(cfg["presets"]), "active": cfg["active"],
                    "printer": cfg["printer"]}
        self.original = cfg["active"]
        self._photo, self._pending = None, None
        p = config.active(cfg)

        split = tk.Frame(self.box, bg=PAPER)
        split.pack(fill="both", expand=True)
        form = tk.Frame(split, bg=PAPER)
        form.pack(side="left", fill="y", anchor="n")
        right = tk.Frame(split, bg=PAPER)
        right.pack(side="left", fill="y", anchor="n", padx=(24, 0))

        kicker(right, "preview").pack(fill="x", pady=(8, 0))
        shell = tk.Frame(right, bg=HAIRLINE)
        shell.pack(fill="x", pady=(4, 0))
        self.pv = tk.Canvas(shell, width=430, height=192, bg=PAPER,
                            highlightthickness=0, bd=0)
        self.pv.pack(padx=1, pady=1)
        self.pv_note = tk.Label(right, text="", font=sans(8), fg=MUTED, bg=PAPER,
                                anchor="w", wraplength=430, justify="left")
        self.pv_note.pack(fill="x", pady=(6, 0))

        def watch(var):
            var.trace_add("write", lambda *a: self.queue_preview())
            return var

        self.editing = tk.StringVar(value=cfg["active"])
        row = ledger_row(form, "Editing", 13)
        self.edit_holder = tk.Frame(row, bg=PAPER)
        self.edit_holder.pack(side="right")
        self.build_edit_menu()

        self.pname = tk.StringVar(value=cfg["active"])
        row = ledger_row(form, "Name", 13)
        entry(row, self.pname, 24).pack(side="right", ipady=3)

        self.brand = watch(tk.StringVar(value=p["brand_name"]))
        row = ledger_row(form, "Business name", 13)
        entry(row, self.brand, 24).pack(side="right", ipady=3)

        addr = list(p["return_address"]) + ["", "", ""]
        self.addr = [watch(tk.StringVar(value=addr[i])) for i in range(3)]
        for i, v in enumerate(self.addr):
            row = ledger_row(form, "Return address" if i == 0 else "", 13)
            entry(row, v, 24).pack(side="right", ipady=3)

        self.ret_style = tk.StringVar(value=p.get("return_style", ""))
        row = ledger_row(form, "Address style", 13)
        StyleToggles(row, self.ret_style, self.queue_preview).pack(side="right")

        self.to_style = tk.StringVar(value=p.get("recipient_style", "b"))
        row = ledger_row(form, "Recipient name", 13)
        StyleToggles(row, self.to_style, self.queue_preview).pack(side="right")

        self.logo = tk.StringVar(value=p["logo_path"])
        row = ledger_row(form, "Logo", 13)
        holder = tk.Frame(row, bg=PAPER)
        holder.pack(side="right")
        self.logo_lbl = tk.Label(holder, font=sans(9), fg=INK, bg=PAPER, anchor="e",
                                 width=26, justify="right")
        self.logo_lbl.pack(anchor="e")
        picks = tk.Frame(holder, bg=PAPER)
        picks.pack(anchor="e")
        UnderlineAction(picks, "Choose file →", self.pick_logo).pack(side="left")
        UnderlineAction(picks, "Clear", self.clear_logo).pack(side="left", padx=(12, 0))
        self.show_logo()

        self.width = watch(tk.StringVar(value=str(p["logo_width_in"])))
        row = ledger_row(form, "Logo width", 13)
        wrap = tk.Frame(row, bg=PAPER)
        wrap.pack(side="right")
        entry(wrap, self.width, 5).pack(side="right", ipady=3, padx=(8, 0))
        Slider(wrap, self.width, 0.5, 5.0, 126, self.queue_preview).pack(side="right")

        self.layout = tk.StringVar(value=p["logo_layout"])
        row = ledger_row(right, "Address sits", 13)
        Segmented(row, self.layout, [("below", "Below logo"),
                                     ("hang", "Under wordmark")],
                  on_change=self.queue_preview).pack(side="right")

        self.font = tk.StringVar(value=p.get("font", envelopes.DEFAULT_FONT))
        row = ledger_row(right, "Font", 13)
        fonts = envelopes.available_fonts() or [envelopes.DEFAULT_FONT]
        if self.font.get() not in fonts:
            fonts.insert(0, self.font.get())
        om = tk.OptionMenu(row, self.font, *fonts,
                           command=lambda *a: self.queue_preview())
        om.config(font=sans(9), bg=PAPER, fg=INK, activebackground=ACCENT_TINT,
                  relief="solid", bd=1, highlightthickness=0, anchor="w",
                  padx=8, pady=2, width=21)
        om["menu"].config(font=sans(9), bg=PAPER, fg=INK,
                          activebackground=ACCENT_TINT, activeforeground=ACCENT, bd=0)
        om.pack(side="right")

        self.accent = tk.StringVar(value=p.get("accent_style", "tick"))
        self.accent_col = watch(tk.StringVar(value=p.get("accent_color", ACCENT)))
        row = ledger_row(right, "Accent", 13)
        bar = tk.Frame(row, bg=PAPER)
        bar.pack(side="right")
        Swatch(bar, self.accent_col, self.queue_preview).pack(side="right", padx=(8, 0))
        labels = {"none": "None", "tick": "Bar", "bracket": "Bracket",
                  "rule": "Rule under logo", "band": "Left edge band",
                  "stripe": "Full-width line"}
        self.accent_label = tk.StringVar(value=labels[self.accent.get()])

        def set_accent(shown):
            for k, v in labels.items():
                if v == shown:
                    self.accent.set(k)
            self.queue_preview()

        om = tk.OptionMenu(bar, self.accent_label, *labels.values(), command=set_accent)
        om.config(font=sans(9), bg=PAPER, fg=INK, activebackground=ACCENT_TINT,
                  relief="solid", bd=1, highlightthickness=0, anchor="w",
                  padx=8, pady=2, width=15)
        om["menu"].config(font=sans(9), bg=PAPER, fg=INK,
                          activebackground=ACCENT_TINT, activeforeground=ACCENT, bd=0)
        om.pack(side="right")

        self.mx = watch(tk.StringVar(value=str(p["margin_x_in"])))
        row = ledger_row(right, "Margin left", 13)
        wrap = tk.Frame(row, bg=PAPER)
        wrap.pack(side="right")
        entry(wrap, self.mx, 5).pack(side="right", ipady=3, padx=(8, 0))
        Slider(wrap, self.mx, 0.2, 2.0, 126, self.queue_preview).pack(side="right")

        self.mt = watch(tk.StringVar(value=str(p["margin_top_in"])))
        row = ledger_row(right, "Margin top", 13)
        wrap = tk.Frame(row, bg=PAPER)
        wrap.pack(side="right")
        entry(wrap, self.mt, 5).pack(side="right", ipady=3, padx=(8, 0))
        Slider(wrap, self.mt, 0.2, 2.0, 126, self.queue_preview).pack(side="right")

        # moves the return address off its computed spot, logo stays put
        self.adx = watch(tk.StringVar(value=str(p.get("addr_dx_in", 0.0))))
        self.ady = watch(tk.StringVar(value=str(p.get("addr_dy_in", 0.0))))
        row = ledger_row(right, "Address nudge", 13)
        wrap = tk.Frame(row, bg=PAPER)
        wrap.pack(side="right")
        entry(wrap, self.ady, 5).pack(side="right", ipady=3, padx=(6, 0))
        Slider(wrap, self.ady, -0.5, 1.5, 80, self.queue_preview).pack(side="right")
        tk.Label(wrap, text="↓", font=sans(9), fg=FAINT, bg=PAPER).pack(side="right", padx=(12, 2))
        entry(wrap, self.adx, 5).pack(side="right", ipady=3, padx=(6, 0))
        Slider(wrap, self.adx, -0.5, 2.0, 80, self.queue_preview).pack(side="right")
        tk.Label(wrap, text="→", font=sans(9), fg=FAINT, bg=PAPER).pack(side="right", padx=(0, 2))

        self.printer = tk.StringVar(value=self.cfg["printer"])
        row = ledger_row(form, "Printer", 13)
        names = printers() or [""]
        if self.printer.get() and self.printer.get() not in names:
            names.insert(0, self.printer.get())
        om = tk.OptionMenu(row, self.printer, *names)
        om.config(font=sans(9), bg=PAPER, fg=INK, activebackground=ACCENT_TINT,
                  relief="solid", bd=1, highlightthickness=0, anchor="w",
                  padx=8, pady=2, width=21)
        om["menu"].config(font=sans(9), bg=PAPER, fg=INK,
                          activebackground=ACCENT_TINT, activeforeground=ACCENT, bd=0)
        om.pack(side="right")

        hairline(form).pack(fill="x", pady=(8, 0))
        UnderlineAction(form, "Set up an envelope printer queue →",
                        lambda: self.wizard(parent)).pack(anchor="w", pady=(8, 0))
        manage = tk.Frame(right, bg=PAPER)
        manage.pack(fill="x", pady=(10, 0))
        UnderlineAction(manage, "Save as new preset →", self.save_as).pack(side="left")
        self.del_act = UnderlineAction(manage, "Delete preset →", self.delete)
        self.del_act.pack(side="left", padx=(20, 0))
        self.del_act.enable(len(self.cfg["presets"]) > 1)

        self.actions("Save", self.save)
        self.draw_preview()
        self.center_on(parent, 20)

    # --- preset switching ----------------------------------------------------

    def build_edit_menu(self):
        for w in self.edit_holder.winfo_children():
            w.destroy()
        om = tk.OptionMenu(self.edit_holder, self.editing, *config.names(self.cfg),
                           command=self.switch_editing)
        om.config(font=sans(9), bg=PAPER, fg=INK, activebackground=ACCENT_TINT,
                  relief="solid", bd=1, highlightthickness=0, anchor="w",
                  padx=8, pady=2, width=21)
        om["menu"].config(font=sans(9), bg=PAPER, fg=INK,
                          activebackground=ACCENT_TINT, activeforeground=ACCENT, bd=0)
        om.pack()

    def switch_editing(self, name):
        """Load another preset into the form, guarding unsaved edits."""
        if name == self.original:
            return
        stored = config.active({"presets": self.cfg["presets"], "active": self.original})
        dirty = (self.collect(quiet=True) != stored) or self.pname.get().strip() != self.original
        if dirty and not messagebox.askyesno(
                "Discard changes",
                f"Discard your unsaved changes to \“{self.original}\”?",
                parent=self):
            self.editing.set(self.original)
            return
        self.original = name
        self.load_preset(name)

    def load_preset(self, name):
        p = config.active({"presets": self.cfg["presets"], "active": name})
        self.pname.set(name)
        self.editing.set(name)
        self.brand.set(p["brand_name"])
        addr = list(p["return_address"]) + ["", "", ""]
        for i, v in enumerate(self.addr):
            v.set(addr[i])
        self.logo.set(p["logo_path"])
        self.show_logo()
        self.width.set(str(p["logo_width_in"]))
        self.layout.set(p["logo_layout"])
        self.font.set(p.get("font", envelopes.DEFAULT_FONT))
        self.accent.set(p.get("accent_style", "tick"))
        self.accent_label.set({"none": "None", "tick": "Bar", "bracket": "Bracket",
                               "rule": "Rule under logo", "band": "Left edge band",
                               "stripe": "Full-width line"}[p.get("accent_style", "tick")])
        self.accent_col.set(p.get("accent_color", ACCENT))
        self.ret_style.set(p.get("return_style", ""))
        self.to_style.set(p.get("recipient_style", "b"))
        self.mx.set(str(p["margin_x_in"]))
        self.mt.set(str(p["margin_top_in"]))
        self.adx.set(str(p.get("addr_dx_in", 0.0)))
        self.ady.set(str(p.get("addr_dy_in", 0.0)))
        self.del_act.enable(len(self.cfg["presets"]) > 1)
        self.err.config(text="")
        self.draw_preview()

    # --- live preview --------------------------------------------------------

    def queue_preview(self):
        """Coalesce bursts of edits (typing, dragging) into a single render."""
        if self._pending:
            self.after_cancel(self._pending)
        self._pending = self.after(60, self.draw_preview)

    def num(self, var, default, lo, hi):
        """Tolerant parse — a half-typed value must not blank the preview."""
        try:
            return min(max(float(var.get()), lo), hi)
        except ValueError:
            return default

    def preview_cfg(self):
        base = config.active({"presets": self.cfg["presets"], "active": self.original})
        return {**base,
                "brand_name": self.brand.get().strip(),
                "return_address": [v.get().strip() for v in self.addr],
                "logo_path": self.logo.get().strip(),
                "logo_width_in": self.num(self.width, 2.35, 0.2, 6),
                "logo_layout": self.layout.get(),
                "margin_x_in": self.num(self.mx, 0.4, 0, 4),
                "margin_top_in": self.num(self.mt, 0.4, 0, 3),
                "addr_dx_in": self.num(self.adx, 0.0, -2, 3),
                "addr_dy_in": self.num(self.ady, 0.0, -2, 3),
                "font": self.font.get(),
                "accent_style": self.accent.get(),
                "accent_color": self.accent_col.get(),
                "return_style": self.ret_style.get(),
                "recipient_style": self.to_style.get()}

    def draw_preview(self):
        self._pending = None
        cfg = self.preview_cfg()
        note = ""
        p = cfg["logo_path"]
        if p and not Path(p).exists():
            note = "That logo file is missing — the business name is shown instead."
        elif p:
            try:
                envelopes.Logo(p, cfg["logo_width_in"])
            except Exception as e:
                note = f"This logo can't be drawn ({e}). The name is shown instead."
        try:
            buf = io.BytesIO()
            envelopes.render(buf, [envelopes.SAMPLE], cfg)
            doc = fitz.open(stream=buf.getvalue(), filetype="pdf")
            pix = doc[0].get_pixmap(dpi=96)
            im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            doc.close()
        except Exception as e:
            self.pv.delete("all")
            self.pv.create_text(215, 96, text=f"Preview failed:\n{e}", fill=MUTED,
                                font=sans(9), width=380, justify="center")
            return
        im = im.rotate(-90, expand=True)  # authored landscape, paged portrait
        cw, ch = int(self.pv["width"]), int(self.pv["height"])
        s = min((cw - 8) / im.width, (ch - 8) / im.height)
        im = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))),
                       Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(im)
        self.pv.delete("all")
        x, y = (cw - im.width) // 2, (ch - im.height) // 2
        self.pv.create_image(cw // 2, ch // 2, image=self._photo)
        # outline the envelope edge — the point is seeing what sits close to it
        self.pv.create_rectangle(x, y, x + im.width, y + im.height,
                                 outline=HAIRLINE, width=1)
        self.pv_note.config(text=note, fg=ACCENT if note else MUTED)

    # --- logo ----------------------------------------------------------------

    def show_logo(self):
        p = self.logo.get()
        self.logo_lbl.config(text=Path(p).name if p else "None — the name is used",
                             fg=INK if p else MUTED)

    def pick_logo(self):
        p = filedialog.askopenfilename(
            parent=self, title="Logo",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.svg"), ("All files", "*.*")])
        if p:
            self.logo.set(p)
            self.show_logo()
            self.draw_preview()
            if p.lower().endswith(".svg"):
                self.err.config(
                    text="SVG support is limited — gradients and live text may not "
                         "render correctly. A PNG with a transparent background is safer.")

    def clear_logo(self):
        self.logo.set("")
        self.show_logo()
        self.draw_preview()

    def wizard(self, parent):
        d = PrinterSetupDialog(parent)
        parent.wait_window(d)
        if d.result:
            self.printer.set(d.result)

    # --- save ----------------------------------------------------------------

    def collect(self, quiet=False):
        """Validated preset fields, or None with the reason shown inline.
        quiet=True serves the dirty check, which must not flash errors at you."""
        def fail(msg):
            if not quiet:
                self.err.config(text=msg)
            return None

        try:
            w, mx, mt = float(self.width.get()), float(self.mx.get()), float(self.mt.get())
            adx, ady = float(self.adx.get()), float(self.ady.get())
        except ValueError:
            return fail("Logo width, margins and nudge must be numbers, e.g. 2.35.")
        if not (-2 <= adx <= 3) or not (-2 <= ady <= 3):
            return fail("Address nudge should stay within ±2 inches.")
        if not (0.2 <= w <= 6) or not (0 <= mx <= 4) or not (0 <= mt <= 3):
            return fail("Logo width 0.2–6in, margins within the envelope.")
        p = self.logo.get().strip()
        if p:
            if not Path(p).exists():
                return fail("That logo file no longer exists.")
            try:
                envelopes.Logo(p, w)
            except Exception as e:
                return fail(f"That logo could not be used: {e}")
        keep = config.active({"presets": self.cfg["presets"], "active": self.original})
        return {**keep,
                "brand_name": self.brand.get().strip(),
                "return_address": [v.get().strip() for v in self.addr],
                "logo_path": p, "logo_width_in": w, "logo_layout": self.layout.get(),
                "margin_x_in": mx, "margin_top_in": mt,
                "addr_dx_in": adx, "addr_dy_in": ady,
                "font": self.font.get(),
                "accent_style": self.accent.get(),
                "accent_color": self.accent_col.get(),
                "return_style": self.ret_style.get(),
                "recipient_style": self.to_style.get()}

    def commit(self):
        try:
            config.save(self.cfg)
        except OSError as e:
            self.err.config(text=f"Could not save settings: {e}")
            return False
        self.result = self.cfg
        self.destroy()
        return True

    def save(self):
        preset = self.collect()
        if preset is None:
            return
        name = self.pname.get().strip()
        if not name:
            self.err.config(text="Give the preset a name.")
            return
        if name != self.original and name in self.cfg["presets"]:
            self.err.config(text=f"A preset called “{name}” already exists.")
            return
        self.cfg["presets"].pop(self.original, None)  # renames by moving the key
        self.cfg["presets"][name] = preset
        self.cfg["active"] = name
        self.cfg["printer"] = self.printer.get().strip()
        self.commit()

    def save_as(self):
        preset = self.collect()
        if preset is None:
            return
        name = self.pname.get().strip()
        if not name:
            self.err.config(text="Give the new preset a name.")
            return
        if name in self.cfg["presets"]:
            self.err.config(text=f"“{name}” already exists — change the name first.")
            return
        self.cfg["presets"][name] = preset      # leaves the original untouched
        self.original = name
        self.cfg["active"] = name
        self.cfg["printer"] = self.printer.get().strip()
        self.commit()

    def delete(self):
        if len(self.cfg["presets"]) <= 1:
            return  # there must always be one to print with
        if not messagebox.askyesno(
                "Delete preset", f"Delete “{self.original}”?", parent=self):
            return
        self.cfg["presets"].pop(self.original, None)
        self.cfg["active"] = next(iter(config.names(self.cfg)))
        self.cfg["printer"] = self.printer.get().strip()
        self.commit()


class PrinterSetupDialog(Dialog):
    """Creates a second queue on the same driver+port, holding the envelope settings.

    Media type and tray are driver-private — no API sets them — so the last step
    hands the user the driver's own dialog.
    """

    def __init__(self, parent):
        super().__init__(parent, "printer", "Envelope queue")
        tk.Label(self.box, font=sans(9), fg=MUTED, bg=PAPER, justify="left",
                 wraplength=420, anchor="w",
                 text="This makes a second queue for your existing printer that "
                      "always prints Com-10, single-sided. Your normal printing is "
                      "untouched.").pack(fill="x", pady=(10, 0))

        self.src = tk.StringVar()
        names = [n for n in printers()]
        row = ledger_row(self.box, "Your printer", 13)
        om = tk.OptionMenu(row, self.src, *(names or [""]))
        om.config(font=sans(9), bg=PAPER, fg=INK, activebackground=ACCENT_TINT,
                  relief="solid", bd=1, highlightthickness=0, anchor="w",
                  padx=8, pady=2, width=28)
        om["menu"].config(font=sans(9), bg=PAPER, fg=INK,
                          activebackground=ACCENT_TINT, activeforeground=ACCENT, bd=0)
        om.pack(side="right")
        if names:
            self.src.set(names[0])

        self.name = tk.StringVar(value="Envelopes")
        row = ledger_row(self.box, "Queue name", 13)
        entry(row, self.name, 28).pack(side="right", ipady=3)

        self.actions("Create", self.create)
        self.center_on(parent, 100)

    def create(self):
        src, name = self.src.get().strip(), self.name.get().strip()
        if not src or not name:
            self.err.config(text="Pick your printer and give the queue a name.")
            return
        self.err.config(text="Creating…", fg=MUTED)
        self.update_idletasks()
        script = (
            f"$s = Get-Printer -Name {psq(src)};"
            f"if (-not (Get-Printer -Name {psq(name)} -ErrorAction SilentlyContinue)) {{"
            f"  Add-Printer -Name {psq(name)} -DriverName $s.DriverName -PortName $s.PortName }};"
            f"Set-PrintConfiguration -PrinterName {psq(name)} -PaperSize Envelope10 "
            f"-DuplexingMode OneSided; 'DONE'"
        )
        try:
            r = powershell(script)
        except Exception as e:
            self.err.config(text=str(e), fg=ACCENT)
            return
        if "DONE" not in r.stdout:
            msg = (r.stderr or r.stdout or "").strip().splitlines()
            self.err.config(fg=ACCENT, text="Could not create it: "
                            + (msg[0] if msg else "unknown error")
                            + "  Try running the app as administrator.")
            return
        self.result = name
        messagebox.showinfo(
            "Queue created",
            f"“{name}” is ready — Com-10, single-sided.\n\n"
            "One step left, and printing won't be right without it:\n"
            "in the driver dialog that opens next, set the paper source to your "
            "manual / multi-purpose tray and the media type to Envelopes.",
            parent=self)
        subprocess.Popen(["rundll32", "printui.dll,PrintUIEntry", "/e", "/n", name])
        self.destroy()


class BrandMark(tk.Canvas):
    """The app mark, drawn from the 512 icon spec — no asset needed."""

    SHAPES = [(349, 120, 96, 96, True), (67, 264, 190, 28, False),
              (67, 308, 280, 28, False), (67, 352, 378, 28, False)]

    def __init__(self, parent, px=16):
        super().__init__(parent, width=px, height=px, bg=PAPER, highlightthickness=0, bd=0)
        s = px / 512
        for x, y, w, h, acc in self.SHAPES:
            self.create_rectangle(x * s, y * s, (x + w) * s, (y + h) * s,
                                  fill=ACCENT if acc else INK, outline="")


class App:
    def __init__(self, root):
        self.root = root
        self.cfg = config.load()
        self.preset = config.active(self.cfg)
        self.pages, self.addrs, self.i = [], [], 0
        self.csv_name, self.manual, self._undo = None, 0, None
        self._printing = False
        root.configure(bg=PAPER)
        root.title("Envelope printer")

        outer = tk.Frame(root, bg=PAPER)
        outer.pack(fill="both", expand=True, padx=24, pady=24)

        head = tk.Frame(outer, bg=PAPER)
        head.pack(fill="x")
        self.kick = kicker(head, config.active(self.cfg)["brand_name"] or "envelopes")
        self.kick.pack(side="left")
        UnderlineAction(head, "Settings →", self.settings).pack(side="right")
        tk.Label(outer, text="Envelope printer", font=serif(22), fg=INK, bg=PAPER,
                 anchor="w").pack(fill="x", pady=(2, 8))
        tk.Frame(outer, height=2, bg=INK).pack(fill="x")

        body = tk.Frame(outer, bg=PAPER)
        body.pack(fill="both", expand=True, pady=(24, 0))
        left = tk.Frame(body, bg=PAPER, width=300)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        right = tk.Frame(body, bg=PAPER)
        right.pack(side="left", fill="both", expand=True, padx=(32, 0))

        # bottom-anchored first, so a long recipient list can't displace them
        self.print_btn = tk.Label(left, text="Print all", font=sans(10),
                                  fg=FAINT, bg=HAIRLINE, pady=8, cursor="arrow")
        self.print_btn.pack(side="bottom", fill="x", pady=(16, 0))
        self.print_btn.bind("<Button-1>", lambda e: self.print_all())
        UnderlineAction(left, "Printer setup →", self.driver_setup).pack(
            side="bottom", anchor="w", pady=(6, 0))
        self.printer_lbl = tk.Label(left, font=sans(9), fg=INK, bg=PAPER, anchor="w",
                                    relief="solid", bd=1, padx=8, pady=4)
        self.printer_lbl.pack(side="bottom", fill="x", pady=(4, 0))
        kicker(left, "printer").pack(side="bottom", fill="x", pady=(16, 0))

        # preset switcher — same shape as the printer block below it
        self.preset_holder = tk.Frame(left, bg=PAPER)
        self.preset_holder.pack(side="bottom", fill="x", pady=(4, 0))
        kicker(left, "brand").pack(side="bottom", fill="x", pady=(16, 0))
        self.preset_var = tk.StringVar(value=self.cfg["active"])
        self.build_preset_menu()

        kicker(left, "source").pack(fill="x")
        self.src = tk.Label(left, text="No file selected", font=sans(10), fg=MUTED,
                            bg=PAPER, anchor="w", wraplength=290, justify="left")
        self.src.pack(fill="x", pady=(4, 8))
        acts = tk.Frame(left, bg=PAPER)
        acts.pack(fill="x")
        UnderlineAction(acts, "Choose CSV →", self.browse).pack(side="left")
        UnderlineAction(acts, "Add address →", self.add_address).pack(side="left", padx=(20, 0))

        self.hero = tk.Label(left, text="", font=serif(44), fg=ACCENT, bg=PAPER, anchor="w")
        self.hero.pack(fill="x", pady=(20, 0))
        self.hero_sub = tk.Label(left, text="Nothing loaded yet", font=sans(9),
                                 fg=MUTED, bg=PAPER, anchor="w")
        self.hero_sub.pack(fill="x")

        rhead = tk.Frame(left, bg=PAPER)
        rhead.pack(fill="x", pady=(20, 0))
        kicker(rhead, "recipients").pack(side="left")
        self.remove_act = UnderlineAction(rhead, "Remove →", self.remove_selected)
        self.remove_act.pack(side="right")
        self.remove_act.enable(False)
        hairline(left).pack(fill="x", pady=(4, 0))

        wrap = tk.Frame(left, bg=PAPER)
        wrap.pack(fill="both", expand=True)
        self.rc = tk.Canvas(wrap, bg=PAPER, highlightthickness=0, bd=0)
        sb = tk.Scrollbar(wrap, orient="vertical", command=self.rc.yview, width=10)
        self.rc.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.rc.pack(side="left", fill="both", expand=True)
        self.rows = tk.Frame(self.rc, bg=PAPER)
        self._win = self.rc.create_window((0, 0), window=self.rows, anchor="nw")
        self.rows.bind("<Configure>",
                       lambda e: self.rc.configure(scrollregion=self.rc.bbox("all")))
        self.rc.bind("<Configure>", lambda e: self.rc.itemconfig(self._win, width=e.width))
        self.rc.bind_all("<MouseWheel>", self._wheel)

        kicker(right, "preview").pack(fill="x")
        frame = tk.Frame(right, bg=HAIRLINE, bd=0)
        frame.pack(fill="both", expand=True, pady=(4, 0))
        # a Canvas, not a Label: a Label sized to its image fights the layout and clips
        self.pv = tk.Canvas(frame, bg=PAPER, highlightthickness=0, bd=0)
        self.pv.pack(fill="both", expand=True, padx=1, pady=1)
        self.pv.bind("<Configure>", lambda e: self.fit())
        self._photo = None

        nav = tk.Frame(right, bg=PAPER)
        nav.pack(pady=(12, 0))
        arrow = dict(font=sans(11), fg=MUTED, bg=PAPER, cursor="hand2")
        prev = tk.Label(nav, text="◀", **arrow)
        prev.pack(side="left", padx=8)
        prev.bind("<Button-1>", lambda e: self.step(-1))
        self.counter = tk.Label(nav, text="—", font=sans(9), fg=FAINT, bg=PAPER, width=12)
        self.counter.pack(side="left")
        nxt = tk.Label(nav, text="▶", **arrow)
        nxt.pack(side="left", padx=8)
        nxt.bind("<Button-1>", lambda e: self.step(1))
        self.print_one = UnderlineAction(nav, "Print this one →", self.print_current)
        self.print_one.pack(side="left", padx=(24, 0))
        self.print_one.enable(False)

        foot = tk.Frame(outer, bg=PAPER)
        foot.pack(fill="x", pady=(20, 0))
        BrandMark(foot, 12).pack(side="left")
        self.foot_lbl = tk.Label(foot, font=sans(8), fg=FAINT, bg=PAPER)
        self.foot_lbl.pack(side="left", padx=6)
        self.undo_lbl = tk.Label(foot, text="", font=sans(8), fg=ACCENT, bg=PAPER,
                                 cursor="hand2")
        self.undo_lbl.pack(side="right")
        self.undo_lbl.bind("<Button-1>", lambda e: self.undo())
        self.status = tk.Label(foot, text="", font=sans(8), fg=FAINT, bg=PAPER, anchor="e")
        self.status.pack(side="right", padx=8)

        root.bind("<Left>", lambda e: self.step(-1))
        root.bind("<Right>", lambda e: self.step(1))
        self.apply_cfg()

    def build_preset_menu(self):
        """Rebuilt whenever presets are added, renamed or removed."""
        for w in self.preset_holder.winfo_children():
            w.destroy()
        names = config.names(self.cfg)
        self.preset_var.set(self.cfg["active"])
        om = tk.OptionMenu(self.preset_holder, self.preset_var, *names,
                           command=self.switch_preset)
        om.config(font=sans(9), bg=PAPER, fg=INK, activebackground=ACCENT_TINT,
                  activeforeground=INK, relief="solid", bd=1, highlightthickness=0,
                  anchor="w", padx=8, pady=3)
        om["menu"].config(font=sans(9), bg=PAPER, fg=INK,
                          activebackground=ACCENT_TINT, activeforeground=ACCENT, bd=0)
        om.pack(fill="x")

    def switch_preset(self, name):
        if name == self.cfg["active"]:
            return
        self.cfg["active"] = name
        try:
            config.save(self.cfg)
        except OSError:
            pass  # switching still works this session even if the file is unwritable
        self.preset = config.active(self.cfg)
        self.apply_cfg()
        if self.addrs:
            self.refresh(select=self.i)
        self.say(f"Switched to {name}.")

    def apply_cfg(self):
        brand = self.preset["brand_name"].strip()
        self.kick.config(text=" ".join((brand or "envelopes").upper()))
        self.foot_lbl.config(text=f"© 2026 {brand}." if brand else "")
        p = self.cfg["printer"].strip()
        self.printer_lbl.config(text=p or "Not set — open Settings",
                                fg=INK if p else MUTED)

    def _wheel(self, e):
        # bind_all is application-global, so the wheel would otherwise scroll this
        # list while the pointer is over a dialog sitting on top of it
        if e.widget.winfo_toplevel() is not self.root:
            return
        if self.rows.winfo_height() > self.rc.winfo_height():
            self.rc.yview_scroll(-1 if e.delta > 0 else 1, "units")

    def say(self, msg, undo=False):
        self.status.config(text=msg)
        self.undo_lbl.config(text="Undo" if undo else "")

    def settings(self):
        d = SettingsDialog(self.root, self.cfg)
        self.root.wait_window(d)
        if d.result:
            self.cfg = d.result
            self.preset = config.active(self.cfg)
            self.build_preset_menu()
            self.apply_cfg()
            if self.addrs:
                self.refresh(select=self.i)
            self.say("Settings saved.")

    def driver_setup(self):
        p = self.cfg["printer"].strip()
        if not p:
            self.settings()
            return
        subprocess.Popen(["rundll32", "printui.dll,PrintUIEntry", "/e", "/n", p])

    # --- sources -------------------------------------------------------------

    def browse(self):
        p = filedialog.askopenfilename(title="TCGplayer shipping export",
                                       filetypes=[("CSV", "*.csv"), ("All files", "*.*")])
        if not p:
            return
        try:
            got = list(envelopes.rows(p))
        except Exception as e:
            messagebox.showerror("Could not read that CSV", str(e))
            return
        if not got:
            messagebox.showwarning("Nothing to print", "No addresses in that file.")
            return
        self.addrs = got
        self.csv_name, self.manual, self._undo = Path(p).name, 0, None
        self.refresh(select=0)

    def add_address(self):
        d = AddressDialog(self.root)
        self.root.wait_window(d)
        if d.result:
            self.addrs.append(d.result)
            self.manual += 1
            self.refresh(select=len(self.addrs) - 1)
            self.say(f"Added {d.result[0]}.")

    def remove_selected(self):
        if not self.addrs:
            return
        i = self.i
        self._undo = (i, self.addrs[i], self.manual)
        name = self.addrs[i][0]
        del self.addrs[i]
        if self.manual:
            self.manual -= 1
        if not self.addrs:
            self.reset()
        else:
            self.refresh(select=min(i, len(self.addrs) - 1))
        self.say(f"Removed {name}.", undo=True)

    def undo(self):
        if not self._undo:
            return
        i, addr, manual = self._undo
        self.addrs.insert(i, addr)
        self.manual, self._undo = manual, None
        self.refresh(select=i)
        self.say("Restored.")

    def source_text(self):
        if self.csv_name and self.manual:
            return f"{self.csv_name} + {self.manual} by hand"
        if self.csv_name:
            return self.csv_name
        if self.manual:
            return f"{self.manual} address{'es' if self.manual != 1 else ''} entered by hand"
        return "No file selected"

    def reset(self):
        self.pages, self.i = [], 0
        self.csv_name, self.manual = None, 0
        self.hero.config(text="")
        self.hero_sub.config(text="Nothing loaded yet")
        self.src.config(text=self.source_text(), fg=MUTED)
        self.counter.config(text="—")
        for w in self.rows.winfo_children():
            w.destroy()
        self.row_widgets = []
        self.remove_act.enable(False)
        self.print_one.enable(False)
        self.arm_print(False)
        self.fit()

    # --- render --------------------------------------------------------------

    def refresh(self, select=0):
        self.say(f"Rendering {len(self.addrs)}…")
        try:
            envelopes.render(OUT, self.addrs, self.preset)
        except Exception as e:
            # a corrupt logo or a locked output file shouldn't take the app down
            self.say("")
            messagebox.showerror(
                "Could not build the envelopes",
                f"{e}\n\nIf you just changed the logo, try a different file, "
                "or clear it in Settings.")
            return
        self.pages.clear()
        doc = fitz.open(OUT)
        for page in doc:
            pix = page.get_pixmap(dpi=PREVIEW_DPI)
            im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            # pages are Com-10 portrait with the design rotated on; turn it back so
            # the preview reads the way the envelope will in your hand
            self.pages.append(im.rotate(-90, expand=True))
        doc.close()

        n = len(self.pages)
        self.src.config(text=self.source_text(), fg=INK)
        self.hero.config(text=str(n))
        self.hero_sub.config(text=f"envelope{'s' if n != 1 else ''} ready to print")
        self.build_rows()
        self.remove_act.enable(True)
        self.print_one.enable(True)
        self.i = max(0, min(select, n - 1))
        self.show()
        self.arm_print(True)
        self.say("")

    def arm_print(self, on):
        if on:
            self.print_btn.config(bg=ACCENT, fg=ON_ACCENT, cursor="hand2",
                                  text=f"Print all ({len(self.pages)})")
        else:
            self.print_btn.config(bg=HAIRLINE, fg=FAINT, cursor="arrow", text="Print all")

    def build_rows(self):
        for w in self.rows.winfo_children():
            w.destroy()
        self.row_widgets = []
        for idx, a in enumerate(self.addrs):
            # constant inset: the accent gutter always exists, only its colour changes
            row = tk.Frame(self.rows, bg=PAPER, cursor="hand2")
            row.pack(fill="x")
            bar = tk.Frame(row, width=2, bg=PAPER)
            bar.pack(side="left", fill="y")
            lbl = tk.Label(row, text=a[0].title(), font=sans(9), fg=INK, bg=PAPER,
                           anchor="w", padx=8, pady=5)
            lbl.pack(side="left", fill="x", expand=True)
            for w in (row, lbl):
                w.bind("<Button-1>", lambda e, i=idx: self.goto(i))
            hairline(self.rows).pack(fill="x")
            self.row_widgets.append((row, bar, lbl))

    def paint_rows(self):
        for idx, (row, bar, lbl) in enumerate(getattr(self, "row_widgets", [])):
            on = idx == self.i
            bg = ACCENT_TINT if on else PAPER
            row.config(bg=bg)
            bar.config(bg=ACCENT if on else bg)
            lbl.config(bg=bg, fg=ACCENT if on else INK)

    def goto(self, i):
        self.i = i
        self.show()

    def fit(self):
        self.pv.delete("all")
        cw, ch = self.pv.winfo_width(), self.pv.winfo_height()
        if cw < 2 or ch < 2:
            return
        if not self.pages:
            self.pv.create_text(cw // 2, ch // 2, fill=FAINT, font=sans(10),
                                text="Choose a CSV, or add an address by hand.")
            return
        im = self.pages[self.i]
        s = min((cw - 16) / im.width, (ch - 16) / im.height, 1.0)
        self._photo = ImageTk.PhotoImage(
            im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))), Image.LANCZOS))
        self.pv.create_image(cw // 2, ch // 2, image=self._photo)

    def show(self):
        if not self.pages:
            return
        self.counter.config(text=f"{self.i + 1} of {len(self.pages)}")
        self.paint_rows()
        self.fit()

    def step(self, d):
        if self.pages:
            self.i = (self.i + d) % len(self.pages)
            self.show()

    # --- print ---------------------------------------------------------------

    def print_all(self):
        if not self.pages:
            return
        n = len(self.pages)
        if not messagebox.askyesno(
            "Print",
            f"Send {n} envelope{'s' if n != 1 else ''} to “{self.cfg['printer'].strip()}”?\n\n"
            "Envelopes go in the manual / multi-purpose tray, printing surface up — "
            "most trays hold about 10.\n\n"
            "If your printer has envelope levers behind the back cover, set them "
            "first, or the envelopes will come out creased.",
        ):
            return
        self._print(None, f"Sent {n} to")

    def print_current(self):
        """Reprint just the shown envelope — the after-a-jam path. No confirm:
        it costs one envelope and jams want a fast retry."""
        if self.pages:
            self._print(str(self.i + 1), f"Sent envelope {self.i + 1} to")

    def _print(self, pages, done_msg):
        queue = self.cfg["printer"].strip()
        if not queue:
            messagebox.showinfo("No printer set",
                                "Open Settings and choose a printer first.")
            return
        if not SUMATRA.exists():
            messagebox.showerror("Missing print engine", f"Not found:\n{SUMATRA}")
            return
        if self._printing:
            self.say("Still sending the previous job…")
            return
        # print from a snapshot, so a re-render (preset switch, add/remove) can't
        # rewrite the file while SumatraPDF is reading it
        try:
            shutil.copy2(OUT, SNAP)
        except OSError as e:
            messagebox.showerror("Print failed", str(e))
            return
        settings = "noscale" if pages is None else f"{pages},noscale"
        self._printing = True
        self.say("Printing…")
        result = {}

        def work():  # off the Tk thread — spooling a batch can take a while
            result["r"] = subprocess.run(
                [str(SUMATRA), "-print-to", queue, "-print-settings", settings,
                 "-silent", str(SNAP)],
                capture_output=True, text=True, creationflags=NO_WINDOW)

        t = threading.Thread(target=work, daemon=True)
        t.start()

        def poll():  # all UI happens back on the Tk thread
            if t.is_alive():
                self.root.after(200, poll)
                return
            self._printing = False
            r = result["r"]
            if r.returncode == 0:
                self.say(f"{done_msg} {queue}.")
            else:
                self.say("Print failed.")
                messagebox.showerror(
                    "Print failed",
                    f"SumatraPDF exited {r.returncode}.\n{r.stderr or r.stdout}")

        self.root.after(200, poll)


if __name__ == "__main__":
    claim_taskbar_identity()
    load_fonts()
    first_run = not config.exists()
    root = tk.Tk()
    root.geometry("1080x720")
    root.minsize(980, 660)
    try:
        root.iconbitmap(default=str(ICON))  # title bar / Toplevels
    except tk.TclError as e:
        print("iconbitmap failed:", e)
    set_window_icon(root)  # and the per-window icon, which is what the taskbar reads
    app = App(root)

    def on_close():
        # the batch PDF holds customer names and addresses — don't leave it in TEMP
        try:
            OUT.unlink(missing_ok=True)
            SNAP.unlink(missing_ok=True)
        except OSError:
            pass
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    if first_run:
        root.after(400, app.settings)
    root.mainloop()
