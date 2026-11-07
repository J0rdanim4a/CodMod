"""CodMod 3.0 - open-source mod manager for Call of Duty games with mod support.
Pure Python + tkinter (no third-party packages)."""
import json, os, sys, re, html, time, glob, threading, queue, zipfile, shutil, subprocess, webbrowser
import urllib.request, urllib.parse
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

APP, APP_VERSION = "CodMod", "3.0"
DATA = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP)
CFG = os.path.join(DATA, "config.json")
CACHE = os.path.join(DATA, "downloads")
BACKUPS = os.path.join(DATA, "backups")
DISABLED = os.path.join(DATA, "disabled")
COMMUNITY = os.path.join(DATA, "community_catalog.json")
os.makedirs(CACHE, exist_ok=True)


def res(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


# ------------------------------------------------------------------ config / catalog / theme
def load_cfg():
    try:
        with open(CFG, encoding="utf-8") as fh:
            c = json.load(fh)
    except Exception:
        c = {}
    for k, v in (("dirs", {}), ("installed", []), ("catalog_url", ""), ("github_token", ""), ("theme", "Dark"),
                 ("accent", "Orange"), ("profiles", {}), ("launch", {}), ("app_repo", "")):
        c.setdefault(k, v)
    return c


cfg = load_cfg()


def save_cfg():
    os.makedirs(DATA, exist_ok=True)
    with open(CFG, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)


with open(res("catalog.json"), encoding="utf-8") as f:
    CATALOG = json.load(f)
COMMUNITY_MODS = []


def merge_catalog(extra):
    """Merge a community catalog: {"games": [...], "mods": [...]} or just a list of mods."""
    if isinstance(extra, list):
        extra = {"mods": extra}
    ids = {g["id"]: g for g in CATALOG["games"]}
    for g in extra.get("games", []):
        if g.get("id") in ids:
            ids[g["id"]].update(g)
        elif g.get("id") and g.get("name"):
            g.setdefault("loaders", [])
            g.setdefault("guide", "")
            CATALOG["games"].append(g)
    COMMUNITY_MODS[:] = [m for m in extra.get("mods", []) if m.get("game") and str(m.get("url", "")).startswith("http")]


try:
    with open(COMMUNITY, encoding="utf-8") as fh:
        merge_catalog(json.load(fh))
except Exception:
    pass

THEMES = {
    "Dark": dict(BG="#0d0f13", SIDE="#12151b", CARD="#191d25", CARD_H="#202531", BORDER="#262c38", INPUT="#10131a",
                 BTN="#2a3040", BTN_H="#38405a", TXT="#eceef2", MUTED="#8b93a1"),
    "Light": dict(BG="#f3f4f7", SIDE="#e7e9ef", CARD="#ffffff", CARD_H="#f0f2f8", BORDER="#d5d9e2", INPUT="#ffffff",
                  BTN="#dfe3ec", BTN_H="#cfd5e2", TXT="#1a1d24", MUTED="#5f6877"),
}
ACCENTS = {"Orange": ("#f5a623", "#ffbe4d"), "Blue": ("#3b82f6", "#60a5fa"), "Green": ("#22c55e", "#4ade80"),
           "Red": ("#ef4444", "#f87171"), "Purple": ("#a855f7", "#c084fc")}
GREEN = "#3ddc84"
F, FB = ("Segoe UI", 10), ("Segoe UI", 10, "bold")


def apply_theme():
    global BG, SIDE, CARD, CARD_H, BORDER, INPUT, BTN, BTN_H, TXT, MUTED, ACCENT, ACCENT_H
    t = THEMES.get(cfg["theme"], THEMES["Dark"])
    BG, SIDE, CARD, CARD_H, BORDER, INPUT, BTN, BTN_H, TXT, MUTED = (t[k] for k in (
        "BG", "SIDE", "CARD", "CARD_H", "BORDER", "INPUT", "BTN", "BTN_H", "TXT", "MUTED"))
    ACCENT, ACCENT_H = ACCENTS.get(cfg["accent"], ACCENTS["Orange"])


apply_theme()

# folders that mean "extract into the game root"
ROOT_DIRS = {"mods", "zone", "main", "bin", "players", "raw", "user_scripts", "scripts", "maps", "plugins", "storage",
             "t4", "t5", "t6", "iw4x", "data", "devraw", "miles", "userraw", "video", "sound", "sp", "mp", "zombies",
             "dlc", "localized_english", "localized_german", "localized_french", "localized_italian",
             "localized_spanish", "localized_russian"}
ARCHIVE_EXT = (".7z", ".rar", ".tar", ".gz", ".tgz", ".xz", ".bz2")


# ------------------------------------------------------------------ tasks, http, archives
class Cancelled(Exception):
    pass


class Task:
    _n = 0

    def __init__(self, name, fn, args):
        Task._n += 1
        self.id, self.name, self.fn, self.args = Task._n, name, fn, args
        self.status, self.progress, self.error = "Queued", 0.0, ""
        self.paused = self.cancelled = False


_tl = threading.local()


def http_get(url, dest=None, progress=None):
    h = {"User-Agent": f"CodMod/{APP_VERSION}"}
    if "api.github.com" in url and cfg.get("github_token"):
        h["Authorization"] = "Bearer " + cfg["github_token"]
    t = getattr(_tl, "task", None)
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=30) as r:
        if dest is None:
            return r.read()
        total, done = int(r.headers.get("Content-Length") or 0), 0
        with open(dest, "wb") as out:
            while chunk := r.read(65536):
                if t:
                    while t.paused and not t.cancelled:
                        time.sleep(0.2)
                    if t.cancelled:
                        raise Cancelled()
                out.write(chunk)
                done += len(chunk)
                if progress and total:
                    progress(done / total)


def _tar(*args):
    kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
    try:
        return subprocess.run(["tar", *args], capture_output=True, text=True, **kw)
    except OSError as ex:
        raise RuntimeError("Couldn't run 'tar' (needed for .rar/.7z). Windows 10/11 includes it.") from ex


def archive_members(path):
    """Returns (relative file list, kind). kind = 'archive' or 'file'."""
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
    elif path.lower().endswith(ARCHIVE_EXT):
        r = _tar("-tf", path)
        if r.returncode:
            raise RuntimeError("Couldn't read this archive. .rar/.7z need Windows 10/11 (built-in tar). "
                               + r.stderr.strip()[:200])
        names = [n.strip() for n in r.stdout.splitlines() if n.strip() and not n.strip().endswith("/")]
    else:
        return [os.path.basename(path)], "file"
    names = [n.replace("\\", "/") for n in names]
    for n in names:  # path-traversal guard
        if n.startswith("/") or ":" in n or ".." in n.split("/"):
            raise ValueError(f"Unsafe path in archive: {n}")
    return names, "archive"


def extract(path, kind, target):
    os.makedirs(target, exist_ok=True)
    if kind == "file":
        shutil.copy2(path, os.path.join(target, os.path.basename(path)))
    elif zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            z.extractall(target)
    else:
        r = _tar("-xf", path, "-C", target)
        if r.returncode:
            raise RuntimeError("Extraction failed: " + r.stderr.strip()[:200])


def safe(name):
    return re.sub(r"[^\w.-]+", "_", name)[:80] or "mod"


def norm(p):
    return os.path.normcase(os.path.normpath(p))


def vtuple(s):
    return tuple(int(x) for x in re.findall(r"\d+", str(s))[:3])


# ------------------------------------------------------------------ widgets
def mix(a, b, t):
    pa = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    pb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(x + (y - x) * t) for x, y in zip(pa, pb))


def rrect(c, x1, y1, x2, y2, r, **kw):
    p = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2,
         x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return c.create_polygon(p, smooth=True, **kw)


class RBtn(tk.Canvas):
    def __init__(self, parent, text, command, primary=False, w=110, h=34, bg=None):
        super().__init__(parent, width=w, height=h, bg=bg or CARD, highlightthickness=0, cursor="hand2")
        self._bw, self._bh, self._t, self._cmd = w, h, text, command
        self._c1 = ACCENT if primary else BTN
        self._c2 = ACCENT_H if primary else BTN_H
        self._fg = "#14110a" if primary else TXT
        self._paint(self._c1)
        self.bind("<Enter>", lambda e: self._paint(self._c2))
        self.bind("<Leave>", lambda e: self._paint(self._c1))
        self.bind("<Button-1>", lambda e: self._cmd())

    def set_text(self, t):
        self._t = t
        self._paint(self._c1)

    def _paint(self, col):
        self.delete("all")
        rrect(self, 1, 1, self._bw - 1, self._bh - 1, 12, fill=col, outline="")
        self.create_text(self._bw / 2, self._bh / 2, text=self._t, fill=self._fg, font=FB)


class ScrollFrame(tk.Frame):
    current = None

    def __init__(self, parent):
        super().__init__(parent, bg=BG)
        self.c = tk.Canvas(self, bg=BG, highlightthickness=0)
        sb = ttk.Scrollbar(self, orient="vertical", command=self.c.yview, style="Dark.Vertical.TScrollbar")
        self.c.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.c.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.c, bg=BG)
        self.win = self.c.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.c.configure(scrollregion=self.c.bbox("all")))
        self.c.bind("<Configure>", lambda e: self.c.itemconfigure(self.win, width=e.width))
        ScrollFrame.current = self

    def wheel(self, e):
        try:
            self.c.yview_scroll(int(-e.delta / 120) * 3, "units")
        except tk.TclError:
            pass


class Card(tk.Frame):
    def __init__(self, parent, title, sub, buttons, badge=None):
        super().__init__(parent, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        self.pack(fill="x", padx=10, pady=5)
        tk.Frame(self, bg=ACCENT, width=3).pack(side="left", fill="y")
        for b in reversed(buttons):
            RBtn(self, b[0], b[1], b[2], b[3] if len(b) > 3 else 104).pack(side="right", padx=(0, 10), pady=14)
        body = tk.Frame(self, bg=CARD)
        body.pack(side="left", fill="both", expand=True, padx=16, pady=12)
        row = tk.Frame(body, bg=CARD)
        row.pack(fill="x")
        tk.Label(row, text=title, bg=CARD, fg=TXT, font=("Segoe UI", 12, "bold"), anchor="w").pack(side="left")
        if badge:
            tk.Label(row, text="  " + badge[0], bg=CARD, fg=badge[1], font=("Segoe UI", 9, "bold")).pack(side="left")
        tk.Label(body, text=sub, bg=CARD, fg=MUTED, font=F, anchor="w", justify="left", wraplength=400).pack(fill="x", pady=(2, 0))
        self._all = [self] + self._walk(self)
        for w in self._all:
            if not isinstance(w, RBtn):
                w.bind("<Enter>", self._in, add="+")
                w.bind("<Leave>", self._out, add="+")

    def _walk(self, w):
        out = []
        for ch in w.winfo_children():
            out.append(ch)
            out += self._walk(ch)
        return out

    def _paint(self, bg, border):
        for w in self._all:
            if isinstance(w, RBtn) or (isinstance(w, tk.Frame) and w.cget("bg") == ACCENT):
                continue
            try:
                w.configure(bg=bg)
            except tk.TclError:
                pass
        self.configure(highlightbackground=border)

    def _in(self, e):
        self._paint(CARD_H, ACCENT)

    def _out(self, e):
        x, y = self.winfo_pointerxy()
        w = self.winfo_containing(x, y)
        while w is not None:
            if w is self:
                return
            w = w.master
        self._paint(CARD, BORDER)


class Nav(tk.Frame):
    def __init__(self, parent, text, cmd):
        super().__init__(parent, bg=SIDE, cursor="hand2")
        self.bar = tk.Frame(self, bg=SIDE, width=4)
        self.bar.pack(side="left", fill="y")
        self.lbl = tk.Label(self, text=text, bg=SIDE, fg=MUTED, font=("Segoe UI", 11), anchor="w", padx=16, pady=9)
        self.lbl.pack(side="left", fill="x", expand=True)
        self.active = False
        for w in (self, self.lbl):
            w.bind("<Button-1>", lambda e: cmd())
            w.bind("<Enter>", lambda e: self._set(True))
            w.bind("<Leave>", lambda e: self._set(False))

    def _set(self, hover):
        bg = CARD if (hover or self.active) else SIDE
        self.configure(bg=bg)
        self.lbl.configure(bg=bg, fg=ACCENT if self.active else (TXT if hover else MUTED))
        self.bar.configure(bg=ACCENT if self.active else bg)

    def set_active(self, v):
        self.active = v
        self._set(False)


# ------------------------------------------------------------------ app
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"CodMod {APP_VERSION} - Call of Duty Mod Manager")
        self.geometry("1180x740")
        self.minsize(900, 600)
        self.configure(bg=BG)
        self.q = queue.Queue()
        self.tasks, self.dl_rows = [], {}
        self.cur = "games"
        self.gvar = tk.StringVar(value=CATALOG["games"][0]["name"])
        self.gb_rows = []
        self._style()
        self._dark_titlebar()

        side = tk.Frame(self, bg=SIDE, width=220)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        logo = tk.Canvas(side, width=220, height=104, bg=SIDE, highlightthickness=0)
        logo.pack()
        logo.create_polygon(40, 36, 62, 24, 84, 36, 84, 62, 62, 74, 40, 62, outline=ACCENT, fill="", width=2)
        logo.create_text(62, 49, text="C", fill=ACCENT, font=("Segoe UI", 20, "bold"))
        logo.create_text(96, 40, text="COD", anchor="w", fill=TXT, font=("Segoe UI", 20, "bold"))
        logo.create_text(97, 64, text="MOD MANAGER", anchor="w", fill=MUTED, font=("Segoe UI", 8, "bold"))
        self.nav = {}
        for key, label in [("games", "Games"), ("loaders", "Mod Loaders"), ("mods", "Mods"),
                           ("browse", "Browse Online"), ("downloads", "Downloads"), ("installed", "Installed"),
                           ("guide", "Guide"), ("settings", "Settings")]:
            n = Nav(side, label, lambda k=key: self.show(k))
            n.pack(fill="x", pady=1)
            self.nav[key] = n
        tk.Label(side, text=f"v{APP_VERSION}", bg=SIDE, fg=MUTED, font=("Segoe UI", 8)).pack(side="bottom", pady=10)

        right = tk.Frame(self, bg=BG)
        right.pack(side="left", fill="both", expand=True)
        foot = tk.Frame(right, bg=SIDE, height=30)
        foot.pack(side="bottom", fill="x")
        self.status = tk.Label(foot, text="Ready", bg=SIDE, fg=MUTED, font=F, anchor="w")
        self.status.pack(side="left", padx=14)
        self.bar = ttk.Progressbar(foot, style="Gold.Horizontal.TProgressbar", length=220, maximum=1.0)
        self.bar.pack(side="right", padx=14, pady=8)

        self.banner = tk.Canvas(right, height=92, bg=BG, highlightthickness=0)
        self.banner.pack(fill="x")
        self.banner.bind("<Configure>", lambda e: self._banner())
        self.combo = ttk.Combobox(self.banner, textvariable=self.gvar, state="readonly", width=34,
                                  values=[g["name"] for g in CATALOG["games"]], style="Dark.TCombobox")
        self.combo.bind("<<ComboboxSelected>>", lambda e: self.show(self.cur))
        self.content = tk.Frame(right, bg=BG)
        self.content.pack(fill="both", expand=True)
        self.bind_all("<MouseWheel>", lambda e: ScrollFrame.current and ScrollFrame.current.wheel(e))
        self.titles = {"games": "Your Games", "loaders": "Mod Loaders", "mods": "Mods", "browse": "Browse Online",
                       "downloads": "Downloads", "installed": "Installed Mods", "guide": "Install Guide",
                       "settings": "Settings"}
        self.after(50, self._pump)
        self.show("games")
        self.bg(self.refresh_community)
        if cfg.get("app_repo"):
            self.bg(self.check_self_update, True)

    # ---- look & feel
    def _style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("Dark.Vertical.TScrollbar", background=BTN, troughcolor=BG, bordercolor=BG,
                    arrowcolor=BG, lightcolor=BTN, darkcolor=BTN, relief="flat", arrowsize=1)
        s.map("Dark.Vertical.TScrollbar", background=[("active", ACCENT)])
        s.configure("Gold.Horizontal.TProgressbar", troughcolor=INPUT, background=ACCENT,
                    bordercolor=SIDE, lightcolor=ACCENT, darkcolor=ACCENT)
        s.configure("Dark.TCombobox", fieldbackground=CARD, background=BTN, foreground=TXT,
                    arrowcolor=ACCENT, bordercolor=BORDER, lightcolor=CARD, darkcolor=CARD,
                    selectbackground=CARD, selectforeground=TXT, padding=6)
        s.map("Dark.TCombobox", fieldbackground=[("readonly", CARD)], foreground=[("readonly", TXT)])
        self.option_add("*TCombobox*Listbox.background", CARD)
        self.option_add("*TCombobox*Listbox.foreground", TXT)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.option_add("*TCombobox*Listbox.selectForeground", "#111")

    def _dark_titlebar(self):
        try:
            import ctypes
            self.update()
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(ctypes.c_int(
                1 if cfg["theme"] == "Dark" else 0)), 4)
        except Exception:
            pass

    def _banner(self):
        c = self.banner
        c.delete("all")
        w = max(c.winfo_width(), 2)
        start = mix(BG, ACCENT, 0.16)
        for x in range(0, w, 3):
            c.create_line(x, 0, x, 92, fill=mix(start, BG, min(1, x / (w * 0.85))), width=3)
        c.create_line(0, 91, w, 91, fill=mix(ACCENT, BG, 0.65))
        c.create_text(28, 36, anchor="w", text=self.titles[self.cur], fill=TXT, font=("Segoe UI", 22, "bold"))
        c.create_text(29, 66, anchor="w", text=self.subtitle(), fill=MUTED, font=F)
        if self.cur in ("loaders", "mods", "browse", "guide", "installed"):
            c.create_window(w - 24, 46, window=self.combo, anchor="e")
        else:
            self.combo.place_forget()

    def subtitle(self):
        return {"games": "Point CodMod at your game folders, then launch",
                "loaders": "Download the clients that enable mods",
                "mods": "Install mods from the catalog, a URL or a file",
                "browse": "Search public sources - no account or key needed",
                "downloads": "Pause, cancel or retry transfers",
                "installed": "Enable, disable, update, share and remove mods",
                "guide": "Step-by-step setup for the selected game",
                "settings": "Appearance, tokens, catalog and updates"}[self.cur]

    # ---- plumbing
    def ui(self, fn):
        self.q.put(fn)

    def _pump(self):
        try:
            while True:
                self.q.get_nowait()()
        except queue.Empty:
            pass
        self.after(50, self._pump)

    def ask_ui(self, fn):
        """Run fn on the UI thread from a worker thread and wait for its return value."""
        ev, box = threading.Event(), []

        def go():
            try:
                box.append(fn())
            finally:
                ev.set()
        self.ui(go)
        ev.wait()
        return box[0] if box else None

    def say(self, t):
        self.ui(lambda: self.status.configure(text=t))

    def prog(self, v):
        t = getattr(_tl, "task", None)
        if t:
            t.progress = v
        self.ui(lambda: self.bar.configure(value=v))

    def bg(self, fn, *a):
        def w():
            try:
                fn(*a)
            except Exception as ex:
                self.say(f"Error: {ex}")
        threading.Thread(target=w, daemon=True).start()

    def run(self, fn, *a, name=None):
        t = Task(name or fn.__name__, fn, a)
        self.tasks.append(t)

        def w():
            _tl.task = t
            t.status = "Running"
            try:
                fn(*a)
                t.status, t.progress = "Done", 1.0
            except Cancelled:
                t.status = "Cancelled"
            except Exception as ex:
                t.status, t.error = "Error", str(ex)
                self.say(f"Error: {ex}")
                self.ui(lambda ex=ex: messagebox.showerror(APP, str(ex)))
            finally:
                self.ui(lambda: self.bar.configure(value=0))
                self.ui(self._after_task)
        threading.Thread(target=w, daemon=True).start()
        return t

    def _after_task(self):
        if self.cur == "installed":
            self.show("installed")

    def game(self, gid):
        return next(x for x in CATALOG["games"] if x["id"] == gid)

    def g(self):
        return next(x for x in CATALOG["games"] if x["name"] == self.gvar.get())

    def gdir(self, gid):
        d = cfg["dirs"].get(gid)
        if not d or not os.path.isdir(d):
            self.ui(lambda: messagebox.showwarning(APP, f"Set the folder for {self.game(gid)['name']} on the Games tab first."))
            return None
        return d

    def show(self, key):
        self.cur = key
        for k, n in self.nav.items():
            n.set_active(k == key)
        for w in self.content.winfo_children():
            w.destroy()
        ScrollFrame.current = None
        self.dl_rows = {}
        if hasattr(self, "_dl_empty"):
            del self._dl_empty
        self._banner()
        getattr(self, "page_" + key)()

    def scroll(self, parent=None):
        s = ScrollFrame(parent or self.content)
        s.pack(fill="both", expand=True, padx=16, pady=(10, 10))
        return s.inner

    def empty(self, parent, text):
        tk.Label(parent, text=text, bg=BG, fg=MUTED, font=F, justify="center").pack(pady=40)

    def entry(self, parent, **kw):
        return tk.Entry(parent, bg=INPUT, fg=TXT, insertbackground=TXT, relief="flat", font=("Segoe UI", 11),
                        highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT, **kw)

    def bar_frame(self, pad=(12, 0)):
        f = tk.Frame(self.content, bg=BG)
        f.pack(fill="x", padx=26, pady=pad)
        return f

    # ================================================================ pages
    def page_games(self):
        s = self.scroll()
        for g in CATALOG["games"]:
            d = cfg["dirs"].get(g["id"])
            ok = bool(d and os.path.isdir(d))
            sub = (g.get("note", "") + "\n" if g.get("note") else "") + (d if ok else "No folder set")
            Card(s, g["name"], sub,
                 [("Launch", lambda g=g: self.launch(g["id"]), True, 80),
                  ("Browse", lambda g=g: self.browse(g), False, 80),
                  ("Detect", lambda g=g: self.detect(g), False, 76),
                  ("Exe…", lambda g=g: self.set_launcher(g["id"]), False, 62)],
                 badge=("● READY", GREEN) if ok else ("○ NOT SET", MUTED))

    def browse(self, g):
        d = filedialog.askdirectory(title=f"Select {g['name']} folder")
        if d:
            cfg["dirs"][g["id"]] = d
            save_cfg()
            self.show("games")

    def detect(self, g):
        libs = []
        for pf in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
            if pf:
                libs.append(os.path.join(pf, "Steam", "steamapps"))
        libs += [f"{d}:\\SteamLibrary\\steamapps" for d in "CDEFG"]
        for lib in list(libs):  # extra Steam libraries listed by Steam itself
            try:
                with open(os.path.join(lib, "libraryfolders.vdf"), encoding="utf-8", errors="ignore") as fh:
                    libs += [os.path.join(p.replace("\\\\", "\\"), "steamapps")
                             for p in re.findall(r'"path"\s+"([^"]+)"', fh.read())]
            except OSError:
                pass
        for lib in libs:
            cands = []
            if g.get("steam"):
                try:
                    with open(os.path.join(lib, f"appmanifest_{g['steam']}.acf"), encoding="utf-8", errors="ignore") as fh:
                        m = re.search(r'"installdir"\s+"([^"]+)"', fh.read())
                    if m:
                        cands.append(os.path.join(lib, "common", m.group(1)))
                except OSError:
                    pass
            cands += [os.path.join(lib, "common", f) for f in g.get("folders", [])]
            for p in cands:
                if os.path.isdir(p):
                    cfg["dirs"][g["id"]] = p
                    save_cfg()
                    self.show("games")
                    return
        messagebox.showinfo(APP, "Couldn't find it automatically. Use Browse to pick the folder.")

    def set_launcher(self, gid):
        p = filedialog.askopenfilename(title="Choose the program that launches this game / its loader",
                                       filetypes=[("Programs", "*.exe *.bat *.lnk"), ("All files", "*.*")])
        if p:
            cfg["launch"][gid] = p
            save_cfg()
            self.say(f"Launcher set: {os.path.basename(p)}")

    def launch(self, gid):
        g, d = self.game(gid), cfg["dirs"].get(gid)
        cands = [cfg["launch"].get(gid)]
        if d and os.path.isdir(d):
            cands += glob.glob(os.path.join(d, "alterware-launcher*.exe"))
        if any("lutonium" in l["name"] for l in g["loaders"]):
            cands.append(os.path.join(DATA, "Plutonium", "plutonium.exe"))
        for c in cands:
            if c and os.path.isfile(c):
                try:
                    if os.name == "nt":
                        os.startfile(c)
                    else:
                        subprocess.Popen([c], cwd=os.path.dirname(c))
                    self.say(f"Launched {os.path.basename(c)}")
                except OSError as ex:
                    messagebox.showerror(APP, str(ex))
                return
        if g.get("steam"):
            webbrowser.open(f"steam://rungameid/{g['steam']}")
            self.say("Launching through Steam...")
        else:
            messagebox.showinfo(APP, "No launcher found. Use 'Exe…' to choose the program to start.")

    def page_loaders(self):
        s = self.scroll()
        g = self.g()
        gid = g["id"]
        if not g["loaders"] and gid != "bo3":
            self.empty(s, "This game doesn't need a separate loader.\nSee the Guide tab.")
        for l in g["loaders"]:
            btns = [("Website", lambda l=l: webbrowser.open(l["url"]), False)]
            if l.get("direct"):
                btns.insert(0, ("Download", lambda l=l: self.run(self.dl_direct, l, name=f"Download {l['name']}"), True))
            elif l.get("github"):
                btns.insert(0, ("Download", lambda l=l: self.run(self.dl_github, gid, l["github"], l["name"],
                                                                 l.get("asset", ""), True, False,
                                                                 name=f"Download {l['name']}"), True))
            Card(s, l["name"], l["desc"], btns)
        if gid == "bo3":
            d = cfg["dirs"].get(gid)
            wdir = os.path.join(os.path.dirname(os.path.dirname(d)), "workshop", "content", "311210") if d else ""
            items = [x for x in os.listdir(wdir)] if wdir and os.path.isdir(wdir) else []
            sub = (f"{len(items)} subscribed Workshop item(s) downloaded by Steam." if items else
                   "Subscribe on the Workshop, then enable mods from the in-game Mods menu."
                   if d else "Set your BO3 folder on the Games tab to see subscribed items.")
            btns = [("Open Workshop", lambda: webbrowser.open("https://steamcommunity.com/app/311210/workshop/"), True, 130)]
            if items and os.name == "nt":
                btns.append(("Folder", lambda: os.startfile(wdir), False, 80))
            Card(s, "Steam Workshop (official)", sub, btns)

    def page_mods(self):
        g = self.g()
        gid = g["id"]
        top = self.bar_frame()
        RBtn(top, "+ From URL", self.custom_url, True, 120, bg=BG).pack(side="left", padx=(0, 8))
        RBtn(top, "+ From file", self.custom_file, False, 120, bg=BG).pack(side="left")
        s = self.scroll()
        mods = [m for m in CATALOG["mods"] + COMMUNITY_MODS if m.get("game") == gid]
        for m in mods:
            Card(s, m["name"], m.get("desc", ""),
                 [("Install", lambda m=m: self.run(self.dl_mod, m, name=f"Install {m['name']}"), True)])
        if not mods:
            self.empty(s, "No catalog entries for this game yet.\nTry Browse Online, install from a URL,\nor add a catalog URL in Settings.")

    def page_browse(self):
        g = self.g()
        bar = self.bar_frame()
        self.qv = tk.StringVar(value=g.get("search", g["name"]))
        e = self.entry(bar, textvariable=self.qv)
        e.pack(side="left", fill="x", expand=True, ipady=7)
        e.bind("<Return>", lambda ev: self.search())
        RBtn(bar, "GitHub", self.search, True, 84, bg=BG).pack(side="left", padx=8)
        RBtn(bar, "GameBanana", self.search_gb, True, 108, bg=BG).pack(side="left")
        RBtn(bar, "ModDB ↗", lambda: webbrowser.open("https://www.moddb.com/search?q=" + urllib.parse.quote(self.qv.get())),
             False, 84, bg=BG).pack(side="left", padx=(8, 0))
        self.sortv = tk.StringVar(value="Default")
        sc = ttk.Combobox(bar, textvariable=self.sortv, state="readonly", width=9, style="Dark.TCombobox",
                          values=["Default", "A–Z", "Newest", "Popular"])
        sc.pack(side="left", padx=(8, 0))
        sc.bind("<<ComboboxSelected>>", lambda ev: self.render_gb(self.results, self.gb_rows) if self.gb_rows else None)
        self.results = self.scroll()
        self.gb_rows = []
        self.search()

    def search(self):
        q = self.qv.get().strip()
        if not q:
            return
        self.gb_rows = []
        for w in self.results.winfo_children():
            w.destroy()
        self.empty(self.results, "Searching...")
        parent, gid = self.results, self.g()["id"]

        def work():
            try:
                url = "https://api.github.com/search/repositories?sort=stars&per_page=25&q=" + urllib.parse.quote(q)
                items = json.loads(http_get(url))["items"]
                self.ui(lambda: self.render_results(parent, items, gid=gid))
            except Exception as ex:
                self.ui(lambda ex=ex: self.render_results(parent, None, str(ex)))
        threading.Thread(target=work, daemon=True).start()

    def render_results(self, parent, items, err=None, gid=None):
        if not parent.winfo_exists():
            return
        for w in parent.winfo_children():
            w.destroy()
        if err:
            return self.empty(parent, f"Search failed: {err}\n(GitHub allows 60 searches/hour without a token - add one in Settings for more.)")
        banned = ("cheat", "hack", "aimbot", "wallhack", "injector", "spoofer", "crack", "esp ", "unlocker",
                  "keygen", "bypass", "exploit", "ddos", "loader for", "trainer")
        items = [i for i in (items or []) if not any(b in (i["full_name"] + " " + (i.get("description") or "")
                 + " " + " ".join(i.get("topics") or [])).lower() for b in banned)]
        if not items:
            return self.empty(parent, "No results. Try different words.")
        for it in items:
            desc = (it.get("description") or "No description")
            r = it["full_name"]
            Card(parent, r, f"★ {it['stargazers_count']}   {desc[:160]}",
                 [("Install", lambda r=r: self.run(self.dl_github, gid, r, r, "", False, True, name=f"Install {r}"), True),
                  ("Page", lambda u=it["html_url"]: webbrowser.open(u), False)])

    # ---- GameBanana
    def search_gb(self):
        g = self.g()
        for w in self.results.winfo_children():
            w.destroy()
        gid = g.get("gb")
        if not gid:
            return self.empty(self.results, "No GameBanana section is set for this game.\nTry GitHub or ModDB, or add 'gb' to the game in catalog.json.")
        self.empty(self.results, "Loading GameBanana...")
        parent, game_id = self.results, g["id"]
        q = self.qv.get().strip().lower()
        words = [] if (not q or q == g.get("search", "").lower()) else q.split()

        def work():
            try:
                u = f"https://gamebanana.com/apiv11/Game/{gid}/Subfeed?_nPage=1&_nPerpage=50&_sSort=default"
                recs = json.loads(http_get(u)).get("_aRecords", [])
                rows = [{"name": r.get("_sName", "?"), "id": r.get("_idRow"), "page": r.get("_sProfileUrl"),
                         "added": r.get("_tsDateAdded", 0), "pop": r.get("_nLikeCount", 0) or r.get("_nViewCount", 0),
                         "game": game_id}
                        for r in recs if r.get("_sModelName") == "Mod"
                        and all(w in r.get("_sName", "").lower() for w in words)]
                self.gb_rows = rows
                self.ui(lambda: self.render_gb(parent, rows))
            except Exception as ex:
                self.ui(lambda ex=ex: self.render_results(parent, None, f"GameBanana: {ex}"))
        threading.Thread(target=work, daemon=True).start()

    def render_gb(self, parent, rows):
        if not parent.winfo_exists():
            return
        for w in parent.winfo_children():
            w.destroy()
        mode = self.sortv.get() if hasattr(self, "sortv") else "Default"
        if mode == "A–Z":
            rows = sorted(rows, key=lambda r: str(r["name"]).lower())
        elif mode == "Newest":
            rows = sorted(rows, key=lambda r: r["added"] or 0, reverse=True)
        elif mode == "Popular":
            rows = sorted(rows, key=lambda r: r["pop"] or 0, reverse=True)
        if not rows:
            return self.empty(parent, "No GameBanana mods matched.")
        for r in rows:
            btns = [("Install", lambda r=r: self.run(self.dl_gb, r["game"], r["id"], str(r["name"]), True,
                                                     name=f"Install {r['name']}"), True),
                    ("Details", lambda r=r: self.bg(self.gb_details, r["id"]), False, 80)]
            if r.get("page"):
                btns.append(("Page", lambda u=r["page"]: webbrowser.open(u), False, 70))
            Card(parent, str(r["name"]), "GameBanana", btns)

    def gb_details(self, mid):
        info = json.loads(http_get(f"https://gamebanana.com/apiv11/Mod/{mid}/ProfilePage"))
        files = info.get("_aFiles", [])
        txt = re.sub(r"<br\s*/?>|</p>", "\n", str(info.get("_sText") or info.get("_sDescription") or ""))
        txt = html.unescape(re.sub(r"<[^>]+>", " ", txt))
        txt = re.sub(r"[ \t]+", " ", txt).strip()
        lines = [info.get("_sName", "Mod"), "",
                 f"Author: {(info.get('_aSubmitter') or {}).get('_sName', '?')}",
                 f"Views: {info.get('_nViewCount', '?')}   Likes: {info.get('_nLikeCount', '?')}   "
                 f"Downloads: {sum(f.get('_nDownloadCount', 0) for f in files)}", "", "Files:"]
        lines += [f"  {f.get('_sFile', '?')}  ({f.get('_nFilesize', 0) / 1048576:.1f} MB)" for f in files] or ["  (none)"]
        lines += ["", txt[:3000]]
        self.ui(lambda: self._details_window(info.get("_sName", "Mod"), "\n".join(lines)))

    def _details_window(self, title, text):
        w = tk.Toplevel(self)
        w.title(title)
        w.configure(bg=BG)
        w.geometry("620x520")
        t = tk.Text(w, bg=CARD, fg=TXT, font=("Segoe UI", 11), wrap="word", relief="flat", padx=18, pady=16)
        t.pack(fill="both", expand=True, padx=12, pady=12)
        t.insert("1.0", text)
        t.configure(state="disabled")

    # ---- downloads tab
    def page_downloads(self):
        self.dl_parent = self.scroll()
        self._tick_downloads()

    def _tick_downloads(self):
        if self.cur != "downloads" or not self.dl_parent.winfo_exists():
            return
        if not self.tasks and not hasattr(self, "_dl_empty"):
            self._dl_empty = tk.Label(self.dl_parent, text="No downloads yet.", bg=BG, fg=MUTED, font=F)
            self._dl_empty.pack(pady=40)
        for t in self.tasks:
            row = self.dl_rows.get(t.id)
            if row is None:
                if hasattr(self, "_dl_empty"):
                    self._dl_empty.destroy()
                    del self._dl_empty
                row = self._make_dl_row(t)
                self.dl_rows[t.id] = row
            row["status"].configure(text=t.status + (f" - {t.error[:70]}" if t.error else ""))
            row["bar"].configure(value=t.progress)
            row["pause"].set_text("Resume" if t.paused else "Pause")
            active, finished = t.status in ("Running", "Queued"), t.status in ("Error", "Cancelled")
            for key, show in (("pause", active), ("cancel", active), ("retry", finished)):
                if show and not row[key].winfo_ismapped():
                    row[key].pack(side="right", padx=(0, 10), pady=12)
                elif not show and row[key].winfo_ismapped():
                    row[key].pack_forget()
        self.after(400, self._tick_downloads)

    def _make_dl_row(self, t):
        f = tk.Frame(self.dl_parent, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        f.pack(fill="x", padx=10, pady=5)
        tk.Frame(f, bg=ACCENT, width=3).pack(side="left", fill="y")
        retry = RBtn(f, "Retry", lambda: self.retry(t), True, 80)
        cancel = RBtn(f, "Cancel", lambda: setattr(t, "cancelled", True), False, 80)
        pause = RBtn(f, "Pause", lambda: setattr(t, "paused", not t.paused), False, 80)
        body = tk.Frame(f, bg=CARD)
        body.pack(side="left", fill="x", expand=True, padx=16, pady=10)
        tk.Label(body, text=t.name, bg=CARD, fg=TXT, font=("Segoe UI", 11, "bold"), anchor="w").pack(fill="x")
        st = tk.Label(body, text=t.status, bg=CARD, fg=MUTED, font=F, anchor="w")
        st.pack(fill="x")
        pb = ttk.Progressbar(body, style="Gold.Horizontal.TProgressbar", maximum=1.0)
        pb.pack(fill="x", pady=(4, 0))
        return {"status": st, "bar": pb, "pause": pause, "cancel": cancel, "retry": retry}

    def retry(self, t):
        t.status = "Retried"
        self.run(t.fn, *t.args, name=t.name)

    # ---- installed tab
    def page_installed(self):
        g = self.g()
        gid = g["id"]
        bar = self.bar_frame()
        pn = ttk.Combobox(bar, state="readonly", width=18, style="Dark.TCombobox", values=self.profile_names(gid))
        pn.pack(side="left")
        if pn["values"]:
            pn.current(0)
        RBtn(bar, "Apply profile", lambda: pn.get() and self.run(self.apply_profile, gid, pn.get(), name=f"Profile {pn.get()}"),
             True, 112, bg=BG).pack(side="left", padx=6)
        RBtn(bar, "Save current", lambda: self.save_profile(gid), False, 108, bg=BG).pack(side="left")
        RBtn(bar, "Delete", lambda: self.del_profile(gid, pn.get()), False, 70, bg=BG).pack(side="left", padx=6)
        bar2 = self.bar_frame((6, 0))
        RBtn(bar2, "Check updates", lambda: self.run(self.check_updates, name="Check for updates"), False, 124, bg=BG).pack(side="left")
        RBtn(bar2, "Export pack", lambda: self.export_pack(gid), False, 112, bg=BG).pack(side="left", padx=6)
        RBtn(bar2, "Import pack", self.import_pack, False, 112, bg=BG).pack(side="left")
        s = self.scroll()
        ents = [e for e in cfg["installed"] if e["game"] == gid]
        if not ents:
            self.empty(s, f"Nothing installed for {g['name']} yet.")
        for e in ents:
            on = e.get("enabled", True)
            badge = ("▲ UPDATE", ACCENT) if e.get("update") else (("● ENABLED", GREEN) if on else ("○ DISABLED", MUTED))
            btns = [("Disable" if on else "Enable", lambda e=e: self.toggle(e), False, 90),
                    ("Uninstall", lambda e=e: self.uninstall(e), False, 96)]
            if e.get("update"):
                btns.insert(0, ("Update", lambda e=e: self.update_entry(e), True, 84))
            kind = "loader" if e.get("loader") else "mod"
            Card(s, e["name"], f"{kind}  -  {len(e['files'])} files" + (f"  -  version {e['version']}" if e.get("version") else ""),
                 btns, badge=badge)

    def page_guide(self):
        box = tk.Text(self.content, bg=CARD, fg=TXT, font=("Segoe UI", 12), wrap="word", relief="flat",
                      padx=24, pady=20, spacing2=4, highlightthickness=1, highlightbackground=BORDER)
        box.pack(fill="both", expand=True, padx=26, pady=16)
        box.insert("1.0", self.g()["guide"])
        box.configure(state="disabled")

    def page_settings(self):
        s = self.scroll()
        f = tk.Frame(s, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        f.pack(fill="x", padx=10, pady=8)
        pad = dict(anchor="w", padx=20)

        def field(label, hint, key, secret=False):
            tk.Label(f, text=label, bg=CARD, fg=TXT, font=FB).pack(pady=(14, 0), **pad)
            tk.Label(f, text=hint, bg=CARD, fg=MUTED, font=F).pack(**pad)
            e = self.entry(f, show="•" if secret else "")
            e.insert(0, cfg[key])
            e.pack(fill="x", padx=20, pady=(6, 0), ipady=6)
            return e
        tok = field("GitHub token (optional)", "Raises GitHub's rate limit from 60 to 5,000 requests/hour.", "github_token", True)
        cat = field("Community catalog URL", "JSON with 'games' and/or 'mods'. Only use catalogs you trust.", "catalog_url")
        rep = field("CodMod repo for self-update", "owner/name on GitHub, e.g. yourname/CodMod", "app_repo")
        tk.Label(f, text="Appearance", bg=CARD, fg=TXT, font=FB).pack(pady=(16, 4), **pad)
        row = tk.Frame(f, bg=CARD)
        row.pack(fill="x", padx=20)
        th = ttk.Combobox(row, state="readonly", width=10, values=list(THEMES), style="Dark.TCombobox")
        th.set(cfg["theme"])
        th.pack(side="left")
        ac = ttk.Combobox(row, state="readonly", width=10, values=list(ACCENTS), style="Dark.TCombobox")
        ac.set(cfg["accent"])
        ac.pack(side="left", padx=8)

        def sv():
            changed = (th.get(), ac.get()) != (cfg["theme"], cfg["accent"])
            cfg.update(github_token=tok.get().strip(), catalog_url=cat.get().strip(), app_repo=rep.get().strip(),
                       theme=th.get(), accent=ac.get())
            save_cfg()
            self.say("Settings saved")
            self.bg(self.refresh_community)
            if changed and messagebox.askyesno(APP, "Restart CodMod to apply the new look?"):
                self.restart()
        b = tk.Frame(f, bg=CARD)
        b.pack(fill="x", padx=20, pady=16)
        RBtn(b, "Save", sv, True, 80).pack(side="left")
        RBtn(b, "Check CodMod update", lambda: self.bg(self.check_self_update, False), False, 164).pack(side="left", padx=8)
        RBtn(b, "Refresh catalog", lambda: self.bg(self.refresh_community), False, 124).pack(side="left")
        b2 = tk.Frame(f, bg=CARD)
        b2.pack(fill="x", padx=20, pady=(0, 16))
        RBtn(b2, "Open data folder", lambda: os.startfile(DATA) if os.name == "nt" else None, False, 140).pack(side="left")
        RBtn(b2, "Clear downloads", self.clear_cache, False, 130).pack(side="left", padx=8)
        tk.Label(s, bg=BG, fg=MUTED, font=F, justify="left", wraplength=700,
                 text="CodMod only installs mods and loaders. You must own the game. "
                      "Don't use mods on official matchmaking servers.").pack(anchor="w", padx=14, pady=8)

    # ================================================================ actions
    def restart(self):
        args = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, os.path.abspath(__file__)]
        subprocess.Popen(args)
        self.destroy()

    def clear_cache(self):
        shutil.rmtree(CACHE, ignore_errors=True)
        os.makedirs(CACHE, exist_ok=True)
        self.say("Download cache cleared")

    def refresh_community(self):
        u = cfg.get("catalog_url")
        if not u:
            return
        data = json.loads(http_get(u).decode("utf-8"))
        with open(COMMUNITY, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        merge_catalog(data)
        self.say("Community catalog updated (new games appear after restart)")

    def check_self_update(self, silent):
        repo = cfg.get("app_repo", "")
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
            if not silent:
                self.ui(lambda: messagebox.showinfo(APP, "Set 'CodMod repo' (owner/name) in Settings first."))
            return
        rel = json.loads(http_get(f"https://api.github.com/repos/{repo}/releases/latest"))
        tag = rel.get("tag_name", "")
        if vtuple(tag) > vtuple(APP_VERSION):
            self.say(f"CodMod {tag} is available")
            self.ui(lambda: messagebox.askyesno(APP, f"CodMod {tag} is available. Open the download page?")
                    and webbrowser.open(rel.get("html_url", f"https://github.com/{repo}/releases")))
        elif not silent:
            self.say("CodMod is up to date")

    # ---- downloading / installing
    def dl_direct(self, l):
        """Standalone launcher/updater (e.g. Plutonium) saved in CodMod's own folder, not the game folder."""
        folder = os.path.join(DATA, "Plutonium" if "lutonium" in l["name"] else safe(l["name"].split()[0]))
        os.makedirs(folder, exist_ok=True)
        dest = os.path.join(folder, os.path.basename(l["direct"]))
        self.say(f"Downloading {os.path.basename(dest)}...")
        http_get(l["direct"], dest, self.prog)
        self.say(f"{l['name']} saved to {folder}")

        def ask():
            if messagebox.askyesno(APP, f"{l['name']} downloaded to:\n{folder}\n\nRun it now? "
                                        "(On first run, point it at your game folder.)") and os.name == "nt":
                os.startfile(dest)
        self.ui(ask)

    def dl_github(self, gid, repo, name, pattern, loader=False, confirm=True):
        d = self.gdir(gid)
        if not d:
            raise Cancelled()
        self.say(f"Looking up latest release of {repo}...")
        rel = json.loads(http_get(f"https://api.github.com/repos/{repo}/releases/latest"))
        pat = pattern.lower()
        exts = (".zip", ".exe", ".7z", ".rar")
        assets = [a for a in rel.get("assets", []) if a["name"].lower().endswith(exts)]
        asset = next((a for a in assets if pat in a["name"].lower()), None) if pat else (assets[0] if assets else None)
        if not asset:
            raise RuntimeError("This repo has no downloadable release file. Use the Page button instead.")
        dest = os.path.join(CACHE, asset["name"])
        self.say(f"Downloading {asset['name']}...")
        http_get(asset["browser_download_url"], dest, self.prog)
        self.install(dest, gid, name, self.game(gid).get("mods_dir", ""),
                     {"src": {"type": "github", "repo": repo, "asset": pattern}, "version": rel.get("tag_name")},
                     confirm=confirm and not loader, loader=loader)
        self.say(f"{name} installed")

    def dl_mod(self, m, confirm=True):
        gid = m["game"]
        if not self.gdir(gid):
            raise Cancelled()
        if not str(m["url"]).startswith(("http://", "https://")):
            raise RuntimeError("Only http(s) links are supported.")
        dest = os.path.join(CACHE, safe(os.path.basename(m["url"].split("?")[0]) or "mod.zip"))
        self.say(f"Downloading {m['name']}...")
        http_get(m["url"], dest, self.prog)
        self.install(dest, gid, m["name"], m.get("subdir", self.game(gid).get("mods_dir", "")),
                     {"src": {"type": "url", "url": m["url"]}}, confirm=confirm)
        self.say(f"{m['name']} installed")

    def dl_gb(self, gid, mid, name, confirm=True):
        if not self.gdir(gid):
            raise Cancelled()
        self.say(f"Fetching {name}...")
        info = json.loads(http_get(f"https://gamebanana.com/apiv11/Mod/{mid}/ProfilePage"))
        files = [f for f in info.get("_aFiles", []) if str(f.get("_sFile", "")).lower().endswith((".zip", ".7z", ".rar"))]
        files.sort(key=lambda f: not str(f.get("_sFile", "")).lower().endswith(".zip"))
        if not files:
            raise RuntimeError("This mod has no .zip/.7z/.rar file. Use the Page button to download it manually.")
        fl = files[0]
        dest = os.path.join(CACHE, safe(fl["_sFile"]))
        self.say(f"Downloading {fl['_sFile']}...")
        http_get(fl["_sDownloadUrl"], dest, self.prog)
        ver = info.get("_tsDateUpdated") or info.get("_tsDateModified") or info.get("_tsDateAdded")
        self.install(dest, gid, name, self.game(gid).get("mods_dir", ""),
                     {"src": {"type": "gb", "id": mid}, "version": ver}, confirm=confirm)
        self.say(f"{name} installed")

    def custom_url(self):
        u = simpledialog.askstring(APP, "Direct download URL (.zip/.7z/.rar or file):", parent=self)
        if u and u.startswith(("http://", "https://")):
            gid = self.g()["id"]
            self.run(self.dl_mod, {"name": os.path.basename(u.split("?")[0]) or "Custom mod", "url": u, "game": gid},
                     name=f"Install {os.path.basename(u.split('?')[0])}")

    def custom_file(self):
        p = filedialog.askopenfilename(title="Select mod (.zip, .7z, .rar or file)")
        if p:
            self.run(self.install_local, p, self.g()["id"], name=f"Install {os.path.basename(p)}")

    def install_local(self, path, gid):
        if not self.gdir(gid):
            raise Cancelled()
        self.install(path, gid, os.path.basename(path), self.game(gid).get("mods_dir", ""), {}, confirm=True)

    def install(self, path, gid, name, default_sub="", meta=None, confirm=True, loader=False):
        gdir = cfg["dirs"][gid]
        names, kind = archive_members(path)
        tops = {n.split("/")[0].lower() for n in names}
        sub = "" if (loader or kind == "file" or tops & ROOT_DIRS) else default_sub
        target = os.path.join(gdir, sub) if sub else gdir
        old = next((e for e in cfg["installed"] if e["game"] == gid and e["name"] == name), None)
        if confirm:
            choice = self.ask_ui(lambda: self.preview(name, names, target, gdir, gid, sub, old))
            if choice is None:
                raise Cancelled()
            if choice == "root":
                target = gdir
        if old:
            self.remove_entry(old)  # restores originals before reinstalling
            cfg["installed"].remove(old)
        files, backups = [], []
        bdir = os.path.join(BACKUPS, gid, safe(name))
        for n in names:
            dest = os.path.join(target, n)
            files.append(dest)
            if os.path.isfile(dest):
                bak = os.path.join(bdir, os.path.relpath(dest, gdir))
                os.makedirs(os.path.dirname(bak), exist_ok=True)
                shutil.copy2(dest, bak)
                backups.append({"orig": dest, "bak": bak})
        extract(path, kind, target)
        entry = {"game": gid, "name": name, "files": files, "backups": backups, "loader": loader, "enabled": True}
        entry.update(meta or {})
        cfg["installed"].append(entry)
        save_cfg()

    def preview(self, name, names, target, gdir, gid, sub, old):
        newset = {norm(os.path.join(target, n)) for n in names}
        over = sum(1 for n in names if os.path.isfile(os.path.join(target, n)))
        conflicts = sorted({e["name"] for e in cfg["installed"] if e is not old and e["game"] == gid
                            and any(norm(f) in newset for f in e["files"])})
        dest = os.path.relpath(target, gdir) if norm(target) != norm(gdir) else "(game folder)"
        msg = f'Install "{name}"?\n\n{len(names)} files -> {dest}\n'
        if over:
            msg += f"{over} existing files will be replaced (originals are backed up).\n"
        if conflicts:
            msg += "Overlaps files from: " + ", ".join(conflicts) + "\n"
        msg += "\n" + "\n".join(names[:8]) + ("\n..." if len(names) > 8 else "")
        if sub:
            r = messagebox.askyesnocancel(APP, msg + "\n\nYes = install here\nNo = install in the game folder instead\nCancel = abort")
            return "here" if r else ("root" if r is False else None)
        return "here" if messagebox.askyesno(APP, msg + "\n\nProceed?") else None

    # ---- enable / disable / uninstall
    def _rel(self, e, f):
        return os.path.relpath(f, cfg["dirs"][e["game"]])

    def disable(self, e):
        ddir = os.path.join(DISABLED, e["game"], safe(e["name"]))
        for f in e["files"]:
            if os.path.isfile(f):
                dst = os.path.join(ddir, self._rel(e, f))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.move(f, dst)
        for b in e.get("backups", []):
            if os.path.isfile(b["bak"]):
                shutil.copy2(b["bak"], b["orig"])
        e["enabled"] = False
        save_cfg()

    def enable(self, e):
        ddir = os.path.join(DISABLED, e["game"], safe(e["name"]))
        for f in e["files"]:
            src = os.path.join(ddir, self._rel(e, f))
            if os.path.isfile(src):
                os.makedirs(os.path.dirname(f), exist_ok=True)
                shutil.move(src, f)
        e["enabled"] = True
        save_cfg()

    def remove_entry(self, e):
        gdir = cfg["dirs"].get(e["game"], "")
        ddir = os.path.join(DISABLED, e["game"], safe(e["name"]))
        for f in e["files"]:
            for p in (f, os.path.join(ddir, self._rel(e, f)) if gdir else None):
                if p and os.path.isfile(p):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
            d = os.path.dirname(f)  # tidy empty folders inside the game dir
            while gdir and norm(d).startswith(norm(gdir)) and norm(d) != norm(gdir):
                try:
                    os.rmdir(d)
                except OSError:
                    break
                d = os.path.dirname(d)
        for b in e.get("backups", []):
            if os.path.isfile(b["bak"]):
                os.makedirs(os.path.dirname(b["orig"]), exist_ok=True)
                shutil.copy2(b["bak"], b["orig"])
                os.remove(b["bak"])
        shutil.rmtree(ddir, ignore_errors=True)

    def toggle(self, e):
        (self.disable if e.get("enabled", True) else self.enable)(e)
        self.show("installed")

    def uninstall(self, e):
        if messagebox.askyesno(APP, f"Remove {e['name']}? Overwritten original files will be restored."):
            self.remove_entry(e)
            cfg["installed"].remove(e)
            save_cfg()
            self.show("installed")

    # ---- updates
    def check_updates(self):
        for e in cfg["installed"]:
            s = e.get("src") or {}
            try:
                if s.get("type") == "github":
                    latest = json.loads(http_get(f"https://api.github.com/repos/{s['repo']}/releases/latest")).get("tag_name")
                elif s.get("type") == "gb":
                    i = json.loads(http_get(f"https://gamebanana.com/apiv11/Mod/{s['id']}/ProfilePage"))
                    latest = i.get("_tsDateUpdated") or i.get("_tsDateModified") or i.get("_tsDateAdded")
                else:
                    continue
                e["update"] = bool(e.get("version")) and str(latest) != str(e["version"])
            except Exception:
                pass
        save_cfg()
        self.say("Update check finished")

    def update_entry(self, e):
        s = e["src"]
        if s["type"] == "github":
            self.run(self.dl_github, e["game"], s["repo"], e["name"], s.get("asset", ""), e.get("loader", False), False,
                     name=f"Update {e['name']}")
        else:
            self.run(self.dl_gb, e["game"], s["id"], e["name"], False, name=f"Update {e['name']}")

    # ---- profiles
    def profile_names(self, gid):
        return sorted(cfg["profiles"].get(gid, {}))

    def save_profile(self, gid):
        n = simpledialog.askstring(APP, "Profile name:", parent=self)
        if n:
            cfg["profiles"].setdefault(gid, {})[n.strip()] = [
                e["name"] for e in cfg["installed"] if e["game"] == gid and not e.get("loader") and e.get("enabled", True)]
            save_cfg()
            self.show("installed")

    def del_profile(self, gid, n):
        if n and cfg["profiles"].get(gid, {}).pop(n, None) is not None:
            save_cfg()
            self.show("installed")

    def apply_profile(self, gid, pname):
        want = set(cfg["profiles"].get(gid, {}).get(pname, []))
        for e in cfg["installed"]:
            if e["game"] != gid or e.get("loader"):
                continue
            on = e.get("enabled", True)
            if e["name"] in want and not on:
                self.enable(e)
            elif e["name"] not in want and on:
                self.disable(e)
        self.say(f"Profile '{pname}' applied")

    # ---- mod packs
    def export_pack(self, gid):
        mods = [{"name": e["name"], "src": e["src"], "loader": e.get("loader", False)}
                for e in cfg["installed"] if e["game"] == gid and e.get("src")]
        if not mods:
            return messagebox.showinfo(APP, "Nothing shareable here (mods installed from local files can't be exported).")
        p = filedialog.asksaveasfilename(defaultextension=".codmodpack", filetypes=[("CodMod pack", "*.codmodpack")])
        if p:
            with open(p, "w", encoding="utf-8") as fh:
                json.dump({"codmod_pack": 1, "game": gid, "mods": mods}, fh, indent=1)
            self.say(f"Pack saved ({len(mods)} items)")

    def import_pack(self):
        p = filedialog.askopenfilename(filetypes=[("CodMod pack", "*.codmodpack"), ("All files", "*.*")])
        if not p:
            return
        try:
            with open(p, encoding="utf-8") as fh:
                pack = json.load(fh)
            gid = pack["game"]
            self.game(gid)
            items = []
            for m in pack["mods"]:
                s, n = m["src"], str(m["name"])[:100]
                if s["type"] == "github" and re.fullmatch(r"[\w.-]+/[\w.-]+", str(s["repo"])):
                    items.append((n, s, bool(m.get("loader"))))
                elif s["type"] == "gb" and str(s["id"]).isdigit():
                    items.append((n, s, False))
                elif s["type"] == "url" and str(s["url"]).startswith("https://"):
                    items.append((n, s, False))
        except Exception as ex:
            return messagebox.showerror(APP, f"Not a valid pack: {ex}")
        if not items:
            return messagebox.showinfo(APP, "The pack has nothing installable.")
        listing = "\n".join(f"- {n}  ({s['type']}: {s.get('repo') or s.get('id') or s.get('url')})" for n, s, _ in items[:15])
        if not messagebox.askyesno(APP, f"This pack will download and install {len(items)} item(s) for "
                                        f"{self.game(gid)['name']}:\n\n{listing}\n\nOnly continue if you trust its author."):
            return
        for n, s, loader in items:
            if s["type"] == "github":
                self.run(self.dl_github, gid, s["repo"], n, s.get("asset", ""), loader, True, name=f"Install {n}")
            elif s["type"] == "gb":
                self.run(self.dl_gb, gid, int(s["id"]), n, True, name=f"Install {n}")
            else:
                self.run(self.dl_mod, {"name": n, "url": s["url"], "game": gid}, name=f"Install {n}")
        self.show("downloads")


if __name__ == "__main__":
    App().mainloop()
