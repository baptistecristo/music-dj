"""Draw the toolbar headphones, big, then downsample to the four sizes.

Drawn rather than sourced: the reference was a watermarked stock file, and a
headphones glyph is a shape, not an asset. Everything is laid out on a 1024
canvas and reduced with LANCZOS, which is what keeps the 16px one from turning
to mush.
"""
import os

from PIL import Image, ImageDraw

S = 1024
ACCENT = (239, 95, 116, 255)          # --accent from the overlay

cx, cy = S // 2, int(S * 0.56)
R = int(S * 0.345)                    # outer radius of the headband
T = int(S * 0.086)                    # its thickness
r = R - T // 2                        # centreline

img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# The band: top half only, with circles rounding off both ends.
d.arc((cx - r, cy - r, cx + r, cy + r), 180, 360, fill=ACCENT, width=T)
for side in (-1, 1):
    ex = cx + side * r
    d.ellipse((ex - T // 2, cy - T // 2, ex + T // 2, cy + T // 2), fill=ACCENT)

# Each ear: an outer cup, a slim gap, then the driver over it.
cup_w, cup_h = int(S * 0.125), int(S * 0.235)
drv_w, drv_h = int(S * 0.085), int(S * 0.265)
top = cy + int(S * 0.005)

for side in (-1, 1):
    outer = cx + side * R                       # flush with the band's edge
    if side < 0:
        cup = (outer, top, outer + cup_w, top + cup_h)
        drv_x = outer + cup_w - int(S * 0.035)
        drv = (drv_x, top, drv_x + drv_w, top + drv_h)
        gap = (drv[0] - int(S * 0.014), top - 4, drv[0] - 4, top + cup_h + 4)
    else:
        cup = (outer - cup_w, top, outer, top + cup_h)
        drv_x = outer - cup_w + int(S * 0.035)
        drv = (drv_x - drv_w, top, drv_x, top + drv_h)
        gap = (drv[2] + 4, top - 4, drv[2] + int(S * 0.014), top + cup_h + 4)

    d.rounded_rectangle(cup, radius=cup_w // 2, fill=ACCENT)
    d.rectangle(gap, fill=(0, 0, 0, 0))          # punched, so it stays a gap
    d.rounded_rectangle(drv, radius=int(drv_w * 0.34), fill=ACCENT)

# Written next to this file: the manifest points at icons/<size>.png. Re-run
# it after changing anything above -- the PNGs are the build output, this is
# the source.
here = os.path.dirname(os.path.abspath(__file__))
for size in (16, 32, 48, 128):
    img.resize((size, size), Image.LANCZOS).save(os.path.join(here, "%d.png" % size))
print("wrote 16, 32, 48 and 128 into", here)
