"""Deterministic portrait accents, cached by image contents in agent identities."""
from __future__ import annotations

import colorsys
import hashlib
import io
import re
from typing import Callable

from PIL import Image, ImageOps

ALGORITHM = 'vibrant-v2'
COLOR_FIELDS = ('avatar_color', 'avatar_day_color', 'avatar_night_color')
HEX = re.compile(r'^#[0-9a-fA-F]{6}$')
FILENAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.(?:png|jpe?g|webp)$', re.I)


def extract(raw: bytes) -> str:
    """Prefer a substantial bright, saturated patch over backgrounds/highlights."""
    with Image.open(io.BytesIO(raw)) as source:
        image = ImageOps.exif_transpose(source).convert('RGBA')
        image.thumbnail((128, 128))
        # Favor the framing, clothes and decorative highlights over the central face.
        width, height = image.size
        pixels = [image.getpixel((x, y))[:3]
                  for y in range(height) for x in range(width)
                  if (x < width * .25 or x > width * .75 or y > height * .8)
                  and image.getpixel((x, y))[3] >= 192]
        if not pixels:
            pixels = [rgb[:3] for rgb in image.getdata() if rgb[3] >= 192]
    if not pixels:
        raise ValueError('Portrait has no opaque pixels')
    strip = Image.new('RGB', (len(pixels), 1))
    strip.putdata(pixels)
    quantized = strip.quantize(colors=48, method=Image.Quantize.MEDIANCUT)
    palette = quantized.getpalette()
    candidates = []
    neutral = []
    for count, index in quantized.getcolors():
        rgb = tuple(palette[index * 3:index * 3 + 3])
        _, saturation, value = colorsys.rgb_to_hsv(*(v / 255 for v in rgb))
        neutral.append((count ** .35 * value, rgb))
        if saturation >= .22 and value >= .45:
            score = count ** .25 * saturation ** 1.4 * value ** 2
            candidates.append((score, rgb))
    _, rgb = max(candidates or neutral)
    return '#' + ''.join(f'{v:02X}' for v in rgb)


def fields(identity: dict) -> dict[str, str]:
    return {key: value.upper() for key in COLOR_FIELDS
            if isinstance(value := identity.get(key), str) and HEX.fullmatch(value)}


def enrich(identity: dict, read_image: Callable[[str], bytes | None]) -> dict:
    """Preserve all unrelated identity metadata; regenerate only changed images."""
    result = dict(identity)
    cache = dict(identity.get('avatar_color_cache') or {})
    for image_field, color_field in [('avatar_file','avatar_color'),
                                     ('avatar_day_file','avatar_day_color'),
                                     ('avatar_night_file','avatar_night_color')]:
        filename = str(identity.get(image_field) or '')
        if not FILENAME.fullmatch(filename):
            continue
        raw = read_image(filename)
        if not raw:
            continue
        digest = hashlib.sha256(raw).hexdigest()
        entry = cache.get(filename) or {}
        if entry.get('sha256') != digest or entry.get('algorithm') != ALGORITHM or not HEX.fullmatch(str(entry.get('hex') or '')):
            entry = {'sha256':digest,'hex':extract(raw),'algorithm':ALGORITHM}
        cache[filename] = entry
        result[color_field] = entry['hex']
    if not identity.get('avatar_file') and result.get('avatar_day_color'):
        result['avatar_color'] = result['avatar_day_color']
    if cache:
        result['avatar_color_cache'] = cache
    return result
