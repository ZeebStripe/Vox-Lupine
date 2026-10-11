"""EQ tab: draggable 5-point EQ drawn on a frequency graph.

Drag a point: left/right = frequency, up/down = boost/cut.
Mouse wheel on a point = narrower/wider. Double-click a point = back to 0 dB.
Bold orange line = your draggable EQ. Faint line = total EQ shape of the whole voice
(cleanup rumble filter, warmth, brightness, preset, tone sliders and this EQ).
"""
import copy
import json
import time
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

import numpy as np

from bright import Bright
from engine import DATA
from fx import Effects
from peq import EQ_PRESETS, FREQ_MAX, FREQ_MIN, bands_response_db, clamp_band, response_db
from pedalboard import (HighpassFilter, HighShelfFilter, LowpassFilter, LowShelfFilter,
                        PeakFilter)
from presets import PRESETS
from warmth import Warmth

W, H = 368, 248
PAD_L, PAD_R, PAD_T, PAD_B = 30, 8, 26, 18   # top strip holds the readout text
HINT = "Drag points · wheel = wider/narrower · double-click = reset"
DB_RANGE = 18.0
FREQS = np.geomspace(FREQ_MIN, FREQ_MAX, 220)
COLORS = ["#e74c3c", "#f39c12", "#2ecc71", "#3498db", "#9b59b6"]
NAMES = ["bass shelf", "bell", "bell", "bell", "treble shelf"]
LINEAR = (HighpassFilter, LowpassFilter, LowShelfFilter, HighShelfFilter, PeakFilter)
BASE_KEYS = ("preset", "warmth", "bright", "cleanup", "eq_bass", "eq_mid", "eq_treble")
USER_EQ_PRESETS = DATA / "eq_presets.json"   # your own saved EQ shapes
SEND_MS = 30   # max rate of live updates while dragging
DRAW_MS = 33   # max redraw rate (~30 fps)


def f_to_x(f):
    return PAD_L + (np.log10(f) - np.log10(FREQ_MIN)) / np.log10(FREQ_MAX / FREQ_MIN) * (W - PAD_L - PAD_R)


def x_to_f(x):
    frac = (x - PAD_L) / (W - PAD_L - PAD_R)
    return FREQ_MIN * (FREQ_MAX / FREQ_MIN) ** frac


def db_to_y(db):
    mid = PAD_T + (H - PAD_T - PAD_B) / 2
    return mid - db / DB_RANGE * (H - PAD_T - PAD_B) / 2


def y_to_db(y):
    mid = PAD_T + (H - PAD_T - PAD_B) / 2
    return (mid - y) / ((H - PAD_T - PAD_B) / 2) * DB_RANGE


def _bands_key(bands):
    return tuple((b["type"], round(b["freq"], 1), round(b["gain"], 2), round(b["q"], 3))
                 for b in bands)


def load_user_eq_presets():
    return json.loads(USER_EQ_PRESETS.read_text()) if USER_EQ_PRESETS.exists() else {}


def save_user_eq_presets(p):
    USER_EQ_PRESETS.write_text(json.dumps(p, indent=2) + "\n")


def fmt_freq(f):
    return f"{f / 1000:.2f} kHz" if f >= 1000 else f"{f:.0f} Hz"


class EQGraph:
    def __init__(self, parent, app):
        self.app = app
        self.sr = app.engine.cfg["samplerate"]
        self.bands = copy.deepcopy(app.engine.cfg["peq"])
        self.drag = self.hover = None
        self.send_pending = False
        self.draw_pending = False
        self.last_draw = 0.0
        self._base_key, self._base_curve = None, None
        self._peq_key, self._peq_curve = None, None

        self.user_presets = load_user_eq_presets()
        top = ttk.Frame(parent)
        top.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        ttk.Label(top, text="EQ preset").pack(side="left")
        self.preset_var = tk.StringVar()
        self.preset_cb = ttk.Combobox(top, textvariable=self.preset_var, state="readonly", width=17)
        self.preset_cb.pack(side="left", padx=6)
        self.preset_cb.bind("<<ComboboxSelected>>", lambda e: self.apply_preset(self.preset_var.get()))
        ttk.Button(top, text="Save as…", command=self.save_preset).pack(side="left")
        self.del_btn = ttk.Button(top, text="Delete", command=self.delete_preset)
        self.del_btn.pack(side="left", padx=(6, 0))
        self._refresh_preset_list()

        self.c = tk.Canvas(parent, width=W, height=H, bg="#1b1b1b", highlightthickness=0)
        self.c.grid(row=1, column=0, columnspan=3, pady=(0, 4))
        self._build_items()
        row = ttk.Frame(parent)
        row.grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.on_var = tk.BooleanVar(value=app.engine.cfg["peq_on"])
        ttk.Checkbutton(row, text="EQ on", variable=self.on_var,
                        command=lambda: self._set_on(self.on_var.get())).pack(side="left")
        self.total_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="Show whole-voice curve", variable=self.total_var,
                        command=self.redraw).pack(side="left", padx=(12, 0))
        ttk.Button(row, text="Flatten", command=self.flatten).pack(side="left", padx=(12, 0))

        self.c.bind("<Button-1>", self._press)
        self.c.bind("<B1-Motion>", self._motion)
        self.c.bind("<ButtonRelease-1>", self._release)
        self.c.bind("<Double-Button-1>", self._double)
        self.c.bind("<Motion>", self._hover)
        self.c.bind("<Leave>", lambda e: self._set_hover(None))
        self.c.bind("<MouseWheel>", self._wheel)
        self.redraw()

    # ----- sync with app -----
    def refresh_from_cfg(self):
        """Called when settings change from elsewhere (revert, scenes, reset)."""
        self.bands = copy.deepcopy(self.app.engine.cfg["peq"])
        self.on_var.set(self.app.engine.cfg["peq_on"])
        self._show_preset_name()
        self.redraw()

    def _set_on(self, on):
        self.app.change("peq_on", on)
        self.redraw()

    def flatten(self):
        self.apply_preset("Flat")

    def _send_now(self):
        self.send_pending = False
        self.app.change("peq", self.bands)
        self._show_preset_name()
        self.redraw()

    # ----- EQ presets -----
    def _all_presets(self):
        return {**EQ_PRESETS, **self.user_presets}

    def _refresh_preset_list(self):
        names = list(EQ_PRESETS) + [f"{n}" for n in self.user_presets]
        self.preset_cb.config(values=names)
        self._show_preset_name()

    def _show_preset_name(self):
        """Show which preset the current points match, or 'Custom' after edits."""
        cur = _bands_key(self.bands)
        name = next((n for n, b in self._all_presets().items() if _bands_key(b) == cur), "Custom")
        if self.preset_var.get() != name:
            self.preset_var.set(name)
        self.del_btn.config(state="normal" if name in self.user_presets else "disabled")

    def apply_preset(self, name):
        bands = self._all_presets().get(name)
        if bands is None:
            return
        self.bands = [clamp_band(b) for b in copy.deepcopy(bands)]
        if not self.on_var.get():            # picking a preset implies you want to hear it
            self.on_var.set(True)
            self.app.change("peq_on", True)
        self._send_now()

    def save_preset(self):
        name = simpledialog.askstring("Save EQ preset", "Name for this EQ:", parent=self.c)
        if not name or not name.strip():
            return
        name = name.strip()
        if name in EQ_PRESETS:
            messagebox.showerror("Save EQ preset", f"'{name}' is a built-in preset; pick another name.")
            return
        if name in self.user_presets and not messagebox.askyesno("Save EQ preset", f"Overwrite '{name}'?"):
            return
        self.user_presets[name] = copy.deepcopy(self.bands)
        save_user_eq_presets(self.user_presets)
        self._refresh_preset_list()

    def delete_preset(self):
        name = self.preset_var.get()
        if name in self.user_presets and messagebox.askyesno("Delete EQ preset", f"Delete '{name}'?"):
            del self.user_presets[name]
            save_user_eq_presets(self.user_presets)
            self._refresh_preset_list()

    def _send_soon(self):
        """Throttle live updates while dragging; the graph itself redraws every move."""
        if not self.send_pending:
            self.send_pending = True
            self.c.after(SEND_MS, self._send_now)

    # ----- mouse -----
    def _point_at(self, x, y, radius=14):
        best, dist = None, radius
        for i, b in enumerate(self.bands):
            d = np.hypot(f_to_x(b["freq"]) - x, db_to_y(b["gain"]) - y)
            if d <= dist:
                best, dist = i, d
        return best

    def _press(self, e):
        self.drag = self._point_at(e.x, e.y)
        self._set_hover(self.drag, force=True)

    def _motion(self, e):
        if self.drag is None:
            return
        b = clamp_band(dict(self.bands[self.drag], freq=x_to_f(e.x),
                            gain=round(y_to_db(e.y) * 2) / 2))
        if b == self.bands[self.drag]:
            return                      # moved less than one step: nothing changed
        self.bands[self.drag] = b
        self._set_hover(self.drag, force=True)
        self._send_soon()

    def _release(self, e):
        if self.drag is not None:
            self._send_now()
        self.drag = None

    def _double(self, e):
        i = self._point_at(e.x, e.y)
        if i is not None:
            self.bands[i] = dict(self.bands[i], gain=0.0)
            self._set_hover(i, force=True)
            self._send_now()

    def _wheel(self, e):
        i = self._point_at(e.x, e.y, radius=30)
        if i is None:
            return
        b = self.bands[i]
        self.bands[i] = clamp_band(dict(b, q=b["q"] * (1.15 ** (e.delta / 120))))
        self._set_hover(i, force=True)
        self._send_now()

    def _hover(self, e):
        if self.drag is None:
            self._set_hover(self._point_at(e.x, e.y))

    def _set_hover(self, i, force=False):
        if i == self.hover and not force:
            return                      # plain mouse movement: nothing to update
        self.hover = i
        # The readout lives inside the fixed-size canvas: changing it can't resize the
        # window. (As a label below the graph, its changing width made the window jump.)
        if i is None:
            text = HINT
        else:
            b = self.bands[i]
            text = (f"Point {i + 1} ({NAMES[i]}) · {fmt_freq(b['freq'])} · "
                    f"{b['gain']:+.1f} dB · Q {b['q']:.2f}")
        self.c.itemconfigure(self.readout, text=text)
        self.redraw()

    # ----- curves -----
    def _base(self):
        """Total EQ of the rest of the voice chain (cached; only linear filters)."""
        cfg = self.app.engine.cfg
        key = tuple(cfg[k] for k in BASE_KEYS)
        if key != self._base_key:
            sr, filters = self.sr, []
            if cfg["cleanup"]:
                filters += [HighpassFilter(cutoff_frequency_hz=80), HighpassFilter(cutoff_frequency_hz=80)]
            if cfg["warmth"]:
                filters += list(Warmth(sr, amount=cfg["warmth"]).eq)
            if cfg["bright"]:
                filters += list(Bright(sr, amount=cfg["bright"]).eq)
            if cfg["preset"] in PRESETS:
                filters += [p for p in PRESETS[cfg["preset"]]() if isinstance(p, LINEAR)]
            fx = Effects(sr, eq_bass=cfg["eq_bass"], eq_mid=cfg["eq_mid"], eq_treble=cfg["eq_treble"])
            if cfg["eq_bass"] or cfg["eq_mid"] or cfg["eq_treble"]:
                filters += list(fx.eq)
            self._base_key, self._base_curve = key, response_db(filters, sr, FREQS)
        return self._base_curve

    def _peq(self):
        """This EQ's curve, recomputed only when a point actually changes."""
        key = tuple((b["type"], b["freq"], b["gain"], b["q"]) for b in self.bands)
        if key != self._peq_key:
            self._peq_key = key
            self._peq_curve = bands_response_db(self.bands, self.sr, FREQS)
        return self._peq_curve

    # ----- drawing -----
    # The grid is drawn once; afterwards only the curves and points are moved, and
    # redraw requests are merged into one per idle cycle. (Redrawing everything on
    # every mouse move made the window stutter.)
    def _build_items(self):
        c = self.c
        for f in (50, 100, 200, 500, 1000, 2000, 5000, 10000):
            x = f_to_x(f)
            c.create_line(x, PAD_T, x, H - PAD_B, fill="#2c2c2c")
            c.create_text(x, H - 9, text=f"{f // 1000}k" if f >= 1000 else str(f), fill="#777",
                          font=("Segoe UI", 7))
        for db in (-12, -6, 0, 6, 12):
            y = db_to_y(db)
            c.create_line(PAD_L, y, W - PAD_R, y, fill="#3a3a3a" if db == 0 else "#2c2c2c")
            c.create_text(PAD_L - 4, y, text=f"{db:+d}" if db else "0", anchor="e", fill="#777",
                          font=("Segoe UI", 7))
        flat = [PAD_L, db_to_y(0), W - PAD_R, db_to_y(0)]
        self.total_line = c.create_line(*flat, fill="#6c7a89", width=1)
        self.peq_line = c.create_line(*flat, fill="#f39c12", width=3)
        self.dots = [c.create_oval(0, 0, 0, 0, width=2) for _ in range(5)]
        self.nums = [c.create_text(0, 0, text=str(i + 1), fill="#ddd", font=("Segoe UI", 7, "bold"))
                     for i in range(5)]
        self.off_text = c.create_text(W / 2, PAD_T + 12, text="EQ off", fill="#aaa",
                                      font=("Segoe UI", 9, "bold"), state="hidden")
        self.readout = c.create_text(PAD_L, 13, text=HINT, anchor="w", fill="#aaa",
                                     font=("Segoe UI", 8))

    def redraw(self):
        """Request a redraw; requests are merged and capped at ~30 per second."""
        if not self.draw_pending:
            self.draw_pending = True
            wait = max(0, int(DRAW_MS - (time.perf_counter() - self.last_draw) * 1000))
            self.c.after(wait, self._draw)

    def _draw(self):
        self.draw_pending = False
        self.last_draw = time.perf_counter()
        c = self.c
        on = self.on_var.get()
        peq = self._peq()
        xs = f_to_x(FREQS)

        def line(curve):
            return np.column_stack([xs, np.clip(db_to_y(curve), PAD_T, H - PAD_B)]).ravel().tolist()

        if self.total_var.get():
            base = self._base()
            # Anchor the rest of the chain at 0 dB @ 1 kHz (its overall gain isn't the point),
            # then add this EQ on top so both curves line up.
            total = base - np.interp(1000, FREQS, base) + (peq if on else 0)
            c.coords(self.total_line, *line(total))
            c.itemconfigure(self.total_line, state="normal")
        else:
            c.itemconfigure(self.total_line, state="hidden")
        c.coords(self.peq_line, *line(peq))
        c.itemconfigure(self.peq_line, fill="#f39c12" if on else "#7f6a45")
        for i, b in enumerate(self.bands):
            x, y = f_to_x(b["freq"]), db_to_y(b["gain"])
            hot = i in (self.hover, self.drag)
            r = 8 if hot else 6
            c.coords(self.dots[i], x - r, y - r, x + r, y + r)
            c.itemconfigure(self.dots[i], fill=COLORS[i] if on else "#555",
                            outline="white" if hot else "")
            ny = y - r - 7
            c.coords(self.nums[i], x, ny if ny > PAD_T + 2 else y + r + 7)  # keep out of readout strip
        c.tag_raise(self.peq_line)
        for d, n in zip(self.dots, self.nums):
            c.tag_raise(d)
            c.tag_raise(n)
        c.itemconfigure(self.off_text, state="hidden" if on else "normal")
