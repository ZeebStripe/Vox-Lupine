"""Vox Lupine: small control window for the live voice chain.

Tabs: Voice (your base sound), EQ (draggable graph), Effects (modulation & friends), Devices.
Changes apply instantly. "Save settings" stores them in config.json for next time;
"Presets" (menu, dropdown, or Ctrl+Alt+1-9 from anywhere) are named snapshots of the
sound you can flip between; they're stored in scenes.json.
"""
import copy
import os
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from about import APP_NAME
from engine import (SCENE_KEYS, SCENES, Engine, device_names, load_config, load_scenes,
                    save_config, save_scenes)
from eqgraph import BASE_KEYS, EQGraph
from hotkeys import MUTE, GlobalHotkeys
from micaccess import mic_access_problems
from fx import DEFAULTS as FX_DEFAULTS, MOD_TYPES
from presets import PRESETS

PRESET_CHOICES = ["none"] + list(PRESETS)
def resource(rel):
    """Path to a bundled file, both from source and inside the built .exe."""
    import sys
    from pathlib import Path
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / rel


DEFAULT_DEVICE = "Windows default"
METER_MIN_DB = -60.0
METER_W = 240


class App:
    def __init__(self, root):
        self.root = root
        self.engine = Engine(load_config())
        self.saved = dict(self.engine.cfg)
        self.scenes = load_scenes()
        if not SCENES.exists():
            self._seed_starters()
        self.active_scene = None
        self.hotkeys = GlobalHotkeys()
        self.hotkeys_var = tk.BooleanVar(value=self.engine.cfg["hotkeys"])
        self.sliders, self.checks, self.combos = {}, {}, {}
        root.title(APP_NAME)
        icon = resource("assets/vox-lupine.ico")
        if icon.exists():
            root.iconbitmap(default=str(icon))
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        style = ttk.Style()
        style.configure("Big.TButton", font=("Segoe UI", 11, "bold"), padding=8)
        style.configure("Head.TLabel", font=("Segoe UI", 10, "bold"))
        style.configure("Hint.TLabel", foreground="#777")
        # Big, obvious tabs (the small default ones are easy to miss).
        style.configure("TNotebook.Tab", font=("Segoe UI", 11, "bold"), padding=(18, 6))

        top = ttk.Frame(root, padding=(14, 14, 14, 6))
        top.grid(sticky="ew")
        top.columnconfigure(1, weight=1)
        self.run_btn = ttk.Button(top, text="▶  Start", style="Big.TButton", command=self.toggle)
        self.run_btn.grid(row=0, column=0, columnspan=2, sticky="ew")
        # tk.Button (not ttk) so it can turn red while muted.
        self.mute_btn = tk.Button(top, text="Mute", font=("Segoe UI", 11, "bold"), width=8,
                                  relief="groove", command=self.toggle_mute)
        self.mute_btn.grid(row=0, column=2, sticky="nsew", padx=(6, 0))
        self.mute_default_bg = self.mute_btn.cget("bg")
        self.status = ttk.Label(top, text="Stopped", foreground="#777")
        self.status.grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 8))
        self.meters = {}
        for i, (key, label) in enumerate((("in", "Mic in"), ("out", "Voice out")), 2):
            ttk.Label(top, text=label).grid(row=i, column=0, sticky="w")
            c = tk.Canvas(top, width=METER_W, height=12, bg="#222", highlightthickness=0)
            c.grid(row=i, column=1, sticky="ew", padx=6)
            v = ttk.Label(top, text="", width=8)
            v.grid(row=i, column=2, sticky="e")
            self.meters[key] = (c, v)
        toggles = ttk.Frame(top)
        toggles.grid(row=4, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.bypass_var = tk.BooleanVar(value=self.engine.cfg["bypass"])
        ttk.Checkbutton(toggles, text="Bypass (raw voice)", variable=self.bypass_var,
                        command=lambda: self.change("bypass", self.bypass_var.get())).pack(side="left")
        mon = tk.BooleanVar(value=self.engine.cfg["monitor"])
        ttk.Checkbutton(toggles, text="Listen in headphones", variable=mon,
                        command=lambda: (self.change("monitor", mon.get()),
                                         self._show_monitor_volume())).pack(side="left", padx=(16, 0))
        self.checks["monitor"] = mon
        # Headphone volume: only shown while listening. It lives in a fixed-size slot so
        # showing/hiding it never shifts the layout or resizes the window.
        self.mon_slot = ttk.Frame(toggles)
        self.mon_slot.pack(side="left")
        self.mon_vol = ttk.Frame(self.mon_slot)
        var = tk.DoubleVar(value=self.engine.cfg["monitor_volume_db"])
        val = ttk.Label(self.mon_vol, width=6)

        def moved(_=None):
            v = round(var.get() * 2) / 2
            val.config(text=f"{v:+.0f} dB" if v else "0 dB")
            if v != self.engine.cfg["monitor_volume_db"]:
                self.change("monitor_volume_db", v)

        s = ttk.Scale(self.mon_vol, from_=-24, to=6, variable=var, command=moved, length=80)
        s.pack(side="left", padx=(6, 2))
        s.bind("<Double-Button-1>", lambda e: (var.set(0.0), moved()))
        val.pack(side="left")
        moved()
        self.sliders["monitor_volume_db"] = (var, val, "{:+.0f} dB")
        self.mon_vol.update_idletasks()
        self.mon_slot.config(width=self.mon_vol.winfo_reqwidth(),
                             height=self.mon_vol.winfo_reqheight())
        self.mon_slot.pack_propagate(False)
        self._show_monitor_volume()
        self._scene_row(top, 5)
        menubar = tk.Menu(root)
        self.preset_menu = tk.Menu(menubar, tearoff=False)
        menubar.add_cascade(label="Presets", menu=self.preset_menu)
        root.config(menu=menubar)
        self._build_menu()
        if self.hotkeys_var.get():
            self.hotkeys.start()   # a clash is reported only when switched on from the menu

        nb = ttk.Notebook(root)
        nb.grid(row=1, sticky="nsew", padx=14)
        self.nb = nb
        self.eqgraph = None
        self._voice_tab(self._tab(nb, "Voice"))
        self.eqgraph = EQGraph(self._tab(nb, "EQ"), self)
        self._fx_tab(self._tab(nb, "Effects"))
        self.devices_tab = self._tab(nb, "Devices")
        self._device_tab(self.devices_tab)
        self._mic_banner()

        bottom = ttk.Frame(root, padding=14)
        bottom.grid(row=2, sticky="ew")
        ttk.Button(bottom, text="Save settings", command=self.save).pack(side="left")
        ttk.Button(bottom, text="Revert to saved", command=self.revert).pack(side="left", padx=6)
        self.dirty_lbl = ttk.Label(bottom, text="", foreground="#b9770e")
        self.dirty_lbl.pack(side="right")
        # Open at the top of the screen so the bottom (Save) never hides behind the taskbar.
        root.update_idletasks()
        root.geometry(f"+{max(root.winfo_screenwidth() - root.winfo_reqwidth() - 40, 0)}+8")
        self.tick()

    # ---------- layout helpers ----------
    @staticmethod
    def _tab(nb, title):
        f = ttk.Frame(nb, padding=12)
        f.columnconfigure(1, weight=1)
        nb.add(f, text=title)
        return f

    def _slider(self, f, r, key, label, lo, hi, step, fmt):
        ttk.Label(f, text=label).grid(row=r, column=0, sticky="w")
        var = tk.DoubleVar(value=self.engine.cfg[key])
        val = ttk.Label(f, text=fmt.format(var.get()), width=9)

        def moved(_=None):
            v = round(round(var.get() / step) * step, 3)
            val.config(text=fmt.format(v))
            if v != self.engine.cfg[key]:
                self.change(key, v)

        s = ttk.Scale(f, from_=lo, to=hi, variable=var, command=moved, length=220)
        s.grid(row=r, column=1, sticky="ew", padx=6, pady=0)
        val.grid(row=r, column=2, sticky="e")
        # Double-click: back to saved value.
        s.bind("<Double-Button-1>", lambda e: (var.set(self.saved.get(key, lo)), moved()))
        self.sliders[key] = (var, val, fmt)
        return r + 1

    def _check(self, f, r, key, label):
        var = tk.BooleanVar(value=self.engine.cfg[key])
        ttk.Checkbutton(f, text=label, variable=var,
                        command=lambda: self.change(key, var.get())
                        ).grid(row=r, column=0, columnspan=3, sticky="w", pady=(4, 1))
        self.checks[key] = var
        return r + 1

    def _combo(self, f, r, key, label, values, width=12, to_cfg=lambda v: v, from_cfg=lambda v: v):
        ttk.Label(f, text=label).grid(row=r, column=0, sticky="w")
        var = tk.StringVar(value=from_cfg(self.engine.cfg[key]))
        cb = ttk.Combobox(f, textvariable=var, values=values, state="readonly", width=width)
        cb.grid(row=r, column=1, columnspan=2, sticky="w", padx=6, pady=2)
        cb.bind("<<ComboboxSelected>>", lambda e: self.change(key, to_cfg(var.get())))
        self.combos[key] = (var, from_cfg)
        return r + 1

    @staticmethod
    def _head(f, r, text, hint=None):
        ttk.Label(f, text=text, style="Head.TLabel").grid(row=r, column=0, sticky="w", pady=(6, 0))
        if hint:
            ttk.Label(f, text=hint, style="Hint.TLabel").grid(row=r, column=1, columnspan=2,
                                                              sticky="w", pady=(6, 0))
        return r + 1

    # ---------- "no sound from your mic" warning ----------
    BANNER_BG, BANNER_FG = "#fdecea", "#7b241c"

    def _mic_banner(self):
        """Warning shown by tick() when the mic delivers no sound. It's laid *over* the
        top of the tab area instead of inserted into the layout, so the window never
        changes size (inserting it pushed Save below the taskbar on 1080p screens)."""
        self.banner = tk.Frame(self.root, bg=self.BANNER_BG, highlightbackground="#e6a19a",
                               highlightthickness=1)
        self.banner_text = tk.Label(self.banner, bg=self.BANNER_BG, fg=self.BANNER_FG,
                                    justify="left", anchor="w", wraplength=360,
                                    font=("Segoe UI", 9))
        self.banner_text.pack(fill="x", padx=8, pady=(6, 3))
        btns = tk.Frame(self.banner, bg=self.BANNER_BG)
        btns.pack(fill="x", padx=8, pady=(0, 7))
        ttk.Button(btns, text="Sound settings",
                   command=lambda: os.startfile("ms-settings:sound")).pack(side="left")
        ttk.Button(btns, text="Mic privacy",
                   command=lambda: os.startfile("ms-settings:privacy-microphone")).pack(side="left", padx=6)
        ttk.Button(btns, text="Choose mic",
                   command=lambda: self.nb.select(self.devices_tab)).pack(side="left")
        ttk.Button(btns, text="Hide", command=self._hide_banner).pack(side="right")
        self.banner_kind = "ok"

    def _hide_banner(self):
        self.banner.place_forget()      # stays hidden until the situation changes

    def _update_mic_banner(self):
        kind = self.engine.mic_status()
        if kind == self.banner_kind:
            return                     # only touch the window when the situation changes
        self.banner_kind = kind
        if kind == "ok":
            self.banner.place_forget()
            return
        mic = self.engine.names.get("in", "your microphone")
        if kind == "silent":
            problems = mic_access_problems()
            if problems:
                text = ("No sound from your microphone: Windows is blocking it ("
                        + "; ".join(problems) + "). Click Mic privacy, turn access on, "
                        "then press Stop and Start.")
            else:
                text = (f"No sound at all from \"{mic}\". It may be muted, set to 0 volume, "
                        "disabled, or not the mic you're using. Open Sound settings → Input, "
                        "pick your microphone and check that its level bar moves when you talk.")
        else:
            text = (f"Barely any sound from \"{mic}\" so far. Say something: if the Mic in "
                    "meter stays flat, check Sound settings → Input, or choose another mic.")
        self.banner_text.config(text=text)
        # Just below the tab headers, full width of the tab area, on top of its content.
        self.banner.place(in_=self.nb, x=0, y=40, relwidth=1.0)
        self.banner.lift()

    # ---------- tabs ----------
    def _scene_row(self, f, r):
        row = ttk.Frame(f)
        row.grid(row=r, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Label(row, text="Preset").pack(side="left")
        self.scene_var = tk.StringVar()
        self.scene_cb = ttk.Combobox(row, textvariable=self.scene_var, state="readonly", width=16,
                                     values=list(self.scenes))   # menu order = hotkey numbers
        self.scene_cb.pack(side="left", padx=6)
        self.scene_cb.bind("<<ComboboxSelected>>", lambda e: self.load_scene(self.scene_var.get()))
        ttk.Button(row, text="Save as…", command=self.save_scene).pack(side="left")
        ttk.Button(row, text="Delete", command=self.delete_scene).pack(side="left", padx=(6, 0))

    def _voice_tab(self, f):
        r = 0
        r = self._combo(f, r, "preset", "Base tone", PRESET_CHOICES,
                        to_cfg=lambda v: None if v == "none" else v,
                        from_cfg=lambda v: v or "none")
        r = self._slider(f, r, "warmth", "Warmth", 0, 1.5, 0.05, "{:.2f}")
        r = self._slider(f, r, "bright", "Brightness", 0, 1.5, 0.05, "{:.2f}")
        r = self._slider(f, r, "volume_db", "Volume", -24, 6, 0.5, "{:+.1f} dB")
        r = self._check(f, r, "cleanup", "Noise cleanup (pops, clicks, hiss, harsh s)")
        r = self._head(f, r, "Tone", "extra EQ on top")
        r = self._slider(f, r, "eq_bass", "Bass", -12, 12, 0.5, "{:+.1f} dB")
        r = self._slider(f, r, "eq_mid", "Mid", -12, 12, 0.5, "{:+.1f} dB")
        r = self._slider(f, r, "eq_treble", "Treble", -12, 12, 0.5, "{:+.1f} dB")
        ttk.Label(f, text="Keep Voice out mostly green: chat apps can crackle if it runs hot.",
                  style="Hint.TLabel").grid(row=r, column=0, columnspan=3, sticky="w", pady=(6, 0))

    def _fx_tab(self, f):
        r = self._head(f, 0, "Character")
        r = self._slider(f, r, "grit", "Grit / growl", 0, 1, 0.05, "{:.0%}")
        r = self._slider(f, r, "radio", "Radio", 0, 1, 0.05, "{:.0%}")
        r = self._head(f, r, "Modulation")
        r = self._combo(f, r, "mod_type", "Type", MOD_TYPES)
        r = self._slider(f, r, "mod_amount", "Amount", 0, 1, 0.05, "{:.0%}")
        r = self._slider(f, r, "mod_rate", "Speed", 0, 1, 0.05, "{:.0%}")
        r = self._head(f, r, "Space")
        r = self._slider(f, r, "echo", "Echo", 0, 1, 0.05, "{:.0%}")
        r = self._slider(f, r, "echo_time", "Echo time", 50, 800, 10, "{:.0f} ms")
        r = self._slider(f, r, "reverb", "Reverb", 0, 1, 0.05, "{:.0%}")
        r = self._slider(f, r, "reverb_size", "Room size", 0, 1, 0.05, "{:.0%}")
        ttk.Button(f, text="Reset effects", command=self.reset_fx).grid(
            row=r, column=0, columnspan=3, sticky="w", pady=(10, 0))

    def _device_tab(self, f):
        ins, outs = device_names("input"), device_names("output")
        self.devs = {}
        r = 0
        for key, label, opts in (("input_device", "Mic", [DEFAULT_DEVICE] + ins),
                                 ("output_device", "Send to", outs),
                                 ("monitor_device", "Headphones", [DEFAULT_DEVICE] + outs)):
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="w")
            var = tk.StringVar(value=self._match(self.engine.cfg[key], opts))
            cb = ttk.Combobox(f, textvariable=var, values=opts, state="readonly", width=40)
            cb.grid(row=r, column=1, columnspan=2, sticky="ew", padx=6, pady=2)
            cb.bind("<<ComboboxSelected>>",
                    lambda e, k=key, v=var: self.change(k, "" if v.get() == DEFAULT_DEVICE else v.get()))
            self.devs[key] = var
            r += 1
        if not any("CABLE" in o.upper() for o in outs):
            ttk.Label(f, text="VB-Cable not found: install it to send your voice to OBS/Discord.",
                      foreground="#c0392b").grid(row=r, column=0, columnspan=3, sticky="w", pady=(4, 0))

    @staticmethod
    def _match(name, opts):
        if not name:
            return DEFAULT_DEVICE
        return next((o for o in opts if name.lower() in o.lower()), name)

    # ---------- actions ----------
    def change(self, key, value):
        try:
            self.engine.set(key, value)
        except Exception as e:  # device failed to open
            messagebox.showerror(APP_NAME, str(e))
            self.engine.stop()
        if self.eqgraph and key in BASE_KEYS:
            self.eqgraph.redraw()       # the whole-voice curve depends on these
        self.update_dirty()

    def set_value(self, key, value):
        """Change a setting and move its widget to match."""
        if self.engine.cfg.get(key) != value:
            self.change(key, value)
        if key in self.sliders:
            var, val, fmt = self.sliders[key]
            var.set(value)
            val.config(text=fmt.format(value))
        elif key in self.checks:
            self.checks[key].set(value)
            if key == "monitor":
                self._show_monitor_volume()
        elif key in self.combos:
            var, from_cfg = self.combos[key]
            var.set(from_cfg(value))
        elif key in ("peq", "peq_on") and self.eqgraph:
            self.eqgraph.refresh_from_cfg()
        elif key == "hotkeys" and self.hotkeys_var.get() != value:
            self.hotkeys_var.set(value)
            self._toggle_hotkeys()

    def reset_fx(self):
        for k, v in FX_DEFAULTS.items():
            self.set_value(k, v)

    # ---------- presets (saved sounds, a.k.a. scenes) ----------
    def _current_sound(self):
        return {k: copy.deepcopy(self.engine.cfg[k]) for k in SCENE_KEYS}

    def _seed_starters(self):
        """First run only: a few presets to switch between, built from the saved sound."""
        base = {k: copy.deepcopy(self.saved[k]) for k in SCENE_KEYS}
        def variant(**over):
            return {**copy.deepcopy(base), **over}
        self.scenes = {
            "Normal": variant(),
            "Growl": variant(grit=0.45, eq_bass=3.0, reverb=0.15, reverb_size=0.4),
            "Spooky echo": variant(mod_type="chorus", mod_amount=0.3, echo=0.35, echo_time=320.0,
                                   reverb=0.5, reverb_size=0.8),
            "Radio": variant(radio=1.0, grit=0.15),
            "Robot": variant(mod_type="robot", mod_amount=0.6, mod_rate=0.3),
        }
        save_scenes(self.scenes)

    def _scenes_changed(self):
        save_scenes(self.scenes)
        self.scene_cb.config(values=list(self.scenes))
        if self.active_scene not in self.scenes:
            self.active_scene = None
        self._build_menu()
        self.update_dirty()

    def load_scene(self, name):
        if name not in self.scenes:
            return
        for k, v in self.scenes[name].items():
            self.set_value(k, v)
        self.active_scene = name
        self.scene_var.set(name)
        self._build_menu()          # Update/Rename/Delete now apply to this preset
        self.update_dirty()

    def load_scene_number(self, n):
        names = list(self.scenes)
        if 1 <= n <= len(names):
            self.load_scene(names[n - 1])

    def save_scene(self):
        name = simpledialog.askstring("Save preset", "Name for this sound:", parent=self.root,
                                      initialvalue=self.active_scene or "")
        if not name:
            return
        name = name.strip()
        if name in self.scenes and not messagebox.askyesno("Save preset", f"Overwrite '{name}'?"):
            return
        self.scenes[name] = self._current_sound()
        self.active_scene = name
        self.scene_var.set(name)
        self._scenes_changed()

    def update_scene(self):
        if self.active_scene in self.scenes:
            self.scenes[self.active_scene] = self._current_sound()
            self._scenes_changed()

    def rename_scene(self):
        old = self.active_scene
        if old not in self.scenes:
            return
        new = simpledialog.askstring("Rename preset", f"New name for '{old}':", parent=self.root,
                                     initialvalue=old)
        if not new or new.strip() == old:
            return
        new = new.strip()
        if new in self.scenes:
            messagebox.showerror("Rename preset", f"'{new}' already exists.")
            return
        # Rebuild in the same order so its number (Ctrl+Alt+N) stays the same.
        self.scenes = {(new if k == old else k): v for k, v in self.scenes.items()}
        self.active_scene = new
        self.scene_var.set(new)
        self._scenes_changed()

    def move_scene(self, step):
        names = list(self.scenes)
        if self.active_scene not in names:
            return
        i = names.index(self.active_scene)
        j = min(max(i + step, 0), len(names) - 1)
        names.insert(j, names.pop(i))
        self.scenes = {k: self.scenes[k] for k in names}
        self._scenes_changed()

    def delete_scene(self):
        name = self.active_scene or self.scene_var.get()
        if name in self.scenes and messagebox.askyesno("Delete preset", f"Delete '{name}'?"):
            del self.scenes[name]
            self.active_scene = None
            self.scene_var.set("")
            self._scenes_changed()

    def _scene_modified(self):
        s = self.scenes.get(self.active_scene)
        return s is not None and any(self.engine.cfg.get(k) != v for k, v in s.items())

    def _build_menu(self):
        m = self.preset_menu
        m.delete(0, "end")
        if not self.scenes:
            m.add_command(label="(no presets yet)", state="disabled")
        for n, name in enumerate(self.scenes, 1):
            m.add_radiobutton(label=name, variable=self.scene_var, value=name,
                              command=lambda nm=name: self.load_scene(nm),
                              accelerator=f"Ctrl+Alt+{n}" if n <= 9 else "")
        m.add_separator()
        m.add_command(label="Save current sound as new preset…", command=self.save_scene)
        has = self.active_scene in self.scenes
        st = "normal" if has else "disabled"
        label = f"'{self.active_scene}'" if has else "preset"
        m.add_command(label=f"Update {label} with current sound", command=self.update_scene, state=st)
        m.add_command(label=f"Rename {label}…", command=self.rename_scene, state=st)
        m.add_command(label=f"Move {label} up", command=lambda: self.move_scene(-1), state=st)
        m.add_command(label=f"Move {label} down", command=lambda: self.move_scene(1), state=st)
        m.add_command(label=f"Delete {label}…", command=self.delete_scene, state=st)
        m.add_separator()
        m.add_checkbutton(label="Global hotkeys: Ctrl+Alt+1–9 presets, Ctrl+Alt+0 mute",
                          variable=self.hotkeys_var, command=self._toggle_hotkeys)

    # ---------- global hotkeys ----------
    def _toggle_hotkeys(self):
        on = self.hotkeys_var.get()
        self.engine.cfg["hotkeys"] = on
        if on:
            self.hotkeys.start()
            if self.hotkeys.failed:
                messagebox.showwarning(
                    APP_NAME, "These hotkeys are already used by another program and "
                    "won't work: " + ", ".join(f"Ctrl+Alt+{n}" for n in self.hotkeys.failed))
        else:
            self.hotkeys.stop()
        self.update_dirty()

    def _poll_hotkeys(self):
        while not self.hotkeys.presses.empty():
            n = self.hotkeys.presses.get_nowait()
            if n == MUTE:
                self.toggle_mute()
            else:
                self.load_scene_number(n)

    # ---------- mute / headphone volume ----------
    def toggle_mute(self):
        self.engine.muted = not self.engine.muted
        if self.engine.muted:
            self.mute_btn.config(text="MUTED", bg="#c0392b", fg="white", activebackground="#a93226",
                                 activeforeground="white")
        else:
            self.mute_btn.config(text="Mute", bg=self.mute_default_bg, fg="black",
                                 activebackground=self.mute_default_bg, activeforeground="black")
        self.update_dirty()            # refreshes the window title

    def _show_monitor_volume(self):
        if self.checks["monitor"].get():
            self.mon_vol.pack(side="left", fill="y")
        else:
            self.mon_vol.pack_forget()

    def toggle(self):
        if self.engine.running:
            self.engine.stop()
            return
        try:
            self.engine.start()
        except Exception as e:
            self.engine.stop()
            msg = f"Couldn't start:\n{e}"
            if "cable" in str(e).lower():
                msg += ("\n\nVB-Cable isn't installed. Get it from vb-audio.com/Cable, run "
                        "VBCABLE_Setup_x64.exe as administrator, then restart your PC.\n\n"
                        "To test now, set 'Send to' to your headphones.")
            messagebox.showerror(APP_NAME, msg)

    def save(self):
        save_config(self.engine.cfg)
        self.saved = dict(self.engine.cfg)
        self.update_dirty()

    def revert(self):
        for k, v in self.saved.items():
            if k in self.devs:
                if self.engine.cfg.get(k) != v:
                    self.change(k, v)
                self.devs[k].set(self._match(v, [v]))
            else:
                self.set_value(k, v)
        self.bypass_var.set(self.saved["bypass"])
        self.update_dirty()

    def update_dirty(self):
        dirty = any(self.engine.cfg.get(k) != v for k, v in self.saved.items())
        self.dirty_lbl.config(text="unsaved changes" if dirty else "")
        title = APP_NAME
        if self.active_scene:
            title += f" — {self.active_scene}" + (" (modified)" if self._scene_modified() else "")
        if self.engine.muted:
            title += " — MUTED"
        if self.root.title() != title:
            self.root.title(title)

    def on_close(self):
        if self.dirty_lbl.cget("text") and messagebox.askyesno(
                APP_NAME, "Save your changes before closing?"):
            self.save()
        self.hotkeys.stop()
        self.engine.stop()
        self.root.destroy()

    # ---------- refresh ----------
    @staticmethod
    def _set(widget, **kw):
        """Configure only what changed (avoids needless relayout/repaint every tick)."""
        changed = {k: v for k, v in kw.items() if str(widget.cget(k)) != str(v)}
        if changed:
            widget.config(**changed)

    def tick(self):
        self._poll_hotkeys()
        self._update_mic_banner()
        e = self.engine
        self._set(self.run_btn, text="■  Stop" if e.running else "▶  Start")
        if e.running:
            mon = "  + headphones" if e.cfg["monitor"] else ""
            head = ("MUTED: nobody hears you (Ctrl+Alt+0 to unmute)" if e.muted
                    else f"Running → {e.names.get('out', '?')}{mon}")
            self._set(self.status,
                      text=f"{head}\n"
                           f"delay ~{e.latency_ms():.0f} ms · CPU {e.cpu_ms():.1f} ms/block · "
                           f"glitches {e.underruns + e.drops}",
                      foreground="#c0392b" if e.muted else "#1e8449")
        else:
            self._set(self.status, text="Stopped" + (" · muted" if e.muted else ""),
                      foreground="#777")
        for key, db in (("in", e.in_db), ("out", e.out_db)):
            c, lbl = self.meters[key]
            if not hasattr(c, "bar"):
                c.bar = c.create_rectangle(0, 0, 0, 12, width=0)
            frac = min(max((db - METER_MIN_DB) / -METER_MIN_DB, 0), 1)
            color = "#27ae60" if db < -12 else ("#f1c40f" if db < -3 else "#e74c3c")
            c.coords(c.bar, 0, 0, int(METER_W * frac), 12)
            c.itemconfigure(c.bar, fill=color)
            self._set(lbl, text=f"{db:5.0f} dB" if e.running else "")
        self.root.after(50, self.tick)


def smoke_test(report_path):
    """Open the window, start/stop audio briefly, write a JSON report, exit.
    Used to check a built .exe, which has no console to print to."""
    import json
    import sys
    import time
    import traceback
    from engine import DATA
    report = {"app": APP_NAME, "frozen": bool(getattr(sys, "frozen", False)), "data_dir": str(DATA)}
    try:
        root = tk.Tk()
        a = App(root)
        root.update()
        report["presets"] = list(a.scenes)
        report["tabs_ok"] = True
        try:
            a.engine.start()
            time.sleep(2)
            report["audio"] = {"in": a.engine.names.get("in"), "out": a.engine.names.get("out"),
                               "glitches": a.engine.underruns + a.engine.drops}
        except Exception as e:  # no VB-Cable / no mic on this machine
            report["audio"] = f"not started: {e}"
        finally:
            a.engine.stop()
        a.hotkeys.stop()
        root.destroy()
        report["ok"] = True
    except Exception:
        report["ok"] = False
        report["error"] = traceback.format_exc()
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)


if __name__ == "__main__":
    import sys
    if "--smoke-test" in sys.argv:
        smoke_test(sys.argv[sys.argv.index("--smoke-test") + 1])
    else:
        root = tk.Tk()
        App(root)
        root.mainloop()
