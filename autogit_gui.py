"""
Obsidian AutoGit GUI
"""

import os, sys, time, queue, threading, subprocess, json, ctypes, math
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime
from typing import List, Optional

# Taskbar process name (Windows)
APP_NAME = "Obsidian AutoGit"
if sys.platform == "win32":
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_NAME)
    try:
        ctypes.windll.kernel32.SetConsoleTitleW(APP_NAME)
    except Exception:
        pass

def _runtime_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))

def _resource_dir() -> str:
    if hasattr(sys, "_MEIPASS"):
        return str(sys._MEIPASS)
    return _runtime_dir()

_HERE = _runtime_dir()
_RES = _resource_dir()
sys.path.insert(0, _HERE)
from auto_commit_cli import (
    normalize_repo_path, validate_repo,
    load_extra_repos, add_extra_repo, remove_extra_repo, save_extra_repos,
    find_git_repos, _collect_repos, pull_repo,
    get_changed_files, commit_in_batches,
)

ALIASES_FILE = os.path.join(_HERE, "repos_aliases.json")

# Palette — baseada no site Obsidian (black + purple)
BG      = "#000000"   # fundo principal (body do site)
BG2     = "#111111"   # sidebar / cards
BG3     = "#1a1a1a"   # inputs / tooltips
BG4     = "#222222"   # hover / seleção
BORDER  = "#333333"   # bordas
TEXT    = "#ffffff"   # texto principal
DIM     = "#cccccc"   # texto secundário
BLUE    = "#9e26d6"   # ação primária → roxo do site
BLUE_LT = "#b33dd6"   # hover roxo
GREEN   = "#3fb950"
RED     = "#f85149"
YELLOW  = "#d29922"
ACCENT  = "#9e26d6"   # logo / destaque (roxo)

FUI   = ("Segoe UI", 9)
FBOLD = ("Segoe UI", 9, "bold")
FH1   = ("Segoe UI", 12, "bold")
FH2   = ("Segoe UI", 10, "bold")
FCODE = ("Consolas", 9)
FMONO = ("Consolas", 8)
FSMALL= ("Segoe UI", 8)


# ── Alias helpers ─────────────────────────────────────────────────────────────
def _load_aliases() -> dict:
    if not os.path.exists(ALIASES_FILE):
        return {}
    try:
        with open(ALIASES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_aliases(d: dict):
    with open(ALIASES_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)

def _set_alias(path: str, alias: str):
    d = _load_aliases()
    if alias:
        d[path] = alias
    else:
        d.pop(path, None)
    _save_aliases(d)

def _get_alias(path: str) -> str:
    return _load_aliases().get(path, "")


# ── Stdout redirect ───────────────────────────────────────────────────────────
class _QueueOut:
    def __init__(self, q, tag="log"):
        self.q, self.tag = q, tag
    def write(self, text):
        if text:
            self.q.put(("log", self.tag, text))
    def flush(self): pass


# ── Slim Scrollbar ────────────────────────────────────────────────────────────
class SlimScrollbar(tk.Canvas):
    def __init__(self, master, orient="vertical", command=None, **kw):
        kw.setdefault("width",  8 if orient == "vertical" else 200)
        kw.setdefault("height", 200 if orient == "vertical" else 8)
        kw.setdefault("bg", BG2)
        kw.setdefault("highlightthickness", 0)
        kw.setdefault("bd", 0)
        super().__init__(master, **kw)
        self._orient  = orient
        self._command = command
        self._thumb   = None
        self._drag    = None
        self._pos     = (0.0, 1.0)

        self.bind("<Configure>",       self._redraw)
        self.bind("<ButtonPress-1>",   self._press)
        self.bind("<B1-Motion>",       self._drag_move)
        self.bind("<ButtonRelease-1>", lambda _: setattr(self, "_drag", None))
        self.bind("<Enter>", lambda _: self._color(ACCENT))
        self.bind("<Leave>", lambda _: self._color("#7a1fa2"))

    def set(self, lo, hi):
        self._pos = (float(lo), float(hi))
        self._redraw()

    def _redraw(self, _=None):
        self.delete("thumb")
        lo, hi = self._pos
        W, H, p = self.winfo_width(), self.winfo_height(), 2
        if self._orient == "vertical":
            y0 = p + lo * (H - 2*p)
            y1 = max(p + hi * (H - 2*p), y0 + 20)
            self._thumb = self.create_rectangle(p, y0, W-p, y1,
                fill="#7a1fa2", outline="", tags="thumb")
        else:
            x0 = p + lo * (W - 2*p)
            x1 = max(p + hi * (W - 2*p), x0 + 20)
            self._thumb = self.create_rectangle(x0, p, x1, H-p,
                fill="#7a1fa2", outline="", tags="thumb")

    def _color(self, c):
        if self._thumb:
            self.itemconfig(self._thumb, fill=c)

    def _press(self, e):
        self._drag = (e.x, e.y)

    def _drag_move(self, e):
        if self._drag is None or self._command is None:
            return
        lo, hi = self._pos
        W = max(self.winfo_width(), 1)
        H = max(self.winfo_height(), 1)
        d = (e.y - self._drag[1]) / H if self._orient == "vertical"             else (e.x - self._drag[0]) / W
        self._drag = (e.x, e.y)
        self._command("moveto", max(0.0, min(lo + d, 1.0 - (hi - lo))))


# ── Dark Spinbox ──────────────────────────────────────────────────────────────
class DarkSpinbox(tk.Frame):
    def __init__(self, master, variable, from_=10, to=3600):
        super().__init__(master, bg=BG3,
                         highlightbackground=BORDER, highlightthickness=1)
        self._var  = variable
        self._from = from_
        self._to   = to
        e = tk.Entry(self, textvariable=variable, bg=BG3, fg=TEXT,
                     insertbackground=ACCENT, relief="flat", font=FUI,
                     width=5, justify="center", bd=4)
        e.pack(side=tk.LEFT)
        e.bind("<FocusIn>",  lambda _: self.config(highlightbackground=ACCENT))
        e.bind("<FocusOut>", lambda _: self.config(highlightbackground=BORDER))
        bf = tk.Frame(self, bg=BG3)
        bf.pack(side=tk.LEFT, padx=(0, 2))
        for txt, d in [("\u25b2", 1), ("\u25bc", -1)]:
            b = tk.Label(bf, text=txt, bg=BG3, fg=DIM, font=("Segoe UI", 6),
                         cursor="hand2")
            b.pack()
            b.bind("<Button-1>", lambda _, s=d: self._step(s))
            b.bind("<Enter>", lambda e, w=b: w.config(fg=ACCENT))
            b.bind("<Leave>", lambda e, w=b: w.config(fg=DIM))

    def _step(self, d):
        try:
            v = int(self._var.get())
        except Exception:
            v = 60
        self._var.set(max(self._from, min(self._to, v + d * 10)))


# ═════════════════════════════════════════════════════════════════════════════
class AutoGitApp(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1060x700")
        self.minsize(820, 560)
        self.configure(bg=BG)

        self._queue     = queue.Queue()
        self._stop_ev   = threading.Event()
        self._task_stop_ev = threading.Event()
        self._running   = False
        self._task_running = False
        self._task_name = ""
        self._interval  = tk.IntVar(value=60)
        self._scan_root = tk.StringVar(value="")
        self._sel_repo  = None       # caminho selecionado
        self._repo_st   = {}         # path -> (tag, summary)

        self._load_icon()
        self._apply_style()
        self._build_ui()
        self.after(20, self._apply_windows_titlebar_theme)
        self._refresh_repos()
        self._process_queue()

    def _apply_windows_titlebar_theme(self):
        """Força barra de título escura no Windows quando suportado."""
        if sys.platform != "win32":
            return
        try:
            hwnd = self.winfo_id()
            # DWMWA_USE_IMMERSIVE_DARK_MODE = 20 (Win10 1809+), 19 em builds antigas.
            dark = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark), ctypes.sizeof(dark))
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(dark), ctypes.sizeof(dark))

            # Tenta forçar caption preto e texto claro (Win11, pode não existir em builds antigas).
            caption_color = ctypes.c_int(0x000000)  # COLORREF em BGR, preto.
            text_color = ctypes.c_int(0xFFFFFF)     # branco.
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 35, ctypes.byref(caption_color), ctypes.sizeof(caption_color)
            )
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 36, ctypes.byref(text_color), ctypes.sizeof(text_color)
            )
        except Exception:
            pass

    # ── ícone ─────────────────────────────────────────────────────────────────
    def _load_icon(self):
        self._logo_img = None
        self._logo_app = None
        # Usa PNG como fonte principal para exibir logo no app e no ícone da janela.
        for f in ["icon.png", "logo6.png"]:
            p = os.path.join(_RES, f)
            if not os.path.exists(p):
                continue
            try:
                img = tk.PhotoImage(file=p)
                self.iconphoto(True, img)
                self._logo_app = img
                ratio = max(1, img.width() // 28)
                self._logo_img = img.subsample(ratio, ratio)
                break
            except Exception:
                pass

        # Em Windows, aplica .ico quando existir (atalhos/integrações do sistema).
        p_ico = os.path.join(_RES, "icon.ico")
        if os.path.exists(p_ico):
            try:
                self.iconbitmap(p_ico)
            except Exception:
                pass

        if self._logo_img:
            return
        self._logo_img = self._make_oct(26, ACCENT)
        try:
            self.iconphoto(True, self._logo_img)
        except Exception:
            pass

    def _make_oct(self, size, color):
        img = tk.PhotoImage(width=size, height=size)
        cx = cy = size / 2
        r  = size / 2 - 1
        pts = [(cx + r * math.cos(math.radians(22.5 + i*45)),
                cy + r * math.sin(math.radians(22.5 + i*45))) for i in range(8)]
        def inside(x, y):
            n, ins, j = len(pts), False, len(pts)-1
            for i in range(n):
                xi,yi = pts[i]; xj,yj = pts[j]
                if ((yi>y)!=(yj>y)) and x < (xj-xi)*(y-yi)/(yj-yi+1e-9)+xi:
                    ins = not ins
                j = i
            return ins
        hx  = color if color.startswith("#") else f"#{color.lstrip('#')}"
        out = BG  # cor de fundo para pixels fora do octógono
        rows = []
        for y in range(size):
            row = [hx if inside(x+.5, y+.5) else out for x in range(size)]
            rows.append("{" + " ".join(row) + "}")
        img.put(" ".join(rows))
        return img

    # ── estilos ───────────────────────────────────────────────────────────────
    def _apply_style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=TEXT,
                    fieldbackground=BG3, troughcolor=BG2,
                    bordercolor=BORDER, selectbackground=ACCENT,
                    selectforeground=TEXT, font=FUI)
        s.configure("TFrame",     background=BG)
        s.configure("TLabel",     background=BG,  foreground=TEXT, font=FUI)
        s.configure("TSeparator", background=BORDER)
        s.configure("Sidebar.TFrame", background=BG2)
        # Botão primário — roxo do site
        s.configure("Primary.TButton", background=ACCENT, foreground=TEXT,
                    font=FBOLD, borderwidth=0, padding=(12,6), relief="flat")
        s.map("Primary.TButton",
              background=[("active", BLUE_LT), ("disabled", BG3)],
              foreground=[("disabled", DIM)])
        s.configure("Fetch.TButton", background=GREEN, foreground=TEXT,
                    font=FBOLD, borderwidth=0, padding=(12,6), relief="flat")
        s.map("Fetch.TButton",
              background=[("active", "#2ea043"), ("disabled", BG3)],
              foreground=[("disabled", DIM)])
        # Botão padrão — fundo escuro, borda sutil
        s.configure("TButton", background=BG3, foreground=TEXT,
                    font=FUI, borderwidth=1, bordercolor=BORDER,
                    padding=(9,5), relief="flat")
        s.map("TButton",
              background=[("active", BG4)],
              bordercolor=[("active", ACCENT)])
        s.configure("Stop.TButton", background=BG3, foreground=RED,
                    font=FBOLD, borderwidth=1, padding=(12,6), relief="flat")
        s.map("Stop.TButton", background=[("active", BG4)])
        # Treeview
        s.configure("Treeview", background=BG2, foreground=TEXT,
                    fieldbackground=BG2, borderwidth=0,
                    rowheight=32, font=FUI)
        s.configure("Treeview.Heading", background=BG3, foreground=DIM,
                    font=FSMALL, borderwidth=0, relief="flat")
        s.map("Treeview",
              background=[("selected", "#3d1560")],
              foreground=[("selected", TEXT)])
        s.configure("TEntry", fieldbackground=BG3, foreground=TEXT,
                    bordercolor=BORDER, insertcolor=TEXT, font=FUI)

    # ── build UI ──────────────────────────────────────────────────────────────
    def _build_ui(self):
        self._build_titlebar()
        self._build_toolbar()
        pane = tk.PanedWindow(self, orient=tk.HORIZONTAL,
                              bg=BG, sashwidth=5, sashpad=0,
                              sashrelief="flat", bd=0,
                              sashcursor="sb_h_double_arrow")
        pane.pack(fill=tk.BOTH, expand=True)
        self._build_sidebar(pane)
        self._build_main(pane)
        self._build_statusbar()

    # ── barra de título ───────────────────────────────────────────────────────
    def _build_titlebar(self):
        bar = tk.Frame(self, bg=BG2, height=52)
        bar.pack(fill=tk.X)
        bar.pack_propagate(False)

        if self._logo_img:
            tk.Label(bar, image=self._logo_img, bg=BG2).pack(
                side=tk.LEFT, padx=(14,6), pady=10)
        else:
            tk.Label(bar, text="\u2b21", font=("Segoe UI",18),
                     bg=BG2, fg=ACCENT).pack(side=tk.LEFT, padx=(14,6), pady=10)

        # Título com span colorido — estilo do site
        tk.Label(bar, text="Obsidian ", font=FH1, bg=BG2, fg=TEXT).pack(
            side=tk.LEFT, pady=10)
        tk.Label(bar, text="AutoGit", font=FH1, bg=BG2, fg=ACCENT).pack(
            side=tk.LEFT, pady=10)

        tk.Frame(bar, bg=BORDER, width=1).pack(side=tk.LEFT, fill=tk.Y,
                                                padx=14, pady=10)

        tk.Label(bar, text="Raiz:", bg=BG2, fg=DIM, font=FUI).pack(
            side=tk.LEFT, padx=(0,4))
        rf = tk.Frame(bar, bg=BG3, highlightbackground=ACCENT,
                      highlightthickness=0)
        rf.pack(side=tk.LEFT)
        entry_root = tk.Entry(rf, textvariable=self._scan_root, bg=BG3,
                              fg=TEXT, insertbackground=ACCENT,
                              relief="flat", font=FUI, width=30, bd=4)
        entry_root.pack(side=tk.LEFT)
        # brilho ao focar — simula o glow do site
        entry_root.bind("<FocusIn>",
            lambda _: rf.config(highlightthickness=1))
        entry_root.bind("<FocusOut>",
            lambda _: rf.config(highlightthickness=0))
        lbl_ico = tk.Label(rf, text="\U0001f4c2", bg=BG3, fg=DIM,
                           cursor="hand2", font=FUI)
        lbl_ico.pack(side=tk.LEFT, padx=4)
        lbl_ico.bind("<Button-1>", lambda _: self._browse_root())

        tk.Frame(bar, bg=BORDER, width=1).pack(side=tk.LEFT, fill=tk.Y,
                                                padx=12, pady=10)
        tk.Label(bar, text="Intervalo (s):", bg=BG2, fg=DIM, font=FUI).pack(
            side=tk.LEFT, padx=(0,4))
        DarkSpinbox(bar, self._interval).pack(side=tk.LEFT, ipady=2)

        # borda inferior roxa — igual ao nav do site
        tk.Frame(bar, bg=ACCENT, height=2).place(relx=0, rely=1.0,
                                                  relwidth=1, anchor="sw")

    # ── toolbar ───────────────────────────────────────────────────────────────
    def _build_toolbar(self):
        bar = tk.Frame(self, bg=BG3, height=42)
        bar.pack(fill=tk.X)
        bar.pack_propagate(False)
        tk.Frame(bar, bg=BORDER, height=1).place(relx=0, rely=0, relwidth=1)
        tk.Frame(bar, bg=BG3, height=1).place(relx=0, rely=0,
                                               y=1, relwidth=1)

        inner = tk.Frame(bar, bg=BG3)
        inner.pack(side=tk.LEFT, padx=8, fill=tk.Y)

        def tb(text, cmd, style="TButton"):
            b = ttk.Button(inner, text=text, command=cmd, style=style)
            b.pack(side=tk.LEFT, padx=3, pady=5)
            return b

        self._btn_pull = tb("\u27f3  Fetch agora", self._pull_once,
                    style="Fetch.TButton")
        self._btn_auto = tb("\u25b6  Iniciar Auto-Pull", self._toggle_auto,
                             style="Primary.TButton")
        self._btn_commit = tb("\u25b6  Iniciar Auto-Commit", self._manual_commit,
                      style="Primary.TButton")
        self._btn_stop_task = tb("\u23f9  Parar", self._stop_current_task,
                     style="Stop.TButton")
        self._btn_stop_task.config(state=tk.DISABLED)
        tk.Frame(inner, bg=BORDER, width=1).pack(side=tk.LEFT,
                                                   fill=tk.Y, padx=6, pady=5)
        tb("\uff0b  Add Repo",   self._add_repo)
        tb("\U0001f4c2  Scan",   self._scan_folder)
        tb("\U0001f5d1  Remover", self._remove_repo)

        self._lbl_cycle = tk.Label(bar, text="", bg=BG3, fg=DIM, font=FMONO)
        self._lbl_cycle.pack(side=tk.RIGHT, padx=14)

    # ── sidebar ───────────────────────────────────────────────────────────────
    def _build_sidebar(self, pane):
        frame = tk.Frame(pane, bg=BG2)
        pane.add(frame, minsize=220, width=260)

        hdr = tk.Frame(frame, bg=BG2)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="REPOSIT\u00d3RIOS", bg=BG2, fg=DIM,
                 font=("Segoe UI", 8, "bold")).pack(side=tk.LEFT, padx=12, pady=8)
        self._lbl_count = tk.Label(hdr, text="0", bg=BG2, fg=DIM, font=FMONO)
        self._lbl_count.pack(side=tk.RIGHT, padx=10)
        tk.Frame(frame, bg=BORDER, height=1).pack(fill=tk.X)

        tf = tk.Frame(frame, bg=BG2)
        tf.pack(fill=tk.BOTH, expand=True)

        self._tree = ttk.Treeview(tf, columns=("status",),
                                   show="tree", selectmode="browse")
        self._tree.column("#0",     width=178, minwidth=100)
        self._tree.column("status", width=72,  minwidth=50, anchor=tk.E)

        vsb = SlimScrollbar(tf, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y, pady=2)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        for tag, fg in [("ok", GREEN), ("updated", BLUE_LT),
                         ("error", RED), ("busy", YELLOW),
                         ("idle", DIM), ("warn", YELLOW)]:
            self._tree.tag_configure(tag, foreground=fg)
        # linha selecionada — destaque roxo escuro
        self._tree.tag_configure("selected_row", background="#3d1560")

        self._tree.bind("<<TreeviewSelect>>", self._on_sel)
        self._tree.bind("<Button-3>",         self._on_rclick)

        # menu de contexto
        self._ctx = tk.Menu(self, tearoff=0, bg=BG3, fg=TEXT,
                             activebackground=ACCENT, activeforeground=TEXT,
                             bd=0, relief="flat", font=FUI)
        self._ctx.add_command(label="\u270f  Adicionar Apelido",
                               command=self._ctx_alias)
        self._ctx.add_command(label="\U0001f4dd  Editar Caminho",
                               command=self._ctx_edit_path)
        self._ctx.add_separator()
        self._ctx.add_command(label="\U0001f5d1  Remover Repo",
                               command=self._remove_repo,
                               foreground=RED, activeforeground=RED)

    # ── painel principal ──────────────────────────────────────────────────────
    def _build_main(self, pane):
        frame = tk.Frame(pane, bg=BG)
        pane.add(frame, minsize=400)

        top = tk.Frame(frame, bg=BG, height=36)
        top.pack(fill=tk.X)
        top.pack_propagate(False)
        tk.Label(top, text="Atividade", bg=BG, fg=TEXT, font=FH2).pack(
            side=tk.LEFT, padx=14, pady=6)
        ttk.Button(top, text="Limpar", width=7,
                   command=self._clear_log).pack(side=tk.RIGHT, padx=10, pady=4)
        tk.Frame(frame, bg=BORDER, height=1).pack(fill=tk.X)

        lf = tk.Frame(frame, bg=BG)
        lf.pack(fill=tk.BOTH, expand=True)

        self._log = tk.Text(lf, bg=BG, fg=TEXT, font=FCODE,
                             wrap=tk.WORD, state=tk.DISABLED,
                             bd=0, relief=tk.FLAT, highlightthickness=0,
                             selectbackground=BG3, padx=8, pady=4)
        vsb2 = SlimScrollbar(lf, orient="vertical", command=self._log.yview)
        self._log.configure(yscrollcommand=vsb2.set)
        vsb2.pack(side=tk.RIGHT, fill=tk.Y, pady=4, padx=(0,2))
        self._log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        for tag, fg in [("ts", DIM), ("ok", GREEN), ("updated", BLUE_LT),
                         ("error", RED), ("warn", YELLOW), ("info", TEXT),
                         ("log", DIM), ("dim", DIM), ("sep", BORDER)]:
            self._log.tag_configure(tag, foreground=fg, font=FCODE)

    # ── status bar ────────────────────────────────────────────────────────────
    def _build_statusbar(self):
        bar = tk.Frame(self, bg=BG2, height=26)
        bar.pack(fill=tk.X, side=tk.BOTTOM)
        bar.pack_propagate(False)
        # borda superior com leve toque roxo
        tk.Frame(bar, bg=ACCENT, height=1).place(relx=0, rely=0, relwidth=1)
        self._lbl_status = tk.Label(bar, text="Pronto", bg=BG2,
                                     fg=DIM, font=FMONO)
        self._lbl_status.pack(side=tk.LEFT, padx=12)
        # indicador roxo piscante quando auto está ativo
        self._dot = tk.Label(bar, text="\u25cf", bg=BG2, fg=BG2, font=FMONO)
        self._dot.pack(side=tk.LEFT, padx=(0,6))
        self._lbl_next = tk.Label(bar, text="", bg=BG2, fg=DIM, font=FMONO)
        self._lbl_next.pack(side=tk.RIGHT, padx=12)

    # ── log ───────────────────────────────────────────────────────────────────
    def _log_write(self, tag, msg):
        self._log.configure(state=tk.NORMAL)
        ts = datetime.now().strftime("%H:%M:%S")
        self._log.insert(tk.END, f"[{ts}] ", "ts")
        self._log.insert(tk.END, msg + "\n", tag)
        self._log.configure(state=tk.DISABLED)
        self._log.see(tk.END)

    def _log_sep(self):
        self._log.configure(state=tk.NORMAL)
        self._log.insert(tk.END, "\u2500" * 64 + "\n", "sep")
        self._log.configure(state=tk.DISABLED)

    def _clear_log(self):
        self._log.configure(state=tk.NORMAL)
        self._log.delete("1.0", tk.END)
        self._log.configure(state=tk.DISABLED)

    # ── lista de repos ────────────────────────────────────────────────────────
    def _refresh_repos(self):
        root  = self._scan_root.get().strip()
        auto  = find_git_repos(root) if root and os.path.isdir(root) else []
        extra = load_extra_repos()
        aliases = _load_aliases()

        seen, all_r = set(), []
        for r in auto + extra:
            n = os.path.abspath(r)
            if n not in seen:
                seen.add(n)
                all_r.append(n)
        all_r.sort()

        sel = self._sel_repo
        self._tree.delete(*self._tree.get_children())
        for r in all_r:
            alias = aliases.get(r, "")
            display = alias if alias else os.path.basename(r)
            tag, summary = self._repo_st.get(r, ("idle", ""))
            bullet = {"ok":"\u25cf","updated":"\u2193","error":"\u2717",
                      "busy":"\u25cc","idle":"\u25cb","warn":"\u26a0"}.get(tag,"\u25cb")
            self._tree.insert("", tk.END, iid=r,
                               text=f"  {display}",
                               values=(f"{bullet} {summary[:12]}",),
                               tags=(tag,))
        self._lbl_count.config(text=str(len(all_r)))
        if sel and self._tree.exists(sel):
            self._tree.selection_set(sel)

    def _on_sel(self, _=None):
        sel = self._tree.selection()
        self._sel_repo = sel[0] if sel else None

    # ── menu de contexto ──────────────────────────────────────────────────────
    def _on_rclick(self, event):
        iid = self._tree.identify_row(event.y)
        if iid:
            self._tree.selection_set(iid)
            self._sel_repo = iid
            try:
                self._ctx.tk_popup(event.x_root, event.y_root)
            finally:
                self._ctx.grab_release()

    def _ctx_alias(self):
        repo = self._sel_repo
        if not repo:
            return
        current = _get_alias(repo)
        name    = os.path.basename(repo)

        win = tk.Toplevel(self)
        win.title(f"{APP_NAME} - Apelido")
        win.geometry("380x160")
        win.configure(bg=BG)
        win.resizable(False, False)
        win.grab_set()
        # borda roxa no topo da janela
        tk.Frame(win, bg=ACCENT, height=3).pack(fill=tk.X)

        tk.Label(win, text=f"Apelido para: {name}", bg=BG,
                 fg=DIM, font=FUI).pack(anchor=tk.W, padx=16, pady=(12,4))
        var = tk.StringVar(value=current)
        ef  = tk.Frame(win, bg=BG3, highlightbackground=ACCENT, highlightthickness=1)
        ef.pack(fill=tk.X, padx=16)
        e = tk.Entry(ef, textvariable=var, bg=BG3, fg=TEXT,
                     insertbackground=ACCENT, relief="flat", font=FUI, bd=4)
        e.pack(fill=tk.X)
        e.focus_set()
        e.select_range(0, tk.END)

        bf = tk.Frame(win, bg=BG)
        bf.pack(pady=12)

        def _ok():
            _set_alias(repo, var.get().strip())
            self._refresh_repos()
            win.destroy()

        ttk.Button(bf, text="Cancelar", command=win.destroy).pack(side=tk.LEFT, padx=6)
        ttk.Button(bf, text="Salvar", style="Primary.TButton",
                   command=_ok).pack(side=tk.LEFT, padx=6)
        win.bind("<Return>", lambda _: _ok())
        win.bind("<Escape>", lambda _: win.destroy())

    def _ctx_edit_path(self):
        repo = self._sel_repo
        if not repo:
            return
        new_p = filedialog.askdirectory(
            title="Novo caminho do reposit\u00f3rio",
            initialdir=repo if os.path.isdir(repo) else _HERE)
        if not new_p:
            return
        new_p = os.path.abspath(new_p)
        try:
            validate_repo(new_p)
        except Exception as exc:
            messagebox.showerror("Erro", str(exc))
            return
        extras = load_extra_repos()
        if repo in extras:
            extras[extras.index(repo)] = new_p
            save_extra_repos(extras)
        aliases = _load_aliases()
        if repo in aliases:
            aliases[new_p] = aliases.pop(repo)
            _save_aliases(aliases)
        if repo in self._repo_st:
            self._repo_st[new_p] = self._repo_st.pop(repo)
        self._sel_repo = new_p
        self._log_write("info", f"Caminho atualizado: {new_p}")
        self._refresh_repos()

    # ── toolbar actions ───────────────────────────────────────────────────────
    def _browse_root(self):
        p = filedialog.askdirectory(title="Diret\u00f3rio raiz dos reposit\u00f3rios")
        if p:
            self._scan_root.set(os.path.normpath(p))
            self._refresh_repos()

    def _scan_folder(self):
        if not self._scan_root.get().strip():
            self._browse_root()
            return
        self._refresh_repos()
        self._log_write("info", f"Scan: {self._scan_root.get()}")

    def _add_repo(self):
        p = filedialog.askdirectory(title="Selecione o reposit\u00f3rio Git")
        if not p:
            return
        try:
            msg = add_extra_repo(p)
            self._log_write("ok", msg)
            self._refresh_repos()
        except Exception as exc:
            self._log_write("error", str(exc))
            messagebox.showerror("Erro", str(exc))

    def _remove_repo(self):
        repo = self._sel_repo
        if not repo:
            messagebox.showinfo(APP_NAME, "Selecione um reposit\u00f3rio na lista.")
            return
        name = _get_alias(repo) or os.path.basename(repo)
        if not messagebox.askyesno("Remover", f"Remover \u2018{name}\u2019 da lista?"):
            return
        remove_extra_repo(repo)
        _set_alias(repo, "")
        self._repo_st.pop(repo, None)
        self._sel_repo = None
        self._log_write("warn", f"Removido: {name}")
        self._refresh_repos()

    # ── pull ──────────────────────────────────────────────────────────────────
    def _pull_once(self):
        if self._task_running:
            self._log_write("warn", "Já existe uma operação em andamento.")
            return
        root  = self._scan_root.get().strip()
        repos = _collect_repos(root) if root and os.path.isdir(root) else load_extra_repos()
        if not repos:
            self._log_write("warn", "Nenhum reposit\u00f3rio encontrado.")
            return
        self._begin_task("fetch")
        threading.Thread(target=self._pull_worker, args=(repos,), daemon=True).start()

    def _pull_worker(self, repos):
        self._queue.put(("status", "Executando pull\u2026"))
        self._log_sep()
        self._queue.put(("log", "info", f"Fetch \u2014 {len(repos)} reposit\u00f3rio(s)"))
        updated = skipped = errors = 0
        aliases = _load_aliases()

        for repo in repos:
            if self._task_stop_ev.is_set():
                self._queue.put(("log", "warn", "Operação de fetch interrompida pelo usuário."))
                break
            name = aliases.get(repo, os.path.basename(repo))
            self._queue.put(("repo_busy", repo))
            self._queue.put(("log", "dim", f"\u2192 {name}  ({repo})"))
            try:
                validate_repo(repo)
                result = pull_repo(repo)
                if "sem novidades" in result or "sem upstream" in result:
                    self._queue.put(("log", "ok",      f"  {name} \u2014 {result}"))
                    self._queue.put(("repo_st", repo, "ok", "\u2713"))
                    skipped += 1
                else:
                    self._queue.put(("log", "updated", f"  {name} \u2014 {result}"))
                    self._queue.put(("repo_st", repo, "updated", "\u2193"))
                    updated += 1
            except subprocess.CalledProcessError as exc:
                det = (exc.stderr or exc.stdout or str(exc)).strip()
                if "not possible to fast-forward" in det.lower() or "diverged" in det.lower():
                    self._queue.put(("log", "warn", f"  {name} \u2014 branch divergiu"))
                    self._queue.put(("repo_st", repo, "warn", "\u26a0"))
                else:
                    self._queue.put(("log", "error", f"  {name} \u2014 {det[:100]}"))
                    self._queue.put(("repo_st", repo, "error", "\u2717"))
                errors += 1
            except Exception as exc:
                self._queue.put(("log",     "error", f"  {name} \u2014 {exc}"))
                self._queue.put(("repo_st", repo, "error", "\u2717"))
                errors += 1

        if self._task_stop_ev.is_set():
            s = f"Interrompido \u2014 Atualizados: {updated}  Sem novidades: {skipped}  Erros: {errors}"
        else:
            s = f"Conclu\u00eddo \u2014 Atualizados: {updated}  Sem novidades: {skipped}  Erros: {errors}"
        self._queue.put(("log",    "info", s))
        self._queue.put(("status", s))
        self._end_task()

    # ── auto-pull ─────────────────────────────────────────────────────────────
    def _toggle_auto(self):
        if self._running: self._stop_auto()
        else:             self._start_auto()

    def _start_auto(self):
        self._running = True
        self._stop_ev.clear()
        self._btn_auto.config(text="\u23f9  Parar Auto-Pull", style="Stop.TButton")
        self._log_write("ok", f"Auto-Pull iniciado \u2014 intervalo: {self._interval.get()}s")
        self._dot.config(fg=ACCENT)   # ponto roxo ativo
        threading.Thread(target=self._auto_loop, daemon=True).start()

    def _stop_auto(self):
        self._running = False
        self._stop_ev.set()
        self._btn_auto.config(text="\u25b6  Iniciar Auto-Pull", style="Primary.TButton")
        self._lbl_next.config(text="")
        self._lbl_cycle.config(text="")
        self._dot.config(fg=BG2)      # ponto oculto
        self._log_write("warn", "Auto-Pull interrompido.")
        self._queue.put(("status", "Parado"))

    def _auto_loop(self):
        cycle = 0
        while not self._stop_ev.is_set():
            cycle += 1
            root  = self._scan_root.get().strip()
            repos = _collect_repos(root) if root and os.path.isdir(root) else load_extra_repos()
            if not repos:
                self._queue.put(("log", "warn", "Nenhum reposit\u00f3rio encontrado."))
            else:
                self._queue.put(("cycle", cycle))
                self._pull_worker(repos)
            interval = self._interval.get()
            nxt = time.time() + interval
            while time.time() < nxt and not self._stop_ev.is_set():
                self._queue.put(("countdown", int(nxt - time.time())))
                time.sleep(1)

    # ── commit manual ─────────────────────────────────────────────────────────
    def _manual_commit(self):
        if self._task_running:
            messagebox.showinfo(APP_NAME, "Já existe uma operação em andamento.")
            return
        repo = self._sel_repo
        if not repo:
            messagebox.showinfo(APP_NAME, "Selecione um reposit\u00f3rio na lista.")
            return
        name = _get_alias(repo) or os.path.basename(repo)
        win = tk.Toplevel(self)
        win.title(f"{APP_NAME} - Commit - {name}")
        win.geometry("560x360")
        win.configure(bg=BG)
        win.resizable(False, False)
        win.grab_set()
        tk.Frame(win, bg=ACCENT, height=3).pack(fill=tk.X)

        tk.Label(win, text=f"Reposit\u00f3rio: {name}", bg=BG,
                 fg=DIM, font=FUI).pack(anchor=tk.W, padx=16, pady=(12,2))
        tk.Label(win, text="Summary:", bg=BG, fg=TEXT,
                 font=FBOLD).pack(anchor=tk.W, padx=16, pady=(8,2))
        summary_var = tk.StringVar(value="update")
        ef  = tk.Frame(win, bg=BG3, highlightbackground=ACCENT, highlightthickness=1)
        ef.pack(fill=tk.X, padx=16)
        e = tk.Entry(ef, textvariable=summary_var, bg=BG3, fg=TEXT,
                     insertbackground=ACCENT, relief="flat", font=FUI, bd=4)
        e.pack(fill=tk.X)
        e.focus_set()
        e.select_range(0, tk.END)

        tk.Label(win, text="Comentário (opcional):", bg=BG, fg=TEXT,
                 font=FBOLD).pack(anchor=tk.W, padx=16, pady=(10,2))
        comment_var = tk.StringVar(value="")
        cf = tk.Frame(win, bg=BG3, highlightbackground=ACCENT, highlightthickness=1)
        cf.pack(fill=tk.X, padx=16)
        tk.Entry(cf, textvariable=comment_var, bg=BG3, fg=TEXT,
                 insertbackground=ACCENT, relief="flat", font=FUI, bd=4).pack(fill=tk.X)

        opts = tk.Frame(win, bg=BG)
        opts.pack(fill=tk.X, padx=16, pady=(12, 2))
        tk.Label(opts, text="Batch size:", bg=BG, fg=DIM, font=FUI).pack(side=tk.LEFT)
        batch_var = tk.IntVar(value=100)
        DarkSpinbox(opts, batch_var, from_=1, to=100000).pack(side=tk.LEFT, padx=(6, 16))
        tk.Label(opts, text="Delay (s):", bg=BG, fg=DIM, font=FUI).pack(side=tk.LEFT)
        delay_var = tk.IntVar(value=2)
        DarkSpinbox(opts, delay_var, from_=0, to=3600).pack(side=tk.LEFT, padx=(6, 0))

        bf = tk.Frame(win, bg=BG)
        bf.pack(pady=12)

        def _do():
            summary = summary_var.get().strip()
            comment = comment_var.get().strip()
            batch = batch_var.get()
            delay = delay_var.get()

            if not summary:
                messagebox.showwarning(APP_NAME, "Summary não pode estar vazio.")
                return
            if batch <= 0:
                messagebox.showwarning(APP_NAME, "Batch size deve ser maior que zero.")
                return
            if delay < 0:
                messagebox.showwarning(APP_NAME, "Delay não pode ser negativo.")
                return
            win.destroy()

            if not self._begin_task("commit"):
                self._log_write("warn", "Já existe uma operação em andamento.")
                return

            self._log_write(
                "info",
                f"Commit em {name} (summary='{summary}', batch={batch}, delay={delay}s)..."
            )

            def worker():
                old = sys.stdout
                sys.stdout = _QueueOut(self._queue)
                try:
                    completed = commit_in_batches(
                        repo_path=repo,
                        summary=summary,
                        description=comment,
                        batch_size=batch,
                        delay_seconds=float(delay),
                        stop_event=self._task_stop_ev,
                    )
                    if completed:
                        self._queue.put(("log", "ok", f"Commit+push conclu\u00eddo \u2014 {name}"))
                    else:
                        self._queue.put(("log", "warn", f"Commit interrompido \u2014 {name}"))
                except Exception as exc:
                    self._queue.put(("log", "error", f"Erro: {exc}"))
                finally:
                    sys.stdout = old
                    self._end_task()
            threading.Thread(target=worker, daemon=True).start()

        ttk.Button(bf, text="Cancelar", command=win.destroy).pack(side=tk.LEFT, padx=6)
        ttk.Button(bf, text="  Commit & Push  ", style="Primary.TButton",
                   command=_do).pack(side=tk.LEFT, padx=6)
        win.bind("<Return>", lambda _: _do())
        win.bind("<Escape>", lambda _: win.destroy())

    # ── fila de eventos ───────────────────────────────────────────────────────
    def _process_queue(self):
        try:
            while True:
                item = self._queue.get_nowait()
                kind = item[0]
                if kind == "log":
                    _, tag, msg = item
                    for line in msg.splitlines():
                        if line.strip():
                            self._log_write(tag, line)
                elif kind == "status":
                    self._lbl_status.config(text=item[1])
                elif kind == "countdown":
                    s = item[1]
                    c = DIM if s > 15 else ACCENT
                    self._lbl_next.config(text=f"pr\u00f3ximo ciclo em {s:3d}s", fg=c)
                    self._lbl_cycle.config(text=f"{s}s", fg=c)
                elif kind == "cycle":
                    self._lbl_status.config(text=f"Ciclo #{item[1]} em andamento\u2026")
                    self._lbl_next.config(text="")
                elif kind == "repo_busy":
                    self._repo_st[item[1]] = ("busy", "\u2026")
                    self._refresh_repos()
                elif kind == "repo_st":
                    _, repo, tag, summary = item
                    self._repo_st[repo] = (tag, summary)
                    self._refresh_repos()
        except queue.Empty:
            pass
        self.after(80, self._process_queue)

    def _begin_task(self, task_name: str) -> bool:
        if self._task_running:
            return False
        self._task_running = True
        self._task_name = task_name
        self._task_stop_ev.clear()
        self._btn_stop_task.config(state=tk.NORMAL)
        self._btn_pull.config(state=tk.DISABLED)
        self._btn_commit.config(state=tk.DISABLED)
        return True

    def _end_task(self):
        self._task_running = False
        self._task_name = ""
        self._task_stop_ev.clear()
        self.after(0, lambda: self._btn_stop_task.config(state=tk.DISABLED))
        self.after(0, lambda: self._btn_pull.config(state=tk.NORMAL))
        self.after(0, lambda: self._btn_commit.config(state=tk.NORMAL))

    def _stop_current_task(self):
        if not self._task_running:
            self._log_write("warn", "Nenhuma operação manual em andamento para parar.")
            return
        self._task_stop_ev.set()
        self._log_write("warn", f"Parando operação: {self._task_name}...")

    def on_close(self):
        self._stop_ev.set()
        self.destroy()


def main():
    app = AutoGitApp()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()


if __name__ == "__main__":
    main()
