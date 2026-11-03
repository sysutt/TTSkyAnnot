"""Build application/installer icons from the approved layered-glass artwork.

The master artwork is preserved; run this script from any working directory.
"""
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "packaging/logo-concepts/skyannot-b1-refined-v1.png"
SIZES = (16, 24, 32, 48, 64, 96, 128, 256)


def make() -> None:
    artwork = Image.open(SOURCE).convert("RGBA")
    bounds = artwork.getchannel("A").point(lambda a: 255 if a >= 128 else 0).getbbox()
    if bounds is None:
        raise ValueError("Approved icon artwork is fully transparent")
    tile = artwork.crop(bounds)
    tile.thumbnail((944, 944), Image.Resampling.LANCZOS)
    master = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    master.alpha_composite(tile, ((1024-tile.width)//2, (1024-tile.height)//2))
    png_path = ROOT / "data/app/skyannot.png"
    ico_path = ROOT / "data/app/skyannot.ico"
    png_path.parent.mkdir(parents=True, exist_ok=True)
    ico_path.parent.mkdir(parents=True, exist_ok=True)
    master.resize((512, 512), Image.Resampling.LANCZOS).save(png_path)
    master.resize((256, 256), Image.Resampling.LANCZOS).save(
        ico_path, format="ICO", sizes=[(size, size) for size in SIZES])
    print(f"Updated {png_path} and {ico_path}")


if __name__ == "__main__":
    make()
