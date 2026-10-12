"""Build assets/vox-lupine.ico from assets/vox-lupine.png (the logo).

To change the logo, replace assets/vox-lupine.png (a square image with a transparent
background, 256 px or larger) and run this, or just build the installer, which runs it.

Usage: python tools/make_icon.py      (needs Pillow: pip install pillow)
"""
from pathlib import Path

from PIL import Image

assets = Path(__file__).resolve().parent.parent / "assets"
logo = Image.open(assets / "vox-lupine.png").convert("RGBA")
logo.save(assets / "vox-lupine.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                                            (64, 64), (128, 128), (256, 256)])
print("wrote", assets / "vox-lupine.ico")
