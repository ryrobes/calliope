"""Bounded evidence and question types shared by the hosted decision adapter."""

import base64
import binascii
import json
import re
import struct
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    model_validator,
)

def nonblank(value):
    if not value.strip():
        raise ValueError("Names must not be blank")
    return value


Name = Annotated[
    str, StringConstraints(min_length=1, max_length=80), AfterValidator(nonblank)
]
Text = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Choice(StrictModel):
    type: Literal["choice"]
    instructions: Text
    criteria: dict[Name, Text] = Field(min_length=2, max_length=16)


class BooleanCriteria(StrictModel):
    # Python keywords are represented by aliases on the wire.
    false_: Text = Field(alias="false")
    true_: Text = Field(alias="true")


class Noul(StrictModel):
    type: Literal["noul"]
    instructions: Text
    criteria: BooleanCriteria | None = None


class Box(StrictModel):
    """Where the described target sits in the image, as fractions of its size."""
    type: Literal["box"]
    instructions: Text


Question = Annotated[Choice | Noul | Box, Field(discriminator="type")]

# Images ride in state as chat-style parts. The provider accepts base64 PNG or
# JPEG data URLs only, 256..1568 px per side; check here so SQL callers get a
# clear 422 instead of an opaque provider rejection.
IMAGE_TYPES = ("image/png", "image/jpeg")
MAX_IMAGES = 8
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_TOTAL = 24 * 1024 * 1024
SIDE = (256, 1568)
PIXELS = (78_400, 2_458_624)
_DATA_URL = re.compile(r"data:(image/[a-z]+);base64,([A-Za-z0-9+/]+={0,2})", re.S)


def is_image_part(value):
    return isinstance(value, dict) and value.get("type") == "image_url"


def image_size(kind, data):
    """Width and height from the PNG IHDR or the first JPEG frame header."""
    if kind == "image/png":
        if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
            raise ValueError("image is not a valid PNG")
        first_frame = data.find(b"IDAT")
        if b"acTL" in data[:first_frame if first_frame > 0 else len(data)]:
            raise ValueError("animated PNG images are not supported")
        return struct.unpack(">II", data[16:24])
    if data[:2] != b"\xff\xd8":
        raise ValueError("image is not a valid JPEG")
    at = 2
    while at + 4 <= len(data):
        if data[at] != 0xFF:
            at += 1
            continue
        marker = data[at + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            at += 2
            continue
        length = struct.unpack(">H", data[at + 2:at + 4])[0]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height, width = struct.unpack(">HH", data[at + 5:at + 9])
            return width, height
        at += 2 + length
    raise ValueError("image is not a valid JPEG")


def image_bytes(part):
    """Validate one image part and return its decoded size."""
    if set(part) - {"type", "image_url"} or not isinstance(part.get("image_url"), dict):
        raise ValueError("image parts are {type: image_url, image_url: {url}}")
    image = part["image_url"]
    if set(image) - {"url", "detail"} or image.get("detail", "auto") not in ("auto", "low", "high"):
        raise ValueError("image_url takes url and an optional detail of auto, low or high")
    match = _DATA_URL.fullmatch(image.get("url") if isinstance(image.get("url"), str) else "")
    if not match:
        raise ValueError("images must be base64 data URLs; remote URLs are not fetched")
    if match.group(1) not in IMAGE_TYPES:
        raise ValueError("images must be PNG or JPEG")
    try:
        data = base64.b64decode(match.group(2), validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("image data is not valid base64") from None
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("each image must be at most 8 MiB")
    width, height = image_size(match.group(1), data)
    if not (SIDE[0] <= width <= SIDE[1] and SIDE[0] <= height <= SIDE[1]
            and PIXELS[0] <= width * height <= PIXELS[1]):
        raise ValueError(f"image is {width}x{height}; each side must be 256..1568 px")
    return len(data)


def split_images(value):
    """The state with each image swapped for a marker, plus the image parts."""
    if is_image_part(value):
        return "[image]", [value]
    if isinstance(value, list):
        parts = [split_images(item) for item in value]
        return [text for text, _ in parts], [image for _, found in parts for image in found]
    if isinstance(value, dict):
        parts = {key: split_images(item) for key, item in value.items()}
        return {key: text for key, (text, _) in parts.items()}, [
            image for _, found in parts.values() for image in found]
    return value, []


class DecisionRequest(StrictModel):
    state: str | dict[str, JsonValue] | list[JsonValue]
    questions: dict[Name, Question] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def bound_state(self):
        if not self.state or (isinstance(self.state, str) and not self.state.strip()):
            raise ValueError("state must not be empty")
        text, images = split_images(self.state)
        if len(images) > MAX_IMAGES:
            raise ValueError(f"state carries {len(images)} images; send at most {MAX_IMAGES}")
        if sum(image_bytes(image) for image in images) > MAX_IMAGE_TOTAL:
            raise ValueError("images in one state must total at most 24 MiB")
        # The text bound ignores image payloads; they are bounded above.
        encoded = json.dumps(text, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode()) > 32768:
            raise ValueError("state exceeds 32768 bytes; split the evidence explicitly")
        return self
