"""Draws the image cards the leaks bot posts: one tall picture of every new item.

Discord opens an image full-size on click, so a skin can be zoomed into without
downloading anything. The card is an HTML page screenshotted by headless Chrome
(preinstalled on GitHub's ubuntu runners), because Chrome shapes Arabic properly
and Pillow without raqm does not.
"""
import html
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

FONTS = Path(__file__).resolve().parent / "fonts"
UA = {"User-Agent": "Mozilla/5.0 (compatible; abod-leaks-bot/1.3)"}
VBUCK = "https://fortnite-api.com/images/vbuck.png"
COLS, TILE_W, TILE_H, GAP, PAD = 5, 300, 400, 18, 48
WIDTH = PAD * 2 + COLS * TILE_W + (COLS - 1) * GAP
FIXED = 40 + 190 + 24 + 6 + 28 + 70 + 20          # card height minus the grid (see the CSS below)

CSS = """
@font-face{font-family:Changa;src:url('{{CHANGA}}');font-weight:200 800}
@font-face{font-family:Tajawal;src:url('{{TAJ7}}');font-weight:700}
@font-face{font-family:Tajawal;src:url('{{TAJ8}}');font-weight:800}
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:{{W}}px;height:{{H}}px;overflow:hidden;background:#0b0620}
.card{position:relative;width:{{W}}px;height:{{H}}px;padding:40px {{PAD}}px 20px;color:#fff;
  font-family:Tajawal,Tahoma,sans-serif;overflow:hidden;
  background:radial-gradient(900px 520px at 85% -8%,rgba(124,58,237,.6),transparent 70%),
             radial-gradient(900px 620px at -5% 104%,rgba(34,211,238,.28),transparent 70%),
             linear-gradient(180deg,#170839 0%,#0a1e3a 100%)}
.hex{position:absolute;inset:0;opacity:.9;background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='84' height='97'><path d='M42 2 L82 25 L82 72 L42 95 L2 72 L2 25 Z' fill='none' stroke='rgba(255,255,255,0.055)' stroke-width='1.4'/></svg>")}
header{position:relative;height:190px;display:flex;align-items:center;justify-content:space-between}
.title{font-family:Changa;font-weight:800;font-size:100px;line-height:1.05;
  background:linear-gradient(180deg,#a5f3fc 0%,#22d3ee 42%,#8b5cf6 100%);-webkit-background-clip:text;color:transparent;
  filter:drop-shadow(0 6px 0 #120a30) drop-shadow(0 0 22px rgba(34,211,238,.45))}
.sub{font-weight:800;font-size:40px;color:#ece9ff;margin-top:4px}
.sub i{font-style:normal;color:#22d3ee;margin:0 12px}
.brand{display:flex;align-items:center;gap:20px}
.brand img{width:140px;height:140px;border-radius:50%;border:6px solid #22d3ee;box-shadow:0 0 26px rgba(34,211,238,.65)}
.brand .n{font-family:Changa;font-weight:800;font-size:54px;line-height:1.1}
.brand .h{font-weight:800;font-size:30px;color:#22d3ee;direction:ltr;text-align:right}
.rule{position:relative;height:6px;margin:24px 0 28px;border-radius:3px;background:linear-gradient(90deg,#8b5cf6,#22d3ee)}
.grid{position:relative;display:grid;grid-template-columns:repeat({{COLS}},{{TW}}px);gap:{{GAP}}px}
.tile{position:relative;width:{{TW}}px;height:{{TH}}px;border-radius:22px;overflow:hidden;
  background:linear-gradient(180deg,var(--a),var(--b));box-shadow:0 10px 26px rgba(0,0,0,.4)}
.art{position:absolute;left:0;right:0;top:0;height:300px;display:flex;align-items:flex-end;justify-content:center}
.art:before{content:'';position:absolute;left:50px;right:50px;top:36px;bottom:6px;border-radius:50%;
  background:rgba(255,255,255,.34);filter:blur(34px)}
.art img{position:relative;max-width:292px;max-height:292px}
.badge{position:absolute;top:12px;right:12px;padding:3px 14px 5px;border-radius:15px;background:rgba(9,5,26,.72);
  font-weight:800;font-size:19px}
.bar{position:absolute;left:0;right:0;bottom:0;height:100px;padding:0 14px;background:rgba(9,5,26,.93);
  border-top:3px solid var(--edge);display:flex;flex-direction:column;align-items:center;justify-content:center;gap:4px}
.name{max-width:100%;font-weight:800;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.price{display:flex;align-items:center;gap:8px;direction:ltr;font-weight:800;font-size:30px}
.price img{width:30px;height:30px}
.price s{font-size:22px;color:#a5a5be;text-decoration-color:#f45260;text-decoration-thickness:3px}
.line{max-width:100%;font-weight:700;font-size:22px;color:#cfcfe8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
footer{position:relative;height:70px;margin-top:0;display:flex;align-items:flex-end;justify-content:center;
  font-weight:700;font-size:28px;color:#bebae1}
"""


def hex_rgb(value, fallback=(139, 92, 246)):
    try:
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
    except (TypeError, ValueError, IndexError):
        return fallback


def shade(rgb, factor):
    """factor > 1 lightens towards white, < 1 darkens towards black."""
    if factor >= 1:
        return tuple(min(255, int(c + (255 - c) * (factor - 1))) for c in rgb)
    return tuple(int(c * factor) for c in rgb)


def css_color(rgb):
    return "#%02x%02x%02x" % tuple(rgb)


def find_chrome():
    for c in (os.environ.get("CHROME"), shutil.which("google-chrome"), shutil.which("chromium"),
              shutil.which("chromium-browser"), shutil.which("chrome"),
              r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"):
        if c and os.path.exists(c):
            return c
    raise RuntimeError("no Chrome found to draw the card")


def download(job):
    url, path = job
    if not url:
        return None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
            path.write_bytes(r.read())
        return path.as_uri()
    except Exception:
        return None


def name_px(name):
    n = len(name)
    return 28 if n <= 14 else 25 if n <= 18 else 22 if n <= 23 else 19


def tile_html(t, art, vbuck):
    top, bottom = t["colors"]
    style = f"--a:{css_color(top)};--b:{css_color(bottom)};--edge:{css_color(shade(top, 1.3))}"
    esc = html.escape
    parts = [f'<div class="tile" style="{style}"><div class="art">']
    if art:
        parts.append(f'<img src="{art}">')
    parts.append("</div>")
    if t.get("badge"):
        parts.append(f'<span class="badge">{esc(t["badge"])}</span>')
    parts.append(f'<div class="bar"><div class="name" style="font-size:{name_px(t["name"])}px">{esc(t["name"])}</div>')
    if t.get("price") is not None:
        old = t.get("regular") or 0
        parts.append('<div class="price">' + (f'<img src="{vbuck}">' if vbuck else "") + f'<b>{t["price"]:,}</b>'
                     + (f"<s>{old:,}</s>" if old > t["price"] else "") + "</div>")
    elif t.get("line"):
        parts.append(f'<div class="line">{esc(t["line"])}</div>')
    parts.append("</div></div>")
    return "".join(parts)


def to_jpeg(png, quality=88):
    try:
        from PIL import Image
    except ImportError:                          # only the runs that draw a card pay for installing Pillow
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pillow"], check=True)
        from PIL import Image
    out = Path(str(png) + ".jpg")
    Image.open(png).convert("RGB").save(out, "JPEG", quality=quality, optimize=True, subsampling=0)
    return out.read_bytes()


def draw_card(title, subtitle, tiles, footer, avatar_url=None):
    """Returns JPEG bytes: a branded header, then the tiles right-to-left, five per row."""
    rows = max(1, (len(tiles) + COLS - 1) // COLS)
    height = FIXED + rows * TILE_H + (rows - 1) * GAP
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        jobs = [(t.get("image"), tmp / f"i{i}.png") for i, t in enumerate(tiles)]
        jobs += [(VBUCK, tmp / "vbuck.png"), (avatar_url, tmp / "avatar.png")]
        with ThreadPoolExecutor(8) as pool:
            got = list(pool.map(download, jobs))
        arts, vbuck, avatar = got[:len(tiles)], got[-2], got[-1]

        css = (CSS.replace("{{CHANGA}}", (FONTS / "Changa.ttf").as_uri())
               .replace("{{TAJ7}}", (FONTS / "Tajawal-Bold.ttf").as_uri())
               .replace("{{TAJ8}}", (FONTS / "Tajawal-ExtraBold.ttf").as_uri())
               .replace("{{W}}", str(WIDTH)).replace("{{H}}", str(height)).replace("{{PAD}}", str(PAD))
               .replace("{{COLS}}", str(COLS)).replace("{{TW}}", str(TILE_W)).replace("{{TH}}", str(TILE_H))
               .replace("{{GAP}}", str(GAP)))
        esc = html.escape
        sub = "<i>•</i>".join(esc(s.strip()) for s in subtitle.split("•"))
        brand = ('<div class="brand"><div><div class="n">أبود قنّاص</div><div class="h">@Ab_sn6</div></div>'
                 + (f'<img src="{avatar}">' if avatar else "") + "</div>")
        page = (f'<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><style>{css}</style></head>'
                f'<body><div class="card"><div class="hex"></div><header><div><div class="title">{esc(title)}</div>'
                f'<div class="sub">{sub}</div></div>{brand}</header><div class="rule"></div><div class="grid">'
                + "".join(tile_html(t, a, vbuck) for t, a in zip(tiles, arts))
                + f"</div><footer>{esc(footer)}</footer></div></body></html>")
        page_path = tmp / "card.html"
        page_path.write_text(page, encoding="utf-8")
        png = tmp / "card.png"
        subprocess.run([find_chrome(), "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
                        "--allow-file-access-from-files", "--force-device-scale-factor=1",
                        f"--window-size={WIDTH},{height}", "--virtual-time-budget=8000",
                        f"--screenshot={png}", page_path.as_uri()],
                       check=True, timeout=120, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return to_jpeg(png)
