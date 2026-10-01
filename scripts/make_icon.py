"""Generate the NightSeam application icon (assets/app.ico) and web favicon."""

import math
import random
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
S = 256

img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

# vertical night gradient
top, bot = (13, 17, 26), (38, 52, 86)
bg = Image.new("RGBA", (S, S), (0, 0, 0, 0))
db = ImageDraw.Draw(bg)
for y in range(S):
    t = y / S
    c = tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)) + (255,)
    db.line([(0, y), (S, y)], fill=c)

mask = Image.new("L", (S, S), 0)
dm = ImageDraw.Draw(mask)
dm.rounded_rectangle([6, 6, S - 6, S - 6], radius=54, fill=255)
img.paste(bg, (0, 0), mask)

d = ImageDraw.Draw(img)

# faint stars
rnd = random.Random(20260822)
for _ in range(46):
    x = rnd.uniform(18, S - 18)
    y = rnd.uniform(18, S - 18)
    r = rnd.uniform(1.0, 3.2)
    a = rnd.randint(90, 190)
    d.ellipse([x - r, y - r, x + r, y + r], fill=(218, 227, 244, a))


def sparkle(draw, cx, cy, R, fill, outline=None):
    """Four-pointed star with concave sides."""
    w = R * 0.26
    tips = [(cx, cy - R), (cx + R, cy), (cx, cy + R), (cx - R, cy)]
    waist = []
    for k in range(4):
        ang = math.radians(45 + k * 90)
        waist.append((cx + w * math.cos(ang), cy + w * math.sin(ang)))
    pts = []
    for i in range(4):
        pts.append(tips[i])
        pts.append(waist[(i + 3) % 4])
    if outline:
        draw.polygon(pts, fill=fill, outline=outline)
    else:
        draw.polygon(pts, fill=fill)


# main gold sparkle
sparkle(d, 128, 122, 92, (255, 209, 102), outline=(214, 160, 60))
# small white companion sparkle
sparkle(d, 196, 74, 30, (233, 238, 250))

out_ico = ROOT / "assets" / "app.ico"
out_ico.parent.mkdir(parents=True, exist_ok=True)
sizes = [(16, 16), (24, 24), (32, 32), (48, 48),
         (64, 64), (128, 128), (256, 256)]
img.save(out_ico, format="ICO", sizes=sizes)

fav = ROOT / "static" / "favicon.ico"
fav.write_bytes(out_ico.read_bytes())

print(f"wrote {out_ico} ({out_ico.stat().st_size} bytes)")
print(f"wrote {fav} ({fav.stat().st_size} bytes)")
