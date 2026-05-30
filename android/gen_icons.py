#!/usr/bin/env python3
"""
WatchDawg Android TV icon + banner generator.

Produces an original orange-on-dark "alert dog silhouette beside a glowing
play tile" mark, in the spirit of the supplied concept art (not a copy).

Outputs:
  - Adaptive launcher icon (foreground PNG, transparent) at multiple densities
  - Adaptive launcher icon background (solid dark) at multiple densities
  - Legacy square launcher icon (ic_launcher) at multiple densities
  - Round launcher icon (ic_launcher_round) at multiple densities
  - Android TV banner 320x180 (xhdpi) -- required by Leanback launcher
"""

import math
import os
from PIL import Image, ImageDraw, ImageFilter

# ----- Brand palette -------------------------------------------------------
ORANGE        = (255, 122, 24)     # primary accent
ORANGE_BRIGHT = (255, 150, 50)
ORANGE_DEEP   = (200, 80, 0)
DARK_BG       = (14, 14, 18)        # near-black panel
DARK_BG2      = (24, 22, 30)
GLOW          = (255, 130, 30)

OUT = os.path.dirname(os.path.abspath(__file__))


def _ss(size):
    """Supersample factor base canvas for crisp downscale."""
    return size * 4


def draw_dog_silhouette(draw, cx, cy, scale, color):
    """
    Draw a stylized sitting alert-dog silhouette (French-bulldog/terrier vibe:
    big upright bat ears, sitting posture) facing right toward the play tile.
    Coordinates are relative to (cx, cy) which is the dog's seat center.
    """
    s = scale
    # Body: sitting haunch + chest, built from a rounded blob polygon
    body = [
        (cx - 0.55*s, cy + 0.95*s),   # bottom back (rump on ground)
        (cx - 0.70*s, cy + 0.30*s),   # back rises
        (cx - 0.62*s, cy - 0.30*s),   # upper back / neck base
        (cx - 0.40*s, cy - 0.62*s),   # back of head base
        (cx - 0.30*s, cy - 0.55*s),
        (cx - 0.05*s, cy - 0.45*s),   # chest top
        (cx + 0.18*s, cy - 0.10*s),   # chest front bulge
        (cx + 0.30*s, cy + 0.45*s),   # front leg front
        (cx + 0.30*s, cy + 0.95*s),   # front paw on ground
        (cx + 0.05*s, cy + 0.98*s),   # between paws
        (cx + 0.02*s, cy + 0.55*s),   # tuck
        (cx - 0.18*s, cy + 0.95*s),   # rear paw
    ]
    draw.polygon(body, fill=color)

    # Head (rounded), sits atop the neck, facing right
    hx, hy, hr = cx - 0.18*s, cy - 0.70*s, 0.34*s
    draw.ellipse([hx - hr, hy - hr, hx + hr, hy + hr], fill=color)

    # Snout nub pointing right
    snout = [
        (hx + 0.10*s, hy - 0.08*s),
        (hx + 0.46*s, hy - 0.02*s),
        (hx + 0.46*s, hy + 0.16*s),
        (hx + 0.10*s, hy + 0.22*s),
    ]
    draw.polygon(snout, fill=color)

    # Two big upright bat ears
    ear_l = [
        (hx - 0.22*s, hy - 0.20*s),
        (hx - 0.34*s, hy - 0.86*s),
        (hx - 0.02*s, hy - 0.42*s),
    ]
    ear_r = [
        (hx + 0.06*s, hy - 0.22*s),
        (hx + 0.16*s, hy - 0.88*s),
        (hx + 0.30*s, hy - 0.30*s),
    ]
    draw.polygon(ear_l, fill=color)
    draw.polygon(ear_r, fill=color)

    # Tail curling up behind the rump
    tail = [
        (cx - 0.55*s, cy + 0.70*s),
        (cx - 0.86*s, cy + 0.40*s),
        (cx - 0.80*s, cy + 0.18*s),
        (cx - 0.62*s, cy + 0.45*s),
    ]
    draw.polygon(tail, fill=color)


def draw_play_tile(img, draw, tx, ty, tw, th, glow=True):
    """Glowing rounded play tile to the dog's right."""
    radius = int(min(tw, th) * 0.22)
    box = [tx, ty, tx + tw, ty + th]

    if glow:
        # outer glow: draw on a separate layer, blur, composite
        glow_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow_layer)
        gpad = int(tw * 0.10)
        gd.rounded_rectangle(
            [tx - gpad, ty - gpad, tx + tw + gpad, ty + th + gpad],
            radius=radius + gpad, fill=GLOW + (255,)
        )
        glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(int(tw * 0.10)))
        img.alpha_composite(glow_layer)

    # tile body (orange) with a subtle inner darkening
    draw.rounded_rectangle(box, radius=radius, fill=ORANGE)
    # thin bright inner border
    draw.rounded_rectangle(box, radius=radius, outline=ORANGE_BRIGHT,
                           width=max(2, int(tw * 0.03)))

    # play triangle (dark, knocked out of the tile)
    cx = tx + tw * 0.54
    cy = ty + th * 0.5
    r = min(tw, th) * 0.26
    tri = [
        (cx - r*0.7, cy - r),
        (cx - r*0.7, cy + r),
        (cx + r,     cy),
    ]
    draw.polygon(tri, fill=DARK_BG)


def make_foreground(size, with_glow=True, margin_factor=0.62):
    """
    Adaptive-icon foreground: transparent background, art centered within the
    safe zone. margin_factor controls how much of the canvas the art occupies
    (adaptive icons mask ~66% safe zone, so keep art compact).
    """
    S = _ss(size)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    art_w = S * margin_factor
    left = (S - art_w) / 2
    top = (S - art_w) / 2

    # Layout within art box: dog on left ~46%, play tile on right ~46%
    dog_scale = art_w * 0.30
    dog_cx = left + art_w * 0.30
    dog_cy = top + art_w * 0.46

    draw_dog_silhouette(draw, dog_cx, dog_cy, dog_scale, ORANGE)

    tw = art_w * 0.46
    th = tw * 0.74
    tx = left + art_w * 0.50
    ty = top + art_w * 0.30
    draw_play_tile(img, draw, tx, ty, tw, th, glow=with_glow)

    return img.resize((size, size), Image.LANCZOS)


def make_background(size):
    """Adaptive-icon background: dark radial-ish panel."""
    S = _ss(size)
    img = Image.new("RGBA", (S, S), DARK_BG + (255,))
    # subtle vertical gradient
    top = Image.new("RGBA", (S, S), DARK_BG2 + (255,))
    mask = Image.new("L", (S, S), 0)
    md = ImageDraw.Draw(mask)
    for y in range(S):
        md.line([(0, y), (S, y)], fill=int(120 * (1 - y / S)))
    img = Image.composite(top, img, mask)
    return img.resize((size, size), Image.LANCZOS)


def make_legacy(size, round_icon=False):
    """Self-contained legacy launcher icon (art on dark, optionally circular)."""
    bg = make_background(size).convert("RGBA")
    fg = make_foreground(size, with_glow=True, margin_factor=0.74)
    bg.alpha_composite(fg)
    if round_icon:
        S = _ss(size)
        mask = Image.new("L", (S, S), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, S, S], fill=255)
        mask = mask.resize((size, size), Image.LANCZOS)
        out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        out.paste(bg, (0, 0), mask)
        return out
    return bg


def make_banner(w=320, h=180):
    """Android TV Leanback banner: logo mark + wordmark on dark."""
    SS = 4
    W, H = w * SS, h * SS
    img = Image.new("RGBA", (W, H), DARK_BG + (255,))
    # gradient wash
    top = Image.new("RGBA", (W, H), DARK_BG2 + (255,))
    mask = Image.new("L", (W, H), 0)
    md = ImageDraw.Draw(mask)
    for y in range(H):
        md.line([(0, y), (W, y)], fill=int(90 * (1 - y / H)))
    img = Image.composite(top, img, mask)
    draw = ImageDraw.Draw(img)

    # Mark on the left ~quarter, sized down so the wordmark has room
    mark = H * 0.9
    dog_scale = mark * 0.17
    dog_cx = W * 0.10
    dog_cy = H * 0.54
    draw_dog_silhouette(draw, dog_cx, dog_cy, dog_scale, ORANGE)

    tw = mark * 0.30
    th = tw * 0.74
    tx = W * 0.13
    ty = H * 0.33
    draw_play_tile(img, draw, tx, ty, tw, th, glow=True)

    # Wordmark "WatchDawg" -- auto-size to fit the remaining width with margin
    try:
        from PIL import ImageFont
        font_path = None
        for fp in [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]:
            if os.path.exists(fp):
                font_path = fp
                break

        word_left = int(W * 0.30)
        word_right_margin = int(W * 0.04)
        avail_w = W - word_left - word_right_margin
        full_word = "WatchDawg"

        if font_path:
            # binary-ish search for the largest font size that fits avail_w
            size_px = int(H * 0.30)
            font = ImageFont.truetype(font_path, size_px)
            while size_px > 8:
                font = ImageFont.truetype(font_path, size_px)
                bb = draw.textbbox((0, 0), full_word, font=font)
                if (bb[2] - bb[0]) <= avail_w:
                    break
                size_px -= 4
            # vertical center the text on the mark
            bb = draw.textbbox((0, 0), full_word, font=font)
            text_h = bb[3] - bb[1]
            ty_word = int(H * 0.5 - text_h / 2 - bb[1])
            # "Watch" white, "Dawg" orange
            draw.text((word_left, ty_word), "Watch", font=font, fill=(245, 245, 248))
            wbb = draw.textbbox((word_left, ty_word), "Watch", font=font)
            draw.text((wbb[2], ty_word), "Dawg", font=font, fill=ORANGE)
        else:
            font = ImageFont.load_default()
            draw.text((word_left, int(H * 0.45)), full_word, font=font,
                      fill=(245, 245, 248))
    except Exception as e:
        print("Font render skipped:", e)

    return img.resize((w, h), Image.LANCZOS)


# Density buckets for launcher icons
LAUNCHER_DENSITIES = {
    "mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192,
}
FG_DENSITIES = {  # adaptive foreground/background are 108dp
    "mdpi": 108, "hdpi": 162, "xhdpi": 216, "xxhdpi": 324, "xxxhdpi": 432,
}

RES = os.path.join(OUT, "app", "src", "main", "res")


def ensure(p):
    os.makedirs(p, exist_ok=True)


def main():
    # Adaptive foreground + background
    for dens, sz in FG_DENSITIES.items():
        d = os.path.join(RES, f"mipmap-{dens}")
        ensure(d)
        make_foreground(sz).save(os.path.join(d, "ic_launcher_foreground.png"))
        make_background(sz).save(os.path.join(d, "ic_launcher_background.png"))

    # Legacy + round
    for dens, sz in LAUNCHER_DENSITIES.items():
        d = os.path.join(RES, f"mipmap-{dens}")
        ensure(d)
        make_legacy(sz, round_icon=False).save(os.path.join(d, "ic_launcher.png"))
        make_legacy(sz, round_icon=True).save(os.path.join(d, "ic_launcher_round.png"))

    # TV banner (xhdpi drawable)
    dband = os.path.join(RES, "drawable-xhdpi")
    ensure(dband)
    make_banner().save(os.path.join(dband, "banner.png"))

    # A larger preview for the user to eyeball
    make_legacy(432).save(os.path.join(OUT, "icon_preview.png"))
    make_banner(640, 360).save(os.path.join(OUT, "banner_preview.png"))
    print("Icons + banner generated.")


if __name__ == "__main__":
    main()
