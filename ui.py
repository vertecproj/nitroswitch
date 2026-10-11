"""
ui.py
-----
Painel lateral do NitroSwitch, encostado na borda direita da tela. Visual
"Cockpit Neon" (escolhido pelo usuário em 2026-10-08): fundo escuro com linhas
de varredura, cantos cortados, destaque neon que desliza entre os modos,
ventoinha e métricas ao vivo na MESMA tela, ajustes numa folha por cima.

REESCRITO em 2026-10-08, sem CustomTkinter: tudo é desenhado à mão num único
`tk.Canvas`. Motivos: (1) CustomTkinter não consegue fazer esse visual (cantos
cortados, listras, brilho) e (2) construir ~100 widgets dele levava ~3,3 s - o
canvas sobe em ~0,3 s.

DPI: sem o CustomTkinter não existe mais a "escala dupla" que dava o bug de
janela fora da tela (ver CLAUDE.md). O processo se declara per-monitor aware
ANTES de criar o Tk, então tudo aqui é em pixels FÍSICOS: `winfo_screenwidth`
e `GetSystemMetrics` concordam e a posição X/Y/tamanho vão direto no
`geometry`. Todas as medidas do desenho são em unidades "lógicas" (px a 100%)
multiplicadas por `self.k` (escala do monitor, reduzida se a tela for baixa).
"""

import ctypes
import ctypes.wintypes as wintypes
import logging
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont

from PIL import Image, ImageDraw, ImageTk

import config
import fan_control
import hotkey
import icons
import power_modes
import startup
import sysmetrics
import updater
from version import __version__

log = logging.getLogger("ui")

# Resolução do temporizador do Windows em 1 ms: sem isso `after()` só dispara a
# cada ~15,6 ms e a animação perde quadros de forma irregular.
try:
    ctypes.windll.winmm.timeBeginPeriod(1)
except Exception:
    pass

# DPI awareness ANTES de qualquer Tk (ver docstring).
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# Fonte Rajdhani empacotada em assets/ (FR_PRIVATE: só para este processo).
if sys.platform.startswith("win"):
    _assets_dir = os.path.join(
        getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "assets"
    )
    for _font_file in ("Rajdhani-SemiBold.ttf", "Rajdhani-Bold.ttf"):
        _font_path = os.path.join(_assets_dir, _font_file)
        if os.path.exists(_font_path):
            ctypes.windll.gdi32.AddFontResourceExW(_font_path, 0x10, 0)
FONT_DISPLAY = "Rajdhani"
FONT_MONO = "Consolas"
FONT_BODY = "Segoe UI"

# ---------------------------------------------------------------- paleta
C_BG = "#0a0a0e"
C_SCAN = "#0d0d11"      # linhas de varredura
C_SURF = "#101015"      # superfície de caixas
C_LINE = "#1f1f26"      # bordas
C_LINE2 = "#2c2c35"
C_TEXT = "#f2f2f5"
C_MUT = "#8d8d96"
C_DIM = "#5f5f69"
C_AMBER = "#ff9f43"

ACC_SUPERECO = "#5ad1ff"
ACC_ECO = "#7ef9d0"
ACC_BALANCED = "#ffd479"
ACC_BOOST = "#ff6b6b"

PANEL_W = 380           # largura lógica
PAD = 16
CHAMFER = 26            # canto cortado do painel (topo esquerdo)
CARD_H, CARD_GAP = 58, 6
FAN_H = 236
LIVE_H = 112

# (modo, cor, título, legenda) na ordem da tela
MODE_SPECS = [
    # {hz} = taxa de atualização que o modo usa NESTA tela (ver power_modes.mode_hz)
    ("boost", ACC_BOOST, "BOOST", "{hz}Alto desempenho"),
    ("balanced", ACC_BALANCED, "EQUILIBRADO", "{hz}Meio-termo · Silencioso"),
    ("eco", ACC_ECO, "ECO", "{hz}Silencioso · Economia"),
    ("supereco", ACC_SUPERECO, "SUPER ECO", "CPU 30% · 1.4 GHz · Brilho 25%"),
]
MODE_ACCENT = {m: c for m, c, _t, _s in MODE_SPECS}
MODE_LABELS = {"supereco": "SUPER ECO", "eco": "ECO", "balanced": "EQUILIBRADO", "boost": "BOOST"}
MODE_LABELS_UI = {"supereco": "Super Eco", "eco": "Eco", "balanced": "Equilibrado", "boost": "Boost"}
FAN_SEG = [("auto", "AUTO"), ("silenciosa", "SILENC."), ("equilibrada", "EQUIL."),
           ("maxima", "MÁX."), ("personalizada", "CUSTOM")]
FAN_MAX_RPM = 7000.0

FAN_SAFETY_LINES = [
    "• Automática é o mais seguro: o firmware decide.",
    "• Jogos e renderização: não limite abaixo de 70%.",
    "• Silenciosa (50%) é para uso leve: navegar, vídeo.",
    "• Até 55°C a ventoinha fica no automático (pode até parar).",
    "• Acima de 85°C o limite é ignorado e ela vai a 100%.",
    "• Acima de 95°C, ou sem leitura de temperatura, volta ao automático.",
    "• Ao fechar o app, a ventoinha volta ao automático.",
]


def _mix(c1, c2, t):
    """Mistura duas cores #rrggbb (t=0 -> c1, t=1 -> c2)."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def _heat(temp):
    if not temp:
        return C_DIM
    return ACC_ECO if temp < 60 else ACC_BALANCED if temp < 80 else ACC_BOOST


def _ease(t):  # ease-in-out cúbico
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def _ease_out(t):
    return 1 - (1 - t) ** 3


class ModePopup(tk.Tk):
    sheet_open = False

    def __init__(self, on_close_to_tray):
        super().__init__()
        self.withdraw()  # nasce oculta: sem piscar durante o boot
        self.title("NitroSwitch")
        self.on_close_to_tray = on_close_to_tray
        self.current_mode = None
        self.cfg = config.load()
        self._just_shown = False
        self._learning_hotkey = False
        self._mode_click_enabled = True
        self.on_mode_changed = None  # main.py liga isto ao ícone da bandeja
        self._last_geom = (0, 0, 0, 0)
        self._anims = {}
        self._closing = False
        self._cur_open = 1.0
        self._hwnd_cache = 0
        self.sheet = None
        self._no_fan = False
        self.on_update_ready = None  # main.py liga isto ao ícone da bandeja
        self.updating = False

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=C_BG)

        self.protocol("WM_DELETE_WINDOW", self._hide_to_tray)
        self.bind_all("<Escape>", lambda e: self._hide_to_tray())
        self.bind("<FocusOut>", self._on_focus_out)
        for i, (mode, _c, _t, _s) in enumerate(MODE_SPECS):
            self.bind_all(str(i + 1), lambda e, m=mode: self._key_mode(m))

        self._compute_scale()
        self._build_ui()
        self._position_window()
        self._read_nitrosense_profile_async()

        self.fan_ctrl = fan_control.FanController(
            on_status=lambda st: self._ui_call(lambda st=st: self._on_fan_status(st))
        )
        self._sync_fan_ui()
        self.fan_ctrl.start()

        self.hotkey = hotkey.HotkeyListener(
            binding=self.cfg.get("hotkey"),
            on_trigger=lambda: self._ui_call(self._toggle_popup),
            on_learned=lambda b: self._ui_call(lambda b=b: self._on_hotkey_learned(b)),
        )
        self.hotkey.start()
        self._metrics_tick()

        self.updater = updater.Updater(on_ready=lambda v: self._ui_call(lambda v=v: self._on_update_ready(v)))
        self.updater.start()

    # ------------------------------------------------------------------
    def _ui_call(self, fn):
        """Agenda `fn` na thread da interface, a partir de qualquer thread.
        As threads de ventoinha e de atalho começam antes do `mainloop()` e
        continuam durante o encerramento; nesses instantes o Tk levanta
        RuntimeError - a atualização perdida é descartada (a próxima chega em
        2 s). Ver CLAUDE.md, "Chamadas de Tk vindas de outras threads"."""
        try:
            self.after(0, fn)
        except RuntimeError:
            pass

    # ------------------------------------------------------------------
    def _on_focus_out(self, event):
        if event.widget != self or self._just_shown or self._learning_hotkey:
            return
        # No instante do FocusOut o Tk ainda não sabe quem vai receber o foco
        # (clicar num botão da própria janela também dispara FocusOut). Três
        # checagens espaçadas: só esconde se o foco ficou fora em TODAS.
        self._focus_out_confirmations = 0
        self.after(120, self._check_focus_still_away)

    def _check_focus_still_away(self):
        focused = self.focus_get()
        if focused is not None and focused.winfo_toplevel() == self:
            return
        self._focus_out_confirmations += 1
        if self._focus_out_confirmations >= 3:
            self._hide_to_tray()
        else:
            self.after(120, self._check_focus_still_away)

    # ------------------------------------------------------------------
    # Escala / janela
    # ------------------------------------------------------------------
    def _compute_scale(self):
        try:
            dpi = ctypes.windll.user32.GetDpiForSystem()
        except Exception:
            dpi = 96
        self.screen_w = ctypes.windll.user32.GetSystemMetrics(0)
        self.screen_h = ctypes.windll.user32.GetSystemMetrics(1)
        # área de trabalho (sem a barra de tarefas)
        wa = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(wa), 0):
            self.work = (wa.left, wa.top, wa.right, wa.bottom)
        else:
            self.work = (0, 0, self.screen_w, self.screen_h)
        self.k_dpi = dpi / 96.0
        self.k = self.k_dpi  # reduzido em _build_ui se a altura não couber

    def _hwnd(self) -> int:
        GA_ROOT = 2
        return ctypes.windll.user32.GetAncestor(self.winfo_id(), GA_ROOT)

    def _apply_region(self):
        """Recorta o canto superior esquerdo (chanfro) com uma região poligonal.
        A região é em pixels reais e não acompanha mudança de tamanho."""
        try:
            w, h, _x, _y = self._last_geom
            c = int(CHAMFER * self.k)
            pts = [(c, 0), (w, 0), (w, h), (0, h), (0, c)]
            arr = (wintypes.POINT * len(pts))(*[wintypes.POINT(px, py) for px, py in pts])
            region = ctypes.windll.gdi32.CreatePolygonRgn(arr, len(pts), 1)
            ctypes.windll.user32.SetWindowRgn(self._hwnd(), region, True)
        except Exception as e:
            log.warning(f"Não foi possível recortar o canto da janela: {e}")

    def _position_window(self):
        w, h = self.win_w, self.win_h
        wl, wt, wr, wb = self.work
        x = wr - w
        y = wt + max(0, (wb - wt - h) // 2)
        self._last_geom = (w, h, x, y)
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.update_idletasks()
        self._hwnd_cache = self._hwnd()
        self._apply_region()
        self.after(250, self._prewarm)

    def _prewarm(self):
        """Mapeia a janela uma vez, invisível e fora da tela, para a 1ª abertura
        de verdade não pagar o custo de criar/pintar a janela nativa."""
        if self.winfo_viewable() or self._just_shown or self._closing:
            return
        try:
            self.attributes("-alpha", 0.0)
            self._slide_to(0.0)
            self.deiconify()
            self.update()
            if not self._just_shown:
                self.withdraw()
            self.attributes("-alpha", 1.0)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Primitivas de desenho (coordenadas LÓGICAS -> físicas via self.k)
    # ------------------------------------------------------------------
    def _p(self, v):
        return v * self.k

    def _font(self, family, px, bold=False):
        return tkfont.Font(family=family, size=-max(1, round(px * self.k)),
                           weight="bold" if bold else "normal")

    def _rect(self, x0, y0, x1, y1, fill="", outline="", width=1, tags=()):
        return self.cv.create_rectangle(
            self._p(x0), self._p(y0), self._p(x1), self._p(y1), fill=fill, outline=outline,
            width=max(1, round(width * self.k)) if outline else 0, tags=tags,
        )

    def _poly(self, pts, fill="", outline="", width=1, tags=()):
        flat = [self._p(v) for pt in pts for v in pt]
        return self.cv.create_polygon(
            flat, fill=fill, outline=outline, width=max(1, round(width * self.k)), tags=tags,
        )

    def _line(self, x0, y0, x1, y1, fill, width=1, tags=()):
        return self.cv.create_line(
            self._p(x0), self._p(y0), self._p(x1), self._p(y1), fill=fill,
            width=max(1, round(width * self.k)), tags=tags,
        )

    def _text(self, x, y, text, font, fill, anchor="w", tags=(), width=None):
        kw = {"width": self._p(width)} if width else {}
        return self.cv.create_text(self._p(x), self._p(y), text=text, font=font, fill=fill,
                                   anchor=anchor, tags=tags, **kw)

    def _spaced(self, x, y, text, font, fill, sp=1.5, anchor="w", tags=()):
        """Texto com espaçamento entre letras (o Tk não tem). Devolve
        (ids, largura_lógica)."""
        spx = self._p(sp)
        widths = [font.measure(ch) for ch in text]
        total = sum(widths) + spx * max(0, len(text) - 1)
        cx = self._p(x) - (total if anchor == "e" else total / 2 if anchor == "c" else 0)
        ids = []
        for ch, wd in zip(text, widths):
            ids.append(self.cv.create_text(cx, self._p(y), text=ch, font=font, fill=fill, anchor="w", tags=tags))
            cx += wd + spx
        return ids, total / self.k

    def _hit(self, hits, x0, y0, x1, y1, cb, hover=None, cursor="hand2"):
        hits.append({"r": (self._p(x0), self._p(y0), self._p(x1), self._p(y1)), "cb": cb,
                     "hover": hover, "cursor": cursor})
        return hits[-1]

    # ------------------------------------------------------------------
    # Animação
    # ------------------------------------------------------------------
    def _anim(self, key, dur_ms, fn, done=None, ease=_ease):
        prev = self._anims.pop(key, None)
        if prev is not None:
            try:
                self.after_cancel(prev)
            except Exception:
                pass
        t0 = time.perf_counter()

        def step():
            f0 = time.perf_counter()
            t = (f0 - t0) * 1000.0 / dur_ms
            if t >= 1:
                self._anims.pop(key, None)
                fn(1.0)
                if done:
                    done()
                return
            fn(ease(t))
            cost = (time.perf_counter() - f0) * 1000.0
            self._anims[key] = self.after(max(1, int(16.6 - cost)), step)

        step()

    # ------------------------------------------------------------------
    # Construção da tela
    # ------------------------------------------------------------------
    def _build_ui(self):
        self.cv = cv = tk.Canvas(self, bg=C_BG, highlightthickness=0, bd=0, takefocus=0)
        cv.pack(fill="both", expand=True)
        self.hits = []
        self.sheet_hits = []
        self._hover = None
        self._bg_img = None
        self.accent = ACC_BALANCED
        self.hl_y = 0.0
        self.hl_target = None
        self.mode_state = {m: "idle" for m, *_ in MODE_SPECS}
        self.cap_dragging = False
        self._fan_mode_cache = "auto"
        self._fan_filled = 0
        self._status_dot = C_DIM
        self._status_cache = ("NENHUM MODO ATIVO", C_MUT)
        self._ns_text = ""

        # layout lógico (a altura total define a escala se a tela for baixa)
        self.Y_MODO = 66
        self.Y_STATUS = 92
        self.Y_CARDS = 112
        y_cards_end = self.Y_CARDS + 4 * (CARD_H + CARD_GAP)
        self.Y_PG = y_cards_end + 2
        self.Y_FAN_SEC = self.Y_PG + 16
        self.Y_FAN_BOX = self.Y_FAN_SEC + 18
        self.Y_LIVE_SEC = self.Y_FAN_BOX + FAN_H + 14
        self.Y_LIVE_BOX = self.Y_LIVE_SEC + 18
        self.H = self.Y_LIVE_BOX + LIVE_H + 16
        avail = (self.work[3] - self.work[1]) * 0.97
        if self.H * self.k_dpi > avail:
            self.k = avail / self.H
        self.win_w = int(round(PANEL_W * self.k))
        self.win_h = int(round(self.H * self.k))
        cv.configure(width=self.win_w, height=self.win_h)

        self.f_title = self._font(FONT_DISPLAY, 24, True)
        self.f_card = self._font(FONT_DISPLAY, 21, True)
        self.f_mono9 = self._font(FONT_MONO, 9)
        self.f_mono10 = self._font(FONT_MONO, 10)
        self.f_mono10b = self._font(FONT_MONO, 10, True)
        self.f_mono11 = self._font(FONT_MONO, 11)
        self.f_rpm = self._font(FONT_DISPLAY, 36, True)
        self.f_num = self._font(FONT_DISPLAY, 23, True)
        self.f_seg = self._font(FONT_DISPLAY, 14, True)
        self.f_body_b = self._font(FONT_BODY, 11, True)
        self.f_sym = self._font("Segoe UI Symbol", 15)

        self._icon_imgs = {}
        px = max(8, round(28 * self.k))
        for mode, color, _t, _s in MODE_SPECS:
            self._icon_imgs[mode] = tuple(
                ImageTk.PhotoImage(icons.make_icon(mode, color, on).resize((px, px), Image.LANCZOS))
                for on in (True, False)
            )

        self._bg_img = self._make_bg()
        cv.create_image(0, 0, image=self._bg_img, anchor="nw", tags=("scan",))
        self._build_header()
        self._build_modes()
        self._build_fan()
        self._build_live()

        # borda neon esquerda + linhas de varredura (por baixo de tudo)
        self._rect(0, 0, 2, self.H, fill=self.accent, tags=("acc_fill", "edge"))
        cv.tag_raise("edge")

        cv.bind("<ButtonPress-1>", self._on_press)
        cv.bind("<B1-Motion>", self._on_drag)
        cv.bind("<ButtonRelease-1>", self._on_release)
        cv.bind("<Motion>", self._on_motion)
        cv.bind("<Leave>", lambda e: self._set_hover(None))

        self._section(self.Y_MODO, "MODO", "sec_modo")
        self._update_status()

    # ---- cabeçalho
    def _make_bg(self):
        """Fundo + linhas de varredura numa imagem só (1 item no canvas em vez
        de ~300: o desenho fica bem mais leve e a animação de abrir, fluida)."""
        img = Image.new("RGB", (self.win_w, self.win_h), C_BG)
        d = ImageDraw.Draw(img)
        step = max(2, round(4 * self.k))
        for yy in range(0, self.win_h, step):
            d.line([(0, yy), (self.win_w, yy)], fill=C_SCAN)
        return ImageTk.PhotoImage(img)

    def _build_header(self):
        _ids, w = self._spaced(32, 30, "NITRO", self.f_title, C_TEXT, sp=3)
        self._spaced(32 + w + 3, 30, "SWITCH", self.f_title, self.accent, sp=3, tags=("acc_fill",))
        self.dev_id = self._text(PANEL_W - 78, 31, sysmetrics.device_name(), self.f_mono9, C_DIM, anchor="e")
        # botão ATUALIZAR: fica oculto até a versão nova estar baixada e conferida
        bx0, bx1 = PANEL_W - 162, PANEL_W - 72
        self.upd_box = (bx0, 18, bx1, 43)
        self.upd_poly = self._poly([(bx0, 18), (bx1 - 7, 18), (bx1, 25), (bx1, 43), (bx0, 43)],
                                   fill=self.accent, tags=("upd", "acc_fill"))
        self.upd_txt = self._text((bx0 + bx1) / 2, 31, "ATUALIZAR", self.f_mono10b, C_BG, anchor="c", tags=("upd",))
        self.cv.itemconfigure("upd", state="hidden")
        self.gear_id = self._text(PANEL_W - 52, 30, "⚙", self.f_sym, C_MUT, anchor="c")
        cx, cy = PANEL_W - 24, 30
        self.close_ids = [
            self._line(cx - 5, cy - 5, cx + 5, cy + 5, C_MUT, width=1.6),
            self._line(cx - 5, cy + 5, cx + 5, cy - 5, C_MUT, width=1.6),
        ]
        self._hit(self.hits, PANEL_W - 66, 14, PANEL_W - 38, 46, self._open_settings,
                  hover=lambda on: self.cv.itemconfig(self.gear_id, fill=self.accent if on else C_MUT))
        self._hit(self.hits, PANEL_W - 38, 14, PANEL_W - 8, 46, self._hide_to_tray,
                  hover=lambda on: [self.cv.itemconfig(i, fill=self.accent if on else C_MUT) for i in self.close_ids])

    def _section(self, y, label, tag, name=None):
        """Rótulo de seção '// TEXTO ————' (name = destaque na cor do modo)."""
        self.cv.delete(tag)
        _ids, w = self._spaced(PAD, y, label, self.f_mono10, C_DIM, sp=2.2, tags=(tag,))
        end = PAD + w
        if name:
            _ids, w2 = self._spaced(end + 7, y, name, self.f_mono10b, self.accent, sp=2.2, tags=(tag, "acc_fill"))
            end += 7 + w2
        self._line(end + 10, y, PANEL_W - PAD, y, C_LINE, tags=(tag,))

    # ---- cartões de modo
    def _build_modes(self):
        self.card_y = {}
        self.card_polys = {}
        self.card_items = {}
        x0, x1, c = PAD, PANEL_W - PAD, 12
        for i, (mode, accent, title, sub) in enumerate(MODE_SPECS):
            y0 = self.Y_CARDS + i * (CARD_H + CARD_GAP)
            self.card_y[mode] = y0
            self.card_polys[mode] = self._poly(
                [(x0, y0), (x1 - c, y0), (x1, y0 + c), (x1, y0 + CARD_H), (x0, y0 + CARD_H)], outline=C_LINE,
            )
        # destaque deslizante: degradê em faixas + contorno + barra à esquerda
        n = 22
        bw = (x1 - x0) / n
        self.hl_bands = [
            self._rect(x0 + j * bw, 0, x0 + (j + 1) * bw + 1, CARD_H, fill=C_BG, tags=("hl",)) for j in range(n)
        ]
        self.hl_outline = self._poly(
            [(x0, 0), (x1 - c, 0), (x1, c), (x1, CARD_H), (x0, CARD_H)], outline=self.accent, width=1.3,
            tags=("hl", "hl_out"),
        )
        self.hl_bar = self._rect(x0, 0, x0 + 4, CARD_H, fill=self.accent, tags=("hl", "acc_fill"))
        self.cv.itemconfigure("hl", state="hidden")
        for i, (mode, accent, title, sub) in enumerate(MODE_SPECS):
            y0 = self.card_y[mode]
            mid = y0 + CARD_H / 2
            icon = self.cv.create_image(self._p(x0 + 34), self._p(mid), image=self._icon_imgs[mode][1])
            hz = power_modes.mode_hz(mode)
            sub = sub.format(hz=f"{hz} Hz · " if hz else "")
            t_ids, _w = self._spaced(x0 + 62, y0 + 20, title, self.f_card, C_MUT, sp=2.4)
            s_id = self._text(x0 + 62, y0 + 42, sub, self.f_mono10, C_DIM)
            self._rect(x1 - 98, mid - 10, x1 - 42, mid + 10, outline=accent, tags=(f"bd_{mode}",))
            bd_txt = self._text(x1 - 70, mid, "ATIVO", self.f_mono9, accent, anchor="c", tags=(f"bd_{mode}",))
            self.cv.itemconfigure(f"bd_{mode}", state="hidden")
            num = self._text(x1 - 18, mid, str(i + 1), self.f_mono10, C_DIM, anchor="c")
            self.card_items[mode] = {"icon": icon, "title": t_ids, "sub": s_id, "bd_txt": bd_txt, "num": num}
            self._hit(
                self.hits, x0, y0, x1, y0 + CARD_H, lambda m=mode: self._on_card_click(m),
                hover=lambda on, m=mode: self._card_hover(m, on),
            )
        # varredura de progresso enquanto aplica
        self._line(PAD, self.Y_PG, PANEL_W - PAD, self.Y_PG, C_LINE, width=2)
        self.pg_block = self._rect(PAD, self.Y_PG - 1, PAD + 80, self.Y_PG + 1, fill=self.accent,
                                   tags=("acc_fill", "pg"))
        self.cv.itemconfigure("pg", state="hidden")
        self._pg_running = False

    def _card_hover(self, mode, on):
        if self.mode_state[mode] == "idle":
            self.cv.itemconfig(self.card_polys[mode], fill=_mix(C_BG, "#ffffff", 0.05) if on else "")

    def _on_card_click(self, mode):
        if self._mode_click_enabled:
            self._apply_mode_async(mode)

    def _key_mode(self, mode):
        if self.winfo_viewable() and self._mode_click_enabled and not self.sheet_open:
            self._apply_mode_async(mode)

    def _set_hl_colors(self, accent):
        n = len(self.hl_bands)
        for j, b in enumerate(self.hl_bands):
            t = max(0.0, 1.0 - j / (n * 0.85)) * 0.22  # 22% do accent no começo, 0 aos 85%
            self.cv.itemconfig(b, fill=_mix(C_BG, accent, t))

    def _move_hl(self, mode, accent, animate=True):
        """Desliza o destaque até o cartão de `mode`, trocando a cor junto."""
        target = self._p(self.card_y[mode])
        first = self.hl_target is None
        self.hl_target = mode
        self.cv.itemconfigure("hl", state="normal")
        old_accent = self.accent
        if first:
            self.cv.move("hl", 0, target)
            self.hl_y = target
        y_from = self.hl_y

        def step(t):
            y = y_from + (target - y_from) * t
            self.cv.move("hl", 0, y - self.hl_y)
            self.hl_y = y
            col = _mix(old_accent, accent, t)
            self._set_accent(col)
            self._set_hl_colors(col)

        if animate:
            self._anim("hl", 420, step)
        else:
            step(1.0)

    def _set_accent(self, color):
        """Cor de destaque de TODA a interface (acompanha o modo)."""
        self.accent = color
        self.cv.itemconfig("acc_fill", fill=color)
        self.cv.itemconfig("hl_out", outline=color)
        self.cv.itemconfig("acc_out", outline=color)
        self._paint_fan_dynamic()

    # ---- ventoinha
    def _build_fan(self):
        self._section(self.Y_FAN_SEC, "VENTOINHA", "sec_fan")
        x0, x1 = PAD, PANEL_W - PAD
        y = self.Y_FAN_BOX
        self._rect(x0, y, x1, y + FAN_H, fill=C_SURF, outline=C_LINE)
        self.fan_title = self._text(x0 + 14, y + 12, "", self.f_mono9, C_DIM)
        self.rpm_text = self._text(x0 + 14, y + 38, "0", self.f_rpm, self.accent, tags=("acc_fill",))
        self.rpm_unit = self._text(x0 + 14 + 60, y + 46, "RPM", self.f_mono9, C_MUT)
        self.stripes = []
        bx0, bx1, by = x0 + 132, x1 - 14, y + 38
        n = int((bx1 - bx0) // 8)
        for j in range(n):
            xx = bx0 + j * 8
            self.stripes.append(self._rect(xx, by - 4, xx + 6, by + 4, fill=C_LINE))
        # modos da ventoinha (5)
        sy = y + 66
        self._rect(x0 + 14, sy, x1 - 14, sy + 36, fill=C_BG, outline=C_LINE)
        self.seg_cells = {}
        cw = (x1 - x0 - 28 - 6) / len(FAN_SEG)
        for j, (key, label) in enumerate(FAN_SEG):
            cx0 = x0 + 14 + 3 + j * cw
            cx1 = cx0 + cw
            poly = self._poly([(cx0, sy + 3), (cx1 - 7, sy + 3), (cx1, sy + 10), (cx1, sy + 33), (cx0, sy + 33)],
                              fill=C_BG)
            txt = self._text((cx0 + cx1) / 2, sy + 18, label, self.f_seg, C_MUT, anchor="c")
            self.seg_cells[key] = (poly, txt)
            self._hit(self.hits, cx0, sy + 3, cx1, sy + 33, lambda k=key: self._on_fan_mode_changed(k),
                      hover=lambda on, k=key: self._seg_hover(k, on))
        # teto de rotação (slider)
        ly = sy + 46
        self._rect(x0 + 14, ly, x1 - 14, ly + 62, fill=C_BG, outline=C_LINE)
        self.cap_label = self._text(x0 + 26, ly + 16, "LIMITE MÁXIMO", self.f_mono10, C_MUT)
        self.cap_value = self._text(x1 - 26, ly + 16, "", self.f_mono10b, C_MUT, anchor="e")
        self.sl_x0, self.sl_x1, self.sl_y = x0 + 32, x1 - 32, ly + 42
        self.sl_track = self._rect(self.sl_x0, self.sl_y - 2, self.sl_x1, self.sl_y + 2, fill=C_LINE)
        self.sl_fill = self._rect(self.sl_x0, self.sl_y - 2, self.sl_x0, self.sl_y + 2, fill=self.accent)
        self.sl_thumb = self._poly([(0, 0), (1, 0), (1, 1), (0, 1)], fill="#ffffff", outline=self.accent, width=1.6)
        self.sl_hit = self._hit(self.hits, self.sl_x0 - 12, ly + 28, self.sl_x1 + 12, ly + 58, None,
                                cursor="sb_h_double_arrow")
        # estado
        ty = ly + 72
        self._rect(x0 + 14, ty, x1 - 14, ty + 44, fill=C_BG)
        self._rect(x0 + 14, ty, x0 + 17, ty + 44, fill=self.accent, tags=("acc_fill",))
        self.state_text = self._text(x0 + 28, ty + 15, "Estado: —", self.f_mono11, C_TEXT)
        self.warn_text = self._text(x0 + 28, ty + 33, "", self.f_mono9, C_AMBER)

    def _seg_hover(self, key, on):
        if key != self._fan_mode_cache:
            self.cv.itemconfig(self.seg_cells[key][1], fill=C_TEXT if on else C_MUT)

    def _place_thumb(self, cap):
        frac = (cap - fan_control.MIN_CUSTOM_CAP) / (100 - fan_control.MIN_CUSTOM_CAP)
        x = self.sl_x0 + max(0.0, min(1.0, frac)) * (self.sl_x1 - self.sl_x0)
        self.cv.coords(self.sl_fill, self._p(self.sl_x0), self._p(self.sl_y - 2), self._p(x), self._p(self.sl_y + 2))
        w, h, sk = 12, 22, 4
        pts = [(x - w / 2 + sk, self.sl_y - h / 2), (x + w / 2 + sk, self.sl_y - h / 2),
               (x + w / 2 - sk, self.sl_y + h / 2), (x - w / 2 - sk, self.sl_y + h / 2)]
        self.cv.coords(self.sl_thumb, *[self._p(v) for pt in pts for v in pt])

    def _paint_fan_dynamic(self):
        """Partes da ventoinha que dependem da cor de destaque e do modo."""
        if not hasattr(self, "seg_cells"):
            return
        fmode = self._fan_mode_cache
        for key, (poly, txt) in self.seg_cells.items():
            on = key == fmode
            self.cv.itemconfig(poly, fill=self.accent if on else C_BG)
            self.cv.itemconfig(txt, fill=C_BG if on else C_MUT)
        custom = fmode == "personalizada"
        dim = _mix(C_BG, C_MUT, 0.45)
        self.cv.itemconfig(self.sl_fill, fill=self.accent if custom else dim)
        self.cv.itemconfig(self.sl_thumb, fill="#ffffff" if custom else dim, outline=self.accent if custom else C_LINE2)
        for j, s in enumerate(self.stripes):
            self.cv.itemconfig(s, fill=self.accent if j < self._fan_filled else C_LINE)

    # ---- ao vivo
    def _build_live(self):
        self._section(self.Y_LIVE_SEC, "AO VIVO", "sec_live")
        x0, x1, y = PAD, PANEL_W - PAD, self.Y_LIVE_BOX
        self._rect(x0, y, x1, y + LIVE_H, fill=C_SURF, outline=C_LINE)
        cw = (x1 - x0) / 4
        self.live_vals = {}
        for i, (key, label) in enumerate((("cpu", "CPU"), ("temp", "TEMP"), ("gpu", "GPU"), ("ram", "RAM"))):
            cx = x0 + i * cw
            if i:
                self._line(cx, y + 1, cx, y + 56, C_LINE)
            self._spaced(cx + 12, y + 14, label, self.f_mono9, C_MUT, sp=1.6)
            self.live_vals[key] = self._text(cx + 12, y + 38, "—", self.f_num, C_TEXT)
        self._line(x0 + 1, y + 56, x1 - 1, y + 56, C_LINE)
        self._text(x0 + 12, y + 67, "TEMP CPU · ÚLTIMOS 80 s", self.f_mono9, C_DIM)
        # bateria: percentual e tempo restante (vazio em PC sem bateria)
        self.batt_text = self._text(x1 - 12, y + 67, sysmetrics.battery_text(), self.f_mono9, C_TEXT, anchor="e")
        self.spark = self.cv.create_line(0, 0, 0, 0, fill=self.accent, width=max(1, round(1.6 * self.k)))
        self.spark_area = (x0 + 12, y + 74, x1 - 12, y + LIVE_H - 8)
        self.temp_hist = []

    def _draw_spark(self):
        hist = self.temp_hist[-40:]
        if len(hist) < 2:
            return
        ax0, ay0, ax1, ay1 = self.spark_area
        pts = []
        for i, v in enumerate(hist):
            x = ax0 + i * (ax1 - ax0) / 39
            f = max(0.0, min(1.0, (v - 30) / 70.0))
            pts += [self._p(x), self._p(ay1 - f * (ay1 - ay0))]
        self.cv.coords(self.spark, *pts)
        self.cv.itemconfig(self.spark, fill=self.accent)

    # ------------------------------------------------------------------
    # Mouse
    # ------------------------------------------------------------------
    def _find_hit(self, x, y):
        hits = self.sheet_hits if self.sheet_open else self.hits
        for h in hits:
            x0, y0, x1, y1 = h["r"]
            if x0 <= x <= x1 and y0 <= y <= y1:
                return h
        return None

    def _set_hover(self, h):
        if h is self._hover:
            return
        if self._hover is not None and self._hover["hover"]:
            self._hover["hover"](False)
        self._hover = h
        if h is not None and h["hover"]:
            h["hover"](True)
        self.cv.configure(cursor=h["cursor"] if h is not None else "")

    def _on_motion(self, e):
        self._set_hover(self._find_hit(e.x, e.y))

    def _on_press(self, e):
        h = self._find_hit(e.x, e.y)
        if h is None:
            return
        if h is self.sl_hit:
            if self._fan_mode_cache == "personalizada":
                self.cap_dragging = True
                self._drag_cap(e.x)
            return
        if h["cb"]:
            h["cb"]()

    def _on_drag(self, e):
        if self.cap_dragging:
            self._drag_cap(e.x)

    def _on_release(self, e):
        if self.cap_dragging:
            self.cap_dragging = False
            config.set_fan_custom_cap(self.cfg["fan_custom_cap"])

    def _drag_cap(self, x):
        frac = max(0.0, min(1.0, (x / self.k - self.sl_x0) / (self.sl_x1 - self.sl_x0)))
        raw = fan_control.MIN_CUSTOM_CAP + frac * (100 - fan_control.MIN_CUSTOM_CAP)
        cap = max(fan_control.MIN_CUSTOM_CAP, min(100, int(round(raw / 5.0) * 5)))
        if cap != self.cfg.get("fan_custom_cap"):
            self.cfg["fan_custom_cap"] = cap
            self.fan_ctrl.set_mode("personalizada", cap)
        self._place_thumb(cap)
        self.cv.itemconfig(self.cap_value, text=f"{cap}%")

    # ------------------------------------------------------------------
    # Métricas ao vivo
    # ------------------------------------------------------------------
    def _metrics_tick(self):
        try:
            if self.winfo_viewable():
                self.cv.itemconfig(self.live_vals["cpu"], text=f"{sysmetrics.cpu_percent():.0f}%")
                used, _total, _pct = sysmetrics.memory()
                self.cv.itemconfig(self.live_vals["ram"], text=f"{used:.1f}G")
                self.cv.itemconfig(self.batt_text, text=sysmetrics.battery_text())
        except Exception:
            pass
        self.after(2000, self._metrics_tick)

    # ------------------------------------------------------------------
    # Ventoinha (lógica)
    # ------------------------------------------------------------------
    def _fan_target_mode(self):
        """Modo do app cuja config de ventoinha está sendo editada/aplicada:
        o ativo, ou o modo padrão enquanto nenhum foi aplicado ainda."""
        return self.current_mode or self.cfg.get("default_mode", "eco")

    def _sync_fan_ui(self):
        m = self._fan_target_mode()
        fmode = self.cfg.get("fan_mode", {}).get(m, "auto")
        if fmode not in fan_control.FAN_MODES or self._no_fan:
            fmode = "auto"  # notebook sem controle de ventoinha: sempre com o firmware
        self._fan_mode_cache = fmode
        cap = int(self.cfg.get("fan_custom_cap", 70))
        self.cv.itemconfig(self.fan_title, text=f"NO MODO {MODE_LABELS[m]}")
        custom = fmode == "personalizada"
        fixed = fan_control.FAN_MODES[fmode]
        self.cv.itemconfig(self.cap_label, fill=C_MUT if custom else C_DIM)
        self.cv.itemconfig(self.cap_value, fill=C_TEXT if custom else C_DIM,
                           text=f"{cap}%" if custom else ("AUTO" if fixed is None else f"{fixed}%"))
        self.cv.itemconfig(self.sl_track, fill=C_LINE2 if custom else C_LINE)
        self._place_thumb(cap if custom else (fixed if fixed else cap))
        self._paint_fan_dynamic()
        self.fan_ctrl.set_mode(fmode, cap)

    def _on_fan_mode_changed(self, key):
        if self._no_fan:
            return  # este notebook não tem controle de ventoinha
        m = self._fan_target_mode()
        config.set_fan_mode(m, key)
        self.cfg.setdefault("fan_mode", {})[m] = key
        self._sync_fan_ui()

    def _on_fan_status(self, st):
        if st.get("state") == "sem_controle" and not self._no_fan:
            self._no_fan = True
            self._sync_fan_ui()
        rpm = st.get("cpu_rpm")
        self.cv.itemconfig(self.rpm_text, text=f"{rpm}" if rpm is not None else "—")
        bb = self.cv.bbox(self.rpm_text)
        if bb:  # a unidade acompanha a largura do número
            self.cv.coords(self.rpm_unit, bb[2] + self._p(6), self._p(self.Y_FAN_BOX + 46))
        self._fan_filled = int(round(min(1.0, (rpm or 0) / FAN_MAX_RPM) * len(self.stripes)))
        for j, s in enumerate(self.stripes):
            self.cv.itemconfig(s, fill=self.accent if j < self._fan_filled else C_LINE)
        ct, gt = st.get("cpu_temp"), st.get("gpu_temp")
        self.cv.itemconfig(self.live_vals["temp"], text=f"{ct}°" if ct else "—", fill=_heat(ct))
        self.cv.itemconfig(self.live_vals["gpu"], text=f"{gt}°" if gt else "—", fill=_heat(gt))
        if ct:
            self.temp_hist.append(ct)
            self.temp_hist = self.temp_hist[-40:]
            self._draw_spark()
        nomes = {
            "auto": "Firmware no controle",
            "manual": f"Manual em {st.get('speed')}%",
            "limite_ignorado": "Limite ignorado (100%)",
            "seguranca": "Devolvida ao automático",
            "indisponivel": "Indisponível",
            "sem_controle": "Firmware no controle",
        }
        self.cv.itemconfig(self.state_text, text=nomes.get(st.get("state"), "—"))
        self.cv.itemconfig(self.warn_text, text=st.get("warning") or "")

    # ------------------------------------------------------------------
    # Status (linha sob o título)
    # ------------------------------------------------------------------
    def _update_status(self, text=None, color=None):
        if text is not None:
            self._status_cache = (text, color or C_MUT)
        text, color = self._status_cache
        self.cv.delete("status")
        self._rect(PAD + 1, self.Y_STATUS - 3, PAD + 7, self.Y_STATUS + 3, fill=self._status_dot, tags=("status",))
        self._text(PAD + 16, self.Y_STATUS, text, self.f_mono10, color, tags=("status",))
        if self._ns_text:
            self._text(PANEL_W - PAD, self.Y_STATUS, self._ns_text, self.f_mono9, C_DIM, anchor="e", tags=("status",))

    def _ns_from(self, profile):
        if profile is None:
            if power_modes.ACER_STATE in (None, "ausente"):
                return ""  # não é Acer: o notebook não tem perfil térmico para mostrar
            return "NitroSense: sem acesso (admin)"
        return f"NitroSense: {power_modes.NITROSENSE_PROFILE_NAMES.get(profile, f'0x{profile:02X}')}"

    def _show_nitrosense_profile(self, profile):
        self._ns_text = self._ns_from(profile)
        self._update_status()

    def _read_nitrosense_profile_async(self):
        def worker():
            profile = power_modes.get_nitrosense_profile()
            self._ui_call(lambda: self._show_nitrosense_profile(profile))

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------
    # Ajustes (folha por cima, criada na 1ª abertura)
    # ------------------------------------------------------------------
    def _open_settings(self):
        if self.sheet is None:
            self._build_settings()
        self.sheet_open = True
        self._set_hover(None)
        self.cv.itemconfigure("sheet", state="normal")
        self.cv.tag_raise("sheet")
        off = self.win_w
        self.cv.move("sheet", off, 0)
        self._sheet_x = off

        def step(t):
            nx = off * (1 - t)
            self.cv.move("sheet", nx - self._sheet_x, 0)
            self._sheet_x = nx

        self._anim("sheet", 320, step, ease=_ease_out)

    def _close_settings(self):
        if self.sheet is None or not self.sheet_open:
            return
        self.sheet_open = False
        self._set_hover(None)
        off = self.win_w

        def step(t):
            nx = off * t
            self.cv.move("sheet", nx - self._sheet_x, 0)
            self._sheet_x = nx

        self._anim("sheet", 260, step, ease=_ease_out,
                   done=lambda: self.cv.itemconfigure("sheet", state="hidden") if not self.sheet_open else None)

    def _section_sheet(self, y, label):
        _ids, w = self._spaced(PAD, y, label, self.f_mono10, C_DIM, sp=2.2, tags=("sheet",))
        self._line(PAD + w + 10, y, PANEL_W - PAD, y, C_LINE, tags=("sheet",))

    def _build_settings(self):
        T = ("sheet",)
        self.sheet = True
        self._sheet_x = 0
        self.cv.create_image(0, 0, image=self._bg_img, anchor="nw", tags=T)
        self._rect(0, 0, 2, self.H, fill=self.accent, tags=T + ("acc_fill",))
        self.back_id = self._text(34, 30, "‹ VOLTAR", self.f_seg, C_MUT, tags=T)
        self._spaced(PANEL_W - PAD, 30, "AJUSTES", self.f_title, self.accent, sp=3, anchor="e",
                     tags=T + ("acc_fill",))
        self._hit(self.sheet_hits, 20, 14, 130, 46, self._close_settings,
                  hover=lambda on: self.cv.itemconfig(self.back_id, fill=C_TEXT if on else C_MUT))

        y = 70
        self._section_sheet(y, "MODO PADRÃO AO LIGAR O PC")
        y += 16
        self.def_cells = {}
        cw = (PANEL_W - 2 * PAD - 18) / 4
        for i, (mode, color, _t, _s) in enumerate(reversed(MODE_SPECS)):
            cx0 = PAD + i * (cw + 6)
            poly = self._poly([(cx0, y), (cx0 + cw - 8, y), (cx0 + cw, y + 8), (cx0 + cw, y + 38), (cx0, y + 38)],
                              outline=C_LINE, tags=T)
            txt = self._text(cx0 + cw / 2, y + 19, MODE_LABELS_UI[mode].upper(), self.f_mono9, C_MUT,
                             anchor="c", tags=T)
            self.def_cells[mode] = (poly, txt, color)
            self._hit(self.sheet_hits, cx0, y, cx0 + cw, y + 38, lambda m=mode: self._on_default_mode_changed(m))
        self._paint_default_cells()
        y += 62
        self.toggle_ids = {}
        for key, label, sub in (("apply_on_startup", "Aplicar automaticamente ao iniciar", "Usa o modo padrão acima"),
                                ("startup", "Iniciar com o Windows", "Abre na bandeja ao entrar no Windows")):
            self._line(PAD, y - 6, PANEL_W - PAD, y - 6, C_LINE, tags=T)
            self._text(PAD, y + 10, label, self.f_body_b, C_TEXT, tags=T)
            self._text(PAD, y + 28, sub, self.f_mono9, C_DIM, tags=T)
            tx1 = PANEL_W - PAD
            track = self._poly([(tx1 - 46, y + 8), (tx1 - 6, y + 8), (tx1, y + 14), (tx1, y + 30), (tx1 - 46, y + 30)],
                               fill=C_LINE, outline=C_LINE2, tags=T)
            knob = self._rect(tx1 - 42, y + 12, tx1 - 30, y + 26, fill=C_MUT, tags=T)
            self.toggle_ids[key] = (track, knob, tx1, y + 12)
            self._hit(self.sheet_hits, tx1 - 50, y + 4, tx1 + 4, y + 34, lambda k=key: self._on_toggle(k))
            y += 54
        self._paint_toggle("apply_on_startup", bool(self.cfg.get("apply_on_startup")))
        threading.Thread(target=self._read_startup_async, daemon=True).start()

        self._section_sheet(y + 4, "ATALHO DO TECLADO")
        y += 24
        self._rect(PAD, y, PANEL_W - PAD - 110, y + 36, outline=C_LINE2, tags=T)
        self.hk_text = self._text(PAD + 12, y + 18, hotkey.describe(self.cfg.get("hotkey")), self.f_mono10,
                                  C_TEXT, tags=T)
        bx0 = PANEL_W - PAD - 100
        self.hk_btn_poly = self._poly(
            [(bx0, y), (bx0 + 92, y), (bx0 + 100, y + 8), (bx0 + 100, y + 36), (bx0, y + 36)],
            outline=self.accent, tags=T + ("acc_out",))
        self.hk_btn_txt = self._text(bx0 + 50, y + 18, "APRENDER", self.f_mono10b, self.accent, anchor="c",
                                     tags=T + ("acc_fill",))
        self._hit(self.sheet_hits, bx0, y, bx0 + 100, y + 36, self._learn_hotkey,
                  hover=lambda on: self.cv.itemconfig(self.hk_btn_poly, fill=_mix(C_BG, self.accent, 0.2) if on else ""))
        y += 56
        self._section_sheet(y, "VENTOINHA · USO SEGURO")
        y += 14
        lines = FAN_SAFETY_LINES
        if power_modes.ACER_STATE == "ausente":
            lines = ["• Este notebook não expõe o controle da ventoinha:",
                     "  ela fica por conta do firmware.",
                     "• Os modos de energia funcionam normalmente."]
        for i, line in enumerate(lines):
            self._text(PAD, y + 8 + i * 17, line, self.f_mono9, C_MUT, tags=T)
        self._text(PAD, self.H - 18, f"NITROSWITCH v{__version__} · atualiza sozinho", self.f_mono9, C_DIM, tags=T)
        self.cv.itemconfigure("sheet", state="hidden")

    def _paint_default_cells(self):
        for mode, (poly, txt, color) in self.def_cells.items():
            on = mode == self.cfg.get("default_mode")
            self.cv.itemconfig(poly, fill=_mix(C_BG, color, 0.18) if on else "", outline=color if on else C_LINE)
            self.cv.itemconfig(txt, fill=color if on else C_MUT)

    def _paint_toggle(self, key, on):
        track, knob, tx1, ky = self.toggle_ids[key]
        self.cv.itemconfig(track, fill=_mix(C_BG, self.accent, 0.35) if on else C_LINE,
                           outline=self.accent if on else C_LINE2)
        x0 = tx1 - 18 if on else tx1 - 42
        ox = self._sheet_x  # a folha pode estar no meio da animação de entrada
        self.cv.coords(knob, self._p(x0) + ox, self._p(ky), self._p(x0 + 12) + ox, self._p(ky + 14))
        self.cv.itemconfig(knob, fill=self.accent if on else C_MUT)

    def _on_toggle(self, key):
        if key == "apply_on_startup":
            new = not bool(self.cfg.get("apply_on_startup"))
            config.set_apply_on_startup(new)
            self.cfg["apply_on_startup"] = new
            self._paint_toggle(key, new)
        else:
            self._toggle_startup()

    def _read_startup_async(self):
        enabled = startup.is_startup_enabled()
        self._ui_call(lambda: self._paint_toggle("startup", enabled))

    def _toggle_startup(self):
        # schtasks leva uns décimos de segundo: fora da thread da UI; o botão
        # reflete o estado REAL ao terminar (ver CLAUDE.md, bug 4 do QA).
        def worker():
            real = startup.toggle_startup()
            self._ui_call(lambda: self._paint_toggle("startup", real))

        threading.Thread(target=worker, daemon=True).start()

    def _on_default_mode_changed(self, mode):
        config.set_default_mode(mode)
        self.cfg["default_mode"] = mode
        self._paint_default_cells()
        if self.current_mode is None:
            self._sync_fan_ui()

    # ------------------------------------------------------------------
    def _learn_hotkey(self):
        self._learning_hotkey = True
        self.cv.itemconfig(self.hk_text, text="Pressione a tecla… (12 s)")
        self.cv.itemconfig(self.hk_btn_txt, text="AGUARDANDO")
        self.hotkey.learn()

    def _on_hotkey_learned(self, binding):
        self._learning_hotkey = False
        if binding is not None:
            config.set_hotkey(binding)
            self.cfg["hotkey"] = binding
        if self.sheet is None:
            return
        self.cv.itemconfig(self.hk_btn_txt, text="APRENDER")
        if binding is None:
            self.cv.itemconfig(self.hk_text, text=hotkey.describe(self.cfg.get("hotkey")) + " (nada detectado)")
        else:
            self.cv.itemconfig(self.hk_text, text=hotkey.describe(binding))

    def _toggle_popup(self):
        if self.winfo_viewable():
            self._hide_to_tray()
            return
        # O Windows só deixa um processo trazer janela para frente se ele
        # recebeu o último input: um toque sintético de Alt satisfaz a regra
        # (o hook ignora eventos injetados).
        ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
        ctypes.windll.user32.keybd_event(0x12, 0, 0x0002, 0)
        self.show_popup()

    # ------------------------------------------------------------------
    # Troca de modo
    # ------------------------------------------------------------------
    def _set_card_state(self, mode, state):
        if self.mode_state[mode] == state:
            return
        self.mode_state[mode] = state
        on = state != "idle"
        it = self.card_items[mode]
        self.cv.itemconfig(it["icon"], image=self._icon_imgs[mode][0 if on else 1])
        for i in it["title"]:
            self.cv.itemconfig(i, fill=C_TEXT if on else C_MUT)
        self.cv.itemconfig(it["sub"], fill=C_MUT if on else C_DIM)
        self.cv.itemconfig(it["num"], fill=C_MUT if on else C_DIM)
        self.cv.itemconfig(self.card_polys[mode], fill="", outline=C_BG if on else C_LINE)
        if state == "idle":
            self.cv.itemconfigure(f"bd_{mode}", state="hidden")
        else:
            self.cv.itemconfigure(f"bd_{mode}", state="normal")
            self.cv.itemconfig(it["bd_txt"], text="APLICANDO" if state == "applying" else "ATIVO")

    def _pg_loop(self):
        if not self._pg_running:
            return
        t = (time.perf_counter() % 1.2) / 1.2
        span = (PANEL_W - 2 * PAD) + 80
        x = PAD - 80 + span * _ease(t)
        x0, x1 = max(PAD, x), min(PANEL_W - PAD, x + 80)
        y = self.Y_PG
        if x1 > x0:
            self.cv.coords(self.pg_block, self._p(x0), self._p(y - 1), self._p(x1), self._p(y + 1))
        else:
            self.cv.coords(self.pg_block, 0, 0, 0, 0)
        self.after(16, self._pg_loop)

    def _pg_start(self):
        if self._pg_running:
            return
        self._pg_running = True
        self.cv.itemconfigure("pg", state="normal")
        self._pg_loop()

    def _pg_stop(self):
        self._pg_running = False
        self.cv.itemconfigure("pg", state="hidden")

    def _begin_applying(self, mode):
        self._mode_click_enabled = False
        for m in self.mode_state:
            self._set_card_state(m, "applying" if m == mode else "idle")
        self._move_hl(mode, MODE_ACCENT[mode], animate=bool(self.winfo_viewable()))
        self._section(self.Y_MODO, "MODO", "sec_modo", name=MODE_LABELS[mode])
        self._status_dot = "#f5c542"
        self._update_status(f"APLICANDO {MODE_LABELS[mode]}…", C_MUT)
        self._pg_start()

    def _apply_mode_async(self, mode: str):
        self._begin_applying(mode)

        def worker():
            results = power_modes.apply_mode(mode)
            self._ui_call(lambda: self._on_mode_applied(mode, results))

        threading.Thread(target=worker, daemon=True).start()

    def adopt_mode(self, mode: str, results: dict):
        """Mostra na UI um modo que já foi aplicado por fora (o modo padrão da
        inicialização roda em paralelo à construção da janela - ver main.py)."""
        if self.hl_target != mode:
            self._begin_applying(mode)
        self._on_mode_applied(mode, results)

    def _on_mode_applied(self, mode: str, results: dict):
        self.current_mode = mode
        self._mode_click_enabled = True
        self._pg_stop()
        for m in self.mode_state:
            self._set_card_state(m, "active" if m == mode else "idle")
        self._section(self.Y_MODO, "MODO", "sec_modo", name=MODE_LABELS[mode])
        self._status_dot = MODE_ACCENT[mode]
        # falhas reais aparecem na tela (o "modo de energia" do Windows é
        # ignorado: não existe nesta build, ver CLAUDE.md)
        # (None = não se aplica a este notebook, não é falha)
        nomes = {"power_plan": "plano de energia", "power_settings": "ajustes de CPU",
                 "refresh_rate": "taxa da tela", "nitrosense": "perfil térmico"}
        failed = [nomes.get(k, k) for k, v in results.items()
                  if k not in ("nitrosense_profile", "power_overlay") and v is False]
        self._ns_text = self._ns_from(results.get("nitrosense_profile"))
        if failed:
            self._update_status(f"{MODE_LABELS[mode]} PARCIAL · falhou: {', '.join(failed)}", C_AMBER)
        else:
            self._update_status(f"MODO ATIVO: {MODE_LABELS[mode]}", C_TEXT)
        self._sync_fan_ui()
        if self.on_mode_changed:
            self.on_mode_changed(mode)

    # ------------------------------------------------------------------
    # Atualização (ver updater.py): o botão só existe quando basta um clique
    # ------------------------------------------------------------------
    def _on_update_ready(self, version):
        self.cv.itemconfigure(self.dev_id, state="hidden")
        self.cv.itemconfigure("upd", state="normal")
        self.cv.tag_raise("upd")
        if self.sheet is not None:
            self.cv.tag_raise("sheet")
        x0, y0, x1, y1 = self.upd_box
        self._hit(self.hits, x0, y0, x1, y1, self.install_update,
                  hover=lambda on: self.cv.itemconfig(
                      self.upd_poly, fill=_mix(self.accent, "#ffffff", 0.3) if on else self.accent))
        if self.on_update_ready:
            self.on_update_ready(version)

    def install_update(self):
        """Clique em ATUALIZAR (painel ou bandeja): dispara o instalador já
        baixado e encerra o app; o instalador o reabre na versão nova."""
        if self.updating or not self.updater.ready:
            return
        self.updating = True
        self._mode_click_enabled = False
        self.cv.itemconfig(self.upd_txt, text="AGUARDE…")
        self._status_dot = "#f5c542"
        self._update_status(f"ATUALIZANDO PARA v{self.updater.ready[0]}…", C_TEXT)
        self._pg_start()
        self.update_idletasks()

        def go():
            if self.updater.install():
                self.destroy()  # main.py devolve a ventoinha ao firmware na saída
                return
            self.updating = False
            self._mode_click_enabled = True
            self._pg_stop()
            self.cv.itemconfig(self.upd_txt, text="ATUALIZAR")
            self._update_status("NÃO DEU PARA ATUALIZAR · tente de novo", C_AMBER)

        self.after(150, go)

    # ------------------------------------------------------------------
    # Abrir / fechar com animação (desliza da/para a borda direita + fade)
    # ------------------------------------------------------------------
    SLIDE_PX = 70   # deslocamento lógico da animação
    OPEN_MS = 260
    CLOSE_MS = 200

    def _slide_to(self, t_open):
        """Posiciona a janela para `t_open` em [0,1] (0 = fora/transparente,
        1 = encostada na borda, opaca). SetWindowPos direto: bem mais barato
        que `geometry` (que passa pela fila de eventos do Tk)."""
        w, h, x, y = self._last_geom
        off = int(self.SLIDE_PX * self.k)
        nx = int(x + off * (1 - t_open))
        # SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
        ctypes.windll.user32.SetWindowPos(self._hwnd_cache, 0, nx, y, 0, 0, 0x0001 | 0x0004 | 0x0010)
        self._cur_open = t_open
        self.attributes("-alpha", max(0.0, min(1.0, t_open)))

    def _hide_to_tray(self):
        if not self.winfo_viewable() or self._closing:
            return
        self._closing = True
        self._cur_open = 1.0

        def step(t):  # t: 0 -> 1 durante o fechamento
            self._slide_to(1 - t)

        def done():
            self._closing = False
            self.withdraw()
            self.attributes("-alpha", 1.0)
            self._slide_to(1.0)
            if self.on_close_to_tray:
                self.on_close_to_tray()

        self._anim("window", self.CLOSE_MS, step, done=done, ease=lambda t: t * t * (3 - 2 * t))

    def show_popup(self):
        self._just_shown = True
        self._closing = False
        if self.winfo_viewable():
            # já aberta (ou fechando): só volta para a posição final
            self._anim("window", 140, lambda t: self._slide_to(max(self._cur_open, t)), ease=_ease_out)
        else:
            self.attributes("-alpha", 0.0)
            self._slide_to(0.0)
            self.deiconify()
            self.lift()
            self.focus_force()
            # deixa o conteúdo ser pintado ENQUANTO transparente e só então anima
            # (o 1º quadro caro não vira engasgo visível)
            self.update_idletasks()
            self.after(30, lambda: self._anim("window", self.OPEN_MS, self._slide_to, ease=_ease_out))
        self.after(400, lambda: setattr(self, "_just_shown", False))

    def apply_mode_from_tray(self, mode: str):
        if self._mode_click_enabled:
            self._apply_mode_async(mode)
