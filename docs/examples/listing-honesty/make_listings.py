"""Generate the listing photos and write listings.sql for the honesty demo.

Each listing in listings.json has the seller's claim and a scene for its photo;
about half of the scenes contradict the claim. Photos come from an OpenRouter
image model, are resized into Jev's range (each side 256..1568 px) and stored
as JPEG data URLs, which DataRabbit shows as thumbnails.

    OPENROUTER_API_KEY=... python make_listings.py > listings.sql
    psql -d rvbbit -f listings.sql

Needs httpx and Pillow. Fifteen photos cost about one US dollar.
"""
import asyncio
import base64
import io
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx
from PIL import Image

MODEL = "google/gemini-3.1-flash-image"
STYLE = ("A realistic smartphone photo taken by a private seller for an online marketplace listing: {scene}. "
         "Natural, slightly imperfect home lighting, ordinary background, landscape orientation. "
         "No people, no faces, no text, no watermark, no captions.")


async def photo(client, limit, item):
    async with limit:
        response = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"]},
            json={"model": MODEL, "modalities": ["image", "text"],
                  "messages": [{"role": "user", "content": STYLE.format(scene=item["scene"])}]})
        response.raise_for_status()
        url = response.json()["choices"][0]["message"]["images"][0]["image_url"]["url"]
    image = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))).convert("RGB")
    scale = min(1.0, 1024 / max(image.size))
    image = image.resize((round(image.width * scale), round(image.height * scale)), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=82, optimize=True)
    print(f"listing {item['id']}: {len(buffer.getvalue()) // 1024} KB", file=sys.stderr)
    return item["id"], "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


async def main():
    listings = json.loads((Path(__file__).parent / "listings.json").read_text())
    limit = asyncio.Semaphore(4)
    async with httpx.AsyncClient(timeout=180) as client:
        photos = dict(await asyncio.gather(*(photo(client, limit, item) for item in listings)))
    rows = ",\n".join(
        f"({item['id']}, {literal(item['title'])}, {literal(item['description'])}, {item['price']}, "
        f"{literal(date(2026, 10, 1) + timedelta(days=item['id']))}, {literal(photos[item['id']])})"
        for item in listings)
    print(f"""BEGIN;
CREATE SCHEMA IF NOT EXISTS marketplace;
DROP TABLE IF EXISTS marketplace.listings;
CREATE TABLE marketplace.listings (
  id integer PRIMARY KEY,
  title text NOT NULL,
  description text NOT NULL,
  asking_price numeric(10,2) NOT NULL,
  listed_on date NOT NULL,
  photo text NOT NULL
);
COMMENT ON TABLE marketplace.listings IS 'Demo: second-hand marketplace listings, each with the seller''s description and one listing photo (JPEG data URL). Photos are AI-generated.';
COMMENT ON COLUMN marketplace.listings.photo IS 'Listing photo as a base64 JPEG data URL; pass to rvbbit.image_part() for Jev decisions.';
INSERT INTO marketplace.listings VALUES
{rows};
COMMIT;
-- Real row counts let EXPLAIN project the semantic calls exactly.
ANALYZE marketplace.listings;""")


if __name__ == "__main__":
    asyncio.run(main())
