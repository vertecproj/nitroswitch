"""
icons.py
--------
Ícones de linha dos modos, desenhados em runtime com PIL (sem emoji, sem
arquivo de imagem). Supersampling 4x + filtro de máximo/mínimo para contorno
liso. Cada ícone existe em duas versões: cheia (modo selecionado) e apagada.
"""

import math

from PIL import Image, ImageChops, ImageDraw, ImageFilter

SS = 4            # supersampling
BASE = 24         # grade de desenho (como um viewBox 24x24)
PX = 28 * SS      # tamanho final em pixels antes de reduzir (CTkImage reescala)
STROKE = 2        # espessura em unidades da grade


def _s(v):
    return v * PX / BASE


def _outline(mask, k):
    """Contorno de uma máscara cheia: dilatada menos erodida."""
    size = 2 * k + 1
    return ImageChops.subtract(mask.filter(ImageFilter.MaxFilter(size)),
                               mask.filter(ImageFilter.MinFilter(size)))


def _bezier(p0, p1, p2, p3, n=24):
    pts = []
    for i in range(n + 1):
        t = i / n
        a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t ** 2, t ** 3
        pts.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
                    a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))
    return pts


def _mask_poly(points):
    m = Image.new("L", (PX, PX), 0)
    ImageDraw.Draw(m).polygon([(_s(x), _s(y)) for x, y in points], fill=255)
    return m


def _shape_mask(kind):
    k = int(_s(STROKE) / 2)
    if kind == "supereco":      # lua: círculo menos círculo deslocado
        a = Image.new("L", (PX, PX), 0)
        b = Image.new("L", (PX, PX), 0)
        ImageDraw.Draw(a).ellipse([_s(4), _s(4), _s(20), _s(20)], fill=255)
        ImageDraw.Draw(b).ellipse([_s(9.5), _s(1.5), _s(24.5), _s(16.5)], fill=255)
        return _outline(ImageChops.subtract(a, b), k)
    if kind == "eco":           # folha: duas curvas + nervura
        up = _bezier((5, 19), (3, 8), (11, 4), (19, 5))
        down = _bezier((19, 5), (21, 14), (13, 20), (5, 19))
        out = _outline(_mask_poly(up + down), k)
        d = ImageDraw.Draw(out)
        w = int(_s(STROKE))
        d.line([(_s(5), _s(19)), (_s(14), _s(10))], fill=255, width=w)
        return out
    if kind == "balanced":      # velocímetro: semicírculo + ponteiro
        m = Image.new("L", (PX, PX), 0)
        d = ImageDraw.Draw(m)
        w = int(_s(STROKE))
        d.arc([_s(3.5), _s(8.5), _s(20.5), _s(25.5)], 180, 360, fill=255, width=w)
        d.line([(_s(12), _s(17)), (_s(16), _s(11.5))], fill=255, width=w)
        r = _s(1.6)
        d.ellipse([_s(12) - r, _s(17) - r, _s(12) + r, _s(17) + r], fill=255)
        return m
    # boost: raio
    pts = [(13, 2), (4, 14), (11, 14), (10, 22), (19, 10), (12, 10)]
    return _outline(_mask_poly(pts), k)


def _tint(mask, color, alpha=1.0):
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    img = Image.new("RGBA", (PX, PX), (r, g, b, 0))
    if alpha < 1.0:
        mask = mask.point(lambda v: int(v * alpha))
    img.putalpha(mask)
    return img


def app_icon(color="#ffd479", size=256):
    """Ícone do app/bandeja: quadrado escuro arredondado com contorno neon e
    um raio preenchido na cor do modo."""
    ss = 4
    px = size * ss
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = int(px * 0.22)
    inset = int(px * 0.04)
    d.rounded_rectangle([inset, inset, px - inset, px - inset], radius=r, fill="#0b0b10",
                        outline=color, width=max(2, int(px * 0.045)))
    # raio (grade 24: x 4..19, y 2..22) centrado e ocupando ~62% do ícone
    pts = [(13, 2), (4, 14), (11, 14), (10, 22), (19, 10), (12, 10)]
    s = px * 0.62 / 20.0
    ox = px / 2 - 11.5 * s
    oy = px / 2 - 12.0 * s
    d.polygon([(ox + x * s, oy + y * s) for x, y in pts], fill=color)
    return img.resize((size, size), Image.LANCZOS)


def make_icon(kind, color, active):
    """Imagem PIL RGBA do ícone: cheia (active) ou apagada."""
    return _tint(_shape_mask(kind), color, 1.0 if active else 0.5)
