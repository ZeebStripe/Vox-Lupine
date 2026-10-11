"""Draw the Vox Lupine icon (wolf head in front of a full moon) -> assets/.

Usage: python tools/make_icon.py      (needs Pillow: pip install pillow)
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
S = 1024                     # draw big, then downsample for smooth edges
k = S / 256                  # design coordinates are on a 256 grid

NIGHT, MOON, WOLF, EYE = "#1c1f33", "#f3e7c6", "#11121c", "#f3e7c6"


def P(points):
    return [(x * k, y * k) for x, y in points]


img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle([0, 0, S - 1, S - 1], radius=int(48 * k), fill=NIGHT)
d.ellipse(P([(40, 22), (216, 198)]), fill=MOON)                    # full moon
d.ellipse(P([(150, 60), (172, 82)]), fill="#e4d6b2")                # craters
d.ellipse(P([(70, 140), (86, 156)]), fill="#e4d6b2")
# Wolf head, front view: ears, cheeks, muzzle.
d.polygon(P([(58, 46), (100, 100), (128, 92), (156, 100), (198, 46), (188, 126),
             (204, 150), (170, 168), (150, 196), (128, 226), (106, 196), (86, 168),
             (52, 150), (68, 126)]), fill=WOLF)
d.polygon(P([(70, 64), (96, 100), (84, 108)]), fill="#2a2d45")      # inner ears
d.polygon(P([(186, 64), (160, 100), (172, 108)]), fill="#2a2d45")
d.polygon(P([(96, 136), (118, 140), (100, 146)]), fill=EYE)         # eyes
d.polygon(P([(160, 136), (138, 140), (156, 146)]), fill=EYE)
d.polygon(P([(118, 200), (138, 200), (128, 212)]), fill="#2a2d45")  # nose

out = ROOT / "assets"
out.mkdir(exist_ok=True)
big = img.resize((256, 256), Image.LANCZOS)
big.save(out / "vox-lupine.png")
big.save(out / "vox-lupine.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                                        (128, 128), (256, 256)])
print("wrote", out / "vox-lupine.ico")
