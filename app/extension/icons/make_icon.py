"""Draw the toolbar headphones, big, then downsample to the four sizes.

Drawn rather than sourced: the reference was a watermarked stock file, and a
headphones glyph is a shape, not an asset. Everything is laid out on a 1024
canvas and reduced with LANCZOS, which is what keeps the 16px one from turning
to mush.

Black, and sized to fill the square. Worth knowing: a black glyph is nearly
invisible on a dark browser toolbar, and Chrome gives an extension no way to
swap the action icon by theme -- there is no matchMedia in a service worker.
If it disappears on yours, the fix is a lighter INK here, not a code change.
"""

import os

from PIL import Image, ImageDraw

S = 1024
INK = (0, 0, 0, 255)

# Sized to very nearly fill the square: at 16px in a toolbar there is no room
# for polite margins, and the glyph has to be legible at a glance.
cx, cy = S // 2, int(S * 0.55)
R = int(S * 0.442)                    # outer radius of the headband
T = int(S * 0.110)                    # its thickness
r = R - T // 2                        # centreline

img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# The band: top half only, with circles rounding off both ends.
d.arc((cx - r, cy - r, cx + r, cy + r), 180, 360, fill=INK, width=T)
for side in (-1, 1):
    ex = cx + side * r
    d.ellipse((ex - T // 2, cy - T // 2, ex + T // 2, cy + T // 2), fill=INK)

# Each ear: an outer cup, a slim gap, then the driver over it.
cup_w, cup_h = int(S * 0.160), int(S * 0.301)
drv_w, drv_h = int(S * 0.109), int(S * 0.339)
top = cy + int(S * 0.006)

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

    d.rounded_rectangle(cup, radius=cup_w // 2, fill=INK)
    d.rectangle(gap, fill=(0, 0, 0, 0))          # punched, so it stays a gap
    d.rounded_rectangle(drv, radius=int(drv_w * 0.34), fill=INK)

# Written next to this file: the manifest points at icons/<size>.png. Re-run
# it after changing anything above -- the PNGs are the build output, this is
# the source.
here = os.path.dirname(os.path.abspath(__file__))
for size in (16, 32, 48, 128):
    img.resize((size, size), Image.LANCZOS).save(os.path.join(here, "%d.png" % size))
print("wrote 16, 32, 48 and 128 into", here)
