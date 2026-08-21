#!/usr/bin/env python3
"""
make_avatar.py -- the Sherlock Quotes Bluesky avatar.

A cream "221B" on Strand Magazine blue, inside a single ruled ring. It replaces
a photograph of a phone box carrying the "I believe in Sherlock" graffiti from
the 2012 television series: an image of unknown provenance on an account whose
bio promises "All public domain".

Three things about the drawing are measured rather than eyeballed:

  It has to survive 40 px, which is the size almost everyone will ever see it
  at. The first version had two concentric rings, a heavy one and a hairline,
  and at 40 px they smear into one grey band. One ring, heavier, stays a ring.

  Baskerville, not Georgia. Measured at 200 pt, Georgia Bold sets 2 and 1 at
  108 units against a 139-unit B, so "221B" reads as three small digits beside
  one large capital and the digits lose their weight first as it shrinks.
  Baskerville Bold sets 2 at 142, 1 at 135 and B at 133: an even run.

  PIL does not anti-alias the outline of an ellipse, so the ring is drawn at 4x
  and downsampled with LANCZOS. Drawn at final size it is a staircase.

Bluesky also crops the avatar to a circle, so the corners are dead space.
Nothing here has yet come close to that edge -- the furthest drawn pixel is the
ring, at 410 of the 512 available -- but clears_the_crop() asserts it before
writing, because the square PNG looks fine either way and the failure would
only ever show up after it shipped.

Usage:
    python3 make_avatar.py                 # writes avatar.png at 1024px
    python3 make_avatar.py --size 512
    python3 make_avatar.py --proof         # also writes 40px feed-size proofs
"""

import argparse
import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
SS = 4                                    # supersampling factor

STRAND = (26, 45, 74)                     # The Strand Magazine's cover blue
CREAM = (246, 240, 226)                   # aged paper

# Baskerville.ttc, face 1, is the Bold. Its figures are lining; see the note above.
SERIF = "/System/Library/Fonts/Supplemental/Baskerville.ttc"
SERIF_INDEX = 1

TEXT = "221B"
TEXT_SIZE = 262                           # design units on a 1024 grid
RING_INSET = 104
RING_WIDTH = 16


def draw(size):
    S = size * SS
    u = S / 1024.0                        # design units: laid out on a 1024 grid

    img = Image.new("RGB", (S, S), STRAND)
    d = ImageDraw.Draw(img)

    font = ImageFont.truetype(SERIF, int(TEXT_SIZE * u), index=SERIF_INDEX)
    x0, y0, x1, y1 = d.textbbox((0, 0), TEXT, font=font)
    d.text(((S - (x1 - x0)) / 2 - x0, (S - (y1 - y0)) / 2 - y0),
           TEXT, font=font, fill=CREAM)

    d.ellipse([RING_INSET * u, RING_INSET * u, S - RING_INSET * u, S - RING_INSET * u],
              outline=CREAM, width=int(RING_WIDTH * u))

    return img.resize((size, size), Image.LANCZOS)


def furthest_drawn_pixel(size=1024):
    """Distance from the centre to the furthest pixel that differs from the ground.

    Measured row by row rather than from the bounding box: the mark is a disc,
    so its bbox corners sit far outside it and a bbox test rejects a drawing
    that is comfortably inside the crop.
    """
    from PIL import ImageChops

    img = draw(size)
    mask = ImageChops.difference(img, Image.new("RGB", img.size, STRAND)).convert("L")
    mask = mask.point(lambda v: 255 if v > 8 else 0)

    c = size / 2.0
    furthest = 0.0
    for y in range(size):
        row = mask.crop((0, y, size, y + 1)).getbbox()
        if not row:
            continue
        dy = abs(y + 0.5 - c)
        for x in (row[0], row[2] - 1):
            furthest = max(furthest, ((x + 0.5 - c) ** 2 + dy ** 2) ** 0.5)
    return furthest


def clears_the_crop(size=1024):
    """True when every drawn pixel sits inside the circle Bluesky crops to."""
    return furthest_drawn_pixel(size) <= size / 2.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=1024)
    ap.add_argument("--proof", action="store_true",
                    help="also write a 40px feed-size proof, and an 8x zoom of it")
    args = ap.parse_args()

    if not clears_the_crop():
        raise SystemExit("refusing to write: the mark runs outside the circular crop")

    img = draw(args.size)
    out = os.path.join(HERE, "avatar.png")
    img.save(out, optimize=True)
    print(f"wrote {out}  {img.size[0]}x{img.size[1]}  {os.path.getsize(out) / 1024:.0f} KB")

    if args.proof:
        small = draw(40)
        small.save(os.path.join(HERE, "avatar_40px.png"))
        small.resize((320, 320), Image.NEAREST).save(os.path.join(HERE, "avatar_40px_zoom.png"))
        print("wrote avatar_40px.png and avatar_40px_zoom.png")


if __name__ == "__main__":
    main()
