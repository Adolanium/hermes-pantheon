"""Build the Pantheon skins for Hermes in the terminal (the `hermes` CLI and TUI).

Reads the eighteen themes packed into desktop/plugin.js and writes one skin per
god to cli/skins/pantheon-<name>.yaml:

- banner_hero: the god's bust from the top of the Desktop sidebar, in the start
  panel's left column, drawn with half blocks (two solid square pixels per cell)
  at half the Desktop image's size, so it keeps the pixel-art look. The panel is already about as tall
  as its tool and skill lists; above the panel there is only room for the
  lettering before art would scroll away. The bust, not the wide new-session
  scene: a terminal has too few cells for the scene's figures.
- banner_art: the whole Desktop image redrawn 80 columns wide (30 to 40 rows), dithered in
  three tones, the Desktop inks and one between them. Two colours per cell; the TUI
  paints one colour per line, so this is for the classic CLI, and a Hermes without
  the key (or a terminal too narrow for it) keeps banner_hero.
- banner_thumb: the same picture 40 columns wide, for the preview Hermes shows
  above the /skin menu while you browse skins (classic CLI).
- banner_logo: the HERMES AGENT lettering of the stock banner in the god's colours.
- colors / light_colors: the god's palette for dark and for light terminals,
  every colour checked for readable contrast against that terminal ground.

The TUI draws each `[#colour]...[/]` run on a line of its own, so every row of
art carries exactly one colour: the marble's lit pixels are solid and its dense
shadow a half-density checkerboard, the way the two inks read on Desktop. (Braille was
tried first: its dots render too thin and sparse for the faces to read.)

Usage: python3 cli/build_skins.py  (needs Pillow)
"""

import base64
import colorsys
import io
import json
import re
from pathlib import Path

from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "cli" / "skins"

# Nominal terminal grounds the palettes are checked against.
DARK_GROUND = "#1c1c1c"
LIGHT_GROUND = "#ffffff"

# Widest bust in Desktop pixels; halved, that is 60 terminal columns. The TUI gives
# the hero column at most 40% of the width, so the bust stays whole from about 160 up.
HERO_MAX_WIDTH = 120

# banner_art's width in terminal columns: half again the hero's. It takes the whole Desktop
# image (no crop around the face), so the god shows entire, about 30 to 40 rows tall.
ART_COLUMNS = 80
ART_MAX_WIDTH = 160

# banner_thumb's width: the same picture at half size, small enough to preview above the
# /skin menu in a terminal of about 45 rows.
THUMB_COLUMNS = 40

# Tone each banner_art level stands for when dithering (shadow, mid, light).
DITHER_TONES = {1: 0.24, 2: 0.61, 3: 1.0}

# Skin names carry a prefix: Desktop also lists terminal skins as themes, and a skin
# named like a plugin theme would shadow it, leaving the palette without the art.
PREFIX = "pantheon-"

# The HERMES AGENT lettering of the stock banner, recoloured per god.
LETTERING = [
    "██╗  ██╗███████╗██████╗ ███╗   ███╗███████╗███████╗       █████╗  ██████╗ ███████╗███╗   ██╗████████╗",
    "██║  ██║██╔════╝██╔══██╗████╗ ████║██╔════╝██╔════╝      ██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝",
    "███████║█████╗  ██████╔╝██╔████╔██║█████╗  ███████╗█████╗███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║",
    "██╔══██║██╔══╝  ██╔══██╗██║╚██╔╝██║██╔══╝  ╚════██║╚════╝██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║",
    "██║  ██║███████╗██║  ██║██║ ╚═╝ ██║███████╗███████║      ██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║",
    "╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝╚═╝     ╚═╝╚══════╝╚══════╝      ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝",
]

# ── Colour helpers ──────────────────────────────────────────────────────────

def rgb(hex_color):
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def hexc(c):
    r, g, b = (max(0, min(255, round(v))) for v in c[:3])
    return f"#{r:02x}{g:02x}{b:02x}"


def luminance(c):
    def channel(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(v) for v in c[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def readable(hex_color, ground, ratio):
    """Nudge a colour's lightness, keeping its hue, until it reads on `ground`."""
    c, g = rgb(hex_color), rgb(ground)
    h, l, s = colorsys.rgb_to_hls(*(v / 255 for v in c))
    step = 0.02 if luminance(g) < 0.5 else -0.02
    while contrast(c, g) < ratio and 0 <= l <= 1:
        l = min(1, max(0, l + step))
        c = tuple(v * 255 for v in colorsys.hls_to_rgb(h, l, s))
        if l in (0, 1):
            break
    return hexc(c)


def mix(a, b, t):
    ca, cb = rgb(a), rgb(b)
    return hexc(tuple(x + (y - x) * t for x, y in zip(ca, cb)))


def stop(value):
    """`#rrggbb 45%` (a wordmark gradient stop) -> `#rrggbb`."""
    return value.split()[0]


# ── Palettes ────────────────────────────────────────────────────────────────

def palette(theme, ground):
    """Skin colours for one terminal polarity, built from the god's Desktop palette."""
    c, v = theme["colors"], theme["vars"]
    dark_terminal = luminance(rgb(ground)) < 0.5
    # On a dark terminal text takes the god's lightest tone and fills its darkest; reversed on light.
    pick, shade = (max, min) if dark_terminal else (min, max)
    lum = lambda x: luminance(rgb(x))

    text = pick(c["background"], c["foreground"], key=lum)
    fill = shade(c["background"], c["foreground"], key=lum)
    title = pick((stop(s) for s in v["wordmark"]), key=lum)
    accent, rule = c["ring"], v["rule"]

    fg = lambda color, ratio=4.5: readable(color, ground, ratio)
    return {
        "banner_border": fg(rule, 3), "banner_title": fg(title), "banner_accent": fg(accent),
        "banner_dim": fg(v["dim"], 3), "banner_text": fg(text), "ui_accent": fg(accent),
        "ui_label": fg(mix(title, accent, 0.35)), "ui_ok": fg("#4caf50"), "ui_error": fg(c["destructive"]),
        "ui_warn": fg("#e0a030"), "prompt": fg(text), "input_rule": fg(rule, 3),
        "response_border": fg(title, 3),
        # Fills: the status bar and menus sit on the god's own ground.
        "status_bar_bg": fill, "status_bar_text": readable(text, fill, 4.5),
        "status_bar_strong": readable(title, fill, 4.5), "status_bar_dim": readable(v["dim"], fill, 3),
        "status_bar_good": readable("#4caf50", fill, 3), "status_bar_warn": readable("#e0a030", fill, 3),
        "status_bar_bad": readable(c["destructive"], fill, 3), "status_bar_critical": readable("#ef5350", fill, 3),
        "session_label": fg(accent), "session_border": fg(rule, 2),
        "completion_menu_bg": fill, "completion_menu_current_bg": mix(fill, accent, 0.3),
        "selection_bg": mix(fill, text, 0.25), "shell_dollar": fg(accent), "voice_status_bg": fill,
    }


# ── Block art ───────────────────────────────────────────────────────────────

def decode(data_uri):
    return Image.open(io.BytesIO(base64.b64decode(data_uri.split(",", 1)[1]))).convert("RGBA")


def inks(img):
    """The image's (shadow, light) ink colours: its darkest and lightest opaque pixels."""
    colours = sorted({px[:3] for _, px in (img.getcolors(1 << 16) or []) if px[3]}, key=luminance)
    return colours[0], colours[-1]


def crop_bust(img, max_width=HERO_MAX_WIDTH):
    """Crop a bust to its ink, keeping at most `max_width` columns around its light (the face).
    Returns (image, shadow_ink, light_ink)."""
    img = img.crop(img.getchannel("A").getbbox())
    shadow, light = inks(img)
    if img.width > max_width:
        lit_x = [x for x in range(img.width) for y in range(img.height) if img.getpixel((x, y))[:3] == light]
        centre = sum(lit_x) / len(lit_x)
        left = int(min(max(centre - max_width / 2, 0), img.width - max_width))
        img = img.crop((left, 0, left + max_width, img.height))
    return img, shadow, light


def bust_tones(img, max_width=HERO_MAX_WIDTH):
    """Half-size tone grid of a bust: every 2x2 block of the Desktop image becomes one
    square pixel holding its mean tone, 0 (empty) to 2 (fully lit). A bust wider than
    `max_width` keeps the columns around its light, where the face is.
    Returns (tones, shadow_ink, light_ink)."""
    img, shadow, light = crop_bust(img, max_width)
    src = img.load()
    tones = [[sum(0 if not src[2 * x + dx, 2 * y + dy][3] else 2 if src[2 * x + dx, 2 * y + dy][:3] == light else 1
                  for dx in (0, 1) for dy in (0, 1)) / 4
              for x in range(img.width // 2)] for y in range(img.height // 2)]
    return tones, shadow, light


def half_block_rows(levels):
    """Pair pixel rows into terminal rows of (upper, lower) level pairs, dropping empty rows at either end."""
    if len(levels) % 2:
        levels = levels + [[0] * len(levels[0])]
    rows = [list(zip(levels[y], levels[y + 1])) for y in range(0, len(levels), 2)]
    while rows and not any(a or b for a, b in rows[0]):
        rows.pop(0)
    while rows and not any(a or b for a, b in rows[-1]):
        rows.pop()
    return rows


def blocks(img, ground, max_width=HERO_MAX_WIDTH):
    """One-colour half-block bust, as rows of (colour, text): what the TUI can paint.

    Half-lit pixels are solid; dense shadow forms a checkerboard and thin shadow stays
    dark, so the faces' light leads the way it does on Desktop.
    """
    tones, _, light = bust_tones(img, max_width)
    levels = [[1 if t >= 1 or (t >= 0.75 and (x + y) % 2 == 0) else 0 for x, t in enumerate(row)]
              for y, row in enumerate(tones)]
    glyphs = {(1, 1): "█", (1, 0): "▀", (0, 1): "▄", (0, 0): " "}
    colour = readable(hexc(light), ground, 4.5)
    return [(colour, "".join(glyphs[cell] for cell in row).rstrip() or " ") for row in half_block_rows(levels)]


def pixel_levels(img, cols=ART_COLUMNS, max_width=ART_MAX_WIDTH, on_ground=False):
    """The bust redrawn at `cols` pixels wide as levels 0 (empty), 1 (shadow), 2 (mid), 3 (light).

    The Desktop image is already dithered, so scaling it straight down gives noise.
    Its tone is rebuilt first (lit 1, shadow 0.43, lightly blurred), averaged down
    by area, then dithered again onto the three tones (Floyd-Steinberg), so the
    shading keeps its detail at the smaller size the way the Desktop dither does.

    `on_ground`: the art of a light theme is dark ink on the theme's ground, its lit
    marble left transparent. Then the ground is the light (level 3), its light ink
    the mid tone and its shadow ink the shadow, and nothing is empty.
    """
    img, shadow, light = crop_bust(img, max_width)
    src = img.load()
    tone, alpha = Image.new("L", img.size), Image.new("L", img.size)
    t_out, a_out = tone.load(), alpha.load()
    for y in range(img.height):
        for x in range(img.width):
            r, g, b, a = src[x, y]
            if on_ground:
                t_out[x, y] = 255 if not a else 150 if (r, g, b) == light else 40
            else:
                t_out[x, y] = 0 if not a else 255 if (r, g, b) == light else 110
            a_out[x, y] = 255 if a or on_ground else 0
    height = round(img.height * cols / img.width)
    tone = tone.filter(ImageFilter.GaussianBlur(0.6)).resize((cols, height), Image.BOX)
    a = alpha.resize((cols, height), Image.BOX).load()
    values = [[tone.getpixel((x, y)) / 255 for x in range(cols)] for y in range(height)]
    levels = [[0] * cols for _ in range(height)]
    for y in range(height):
        for x in range(cols):
            if a[x, y] < 110:
                continue
            level = min(DITHER_TONES, key=lambda k: abs(DITHER_TONES[k] - values[y][x]))
            levels[y][x] = level
            error = values[y][x] - DITHER_TONES[level]
            for dx, dy, weight in ((1, 0, 7), (-1, 1, 3), (0, 1, 5), (1, 1, 1)):
                if 0 <= x + dx < cols and y + dy < height:
                    values[y + dy][x + dx] += error * weight / 16
    return levels, shadow, light


def pixel_art(img, ground, theme_ground, cols=ART_COLUMNS, max_width=ART_MAX_WIDTH):
    """Three-tone half-block bust as Rich markup: the Desktop shadow and light inks and
    a mid tone between them, two pixels per cell (upper as foreground, lower as background).
    A cell filled by one tone is a space on that background, not a full block: some
    terminals (xterm.js, as in Orca) leave a hairline between block glyphs, never
    between backgrounds. Several colours per line, so only the classic CLI can paint
    it (`banner_art`). A light theme's bust sits on `theme_ground`, as on Desktop."""
    on_ground = luminance(rgb(theme_ground)) >= 0.5
    levels, shadow, light = pixel_levels(img, cols, max_width, on_ground)
    if on_ground:  # a framed picture: the inks as drawn, no contrast nudging against the terminal
        ink = {1: hexc(shadow), 2: hexc(light), 3: theme_ground}
    elif shadow == light:  # a one-ink image (Nyx): shade towards the terminal ground instead
        low, mid = mix(ground, hexc(light), 0.35), mix(ground, hexc(light), 0.65)
    else:
        low, mid = hexc(shadow), mix(hexc(shadow), hexc(light), 0.5)
    if not on_ground:
        ink = {1: readable(low, ground, 1.6), 2: readable(mid, ground, 2.5), 3: readable(hexc(light), ground, 4.5)}
    lines = []
    for row in half_block_rows(levels):
        runs = []  # [style, text] with consecutive cells of one style merged
        for upper, lower in row:
            if upper == lower:
                style, glyph = (f"on {ink[upper]}" if upper else None), " "
            elif not lower:
                style, glyph = ink[upper], "▀"
            elif not upper:
                style, glyph = ink[lower], "▄"
            else:
                style, glyph = f"{ink[upper]} on {ink[lower]}", "▀"
            if runs and runs[-1][0] == style:
                runs[-1][1] += glyph
            else:
                runs.append([style, glyph])
        while runs and runs[-1][0] is None:
            runs.pop()
        lines.append("".join(text if style is None else f"[{style}]{text}[/]" for style, text in runs) or " ")
    return "\n".join(lines)


def markup(rows):
    return "\n".join(f"[{colour}]{text}[/]" for colour, text in rows)


def lettering(theme, ground):
    stops = [readable(stop(s), ground, 4.5) for s in theme["vars"]["wordmark"]]
    # Top rows take the first stop, bottom rows the last, like the Desktop gradient.
    picks = [stops[i * len(stops) // len(LETTERING)] for i in range(len(LETTERING))]
    return "\n".join(f"[bold {colour}]{line}[/]" for colour, line in zip(picks, LETTERING))


# ── YAML output ─────────────────────────────────────────────────────────────

def block(key, text, indent="  "):
    # Explicit indentation: art lines may start with spaces, which would set it otherwise.
    return f"{key}: |{len(indent)}-\n" + "\n".join(indent + line for line in text.splitlines())


def mapping(key, values):
    return f"{key}:\n" + "\n".join(f'  {k}: "{v}"' for k, v in values.items())


def skin_yaml(theme):
    logo = lettering(theme, DARK_GROUND)
    hero = markup(blocks(decode(theme["art"]["top"]), DARK_GROUND))
    description = f"Pantheon: {theme['description']}"
    return "\n".join([
        "# Generated by cli/build_skins.py from desktop/plugin.js. Do not edit by hand.",
        f"name: {PREFIX}{theme['name']}",
        f'description: "{description}"',
        mapping("colors", palette(theme, DARK_GROUND)),
        mapping("light_colors", palette(theme, LIGHT_GROUND)),
        block("banner_logo", logo),
        block("banner_hero", hero),
        block("banner_art", pixel_art(decode(theme["art"]["top"]), DARK_GROUND, theme["colors"]["background"])),
        block("banner_thumb", pixel_art(decode(theme["art"]["top"]), DARK_GROUND, theme["colors"]["background"],
                                        cols=THUMB_COLUMNS)),
        "",
    ])


def themes():
    src = (ROOT / "desktop" / "plugin.js").read_text(encoding="utf-8")
    start = src.index("const THEMES = ") + len("const THEMES = ")
    end = src.index("\n]\n", start) + 2
    return json.loads(src[start:end])


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for theme in themes():
        if not re.fullmatch(r"[a-z]+", theme["name"]):
            raise SystemExit(f"unexpected theme name: {theme['name']!r}")
        (OUT / f"{PREFIX}{theme['name']}.yaml").write_text(skin_yaml(theme), encoding="utf-8")
        print(f"cli/skins/{PREFIX}{theme['name']}.yaml")


if __name__ == "__main__":
    main()
