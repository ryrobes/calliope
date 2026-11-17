# Listing honesty: does the photo back up the seller?

A small demo of Jev looking at images from SQL. Fifteen second-hand marketplace
listings, each a seller's description plus one photo; nine photos contradict
their description ("pet-free home" with a dog asleep on the sofa, "flawless
screen" on a shattered phone).

| File | What it is |
|---|---|
| `listings.json` | The listings: title, price, the seller's claim, the scene for the photo, and `honest` as the answer key (not loaded into the table). |
| `make_listings.py` | Generates the photos with an OpenRouter image model and prints `listings.sql` (schema `marketplace`). About $1 for fifteen photos. |
| `listing_verdict.sql` | A three-step operator: Jev 1.14 judges honest / condition / flaw and boxes the flaw in one call, an LLM writes a one-line verdict, SQL assembles the result. |
| `queries.sql` | The three demo queries, simplest first. |

## Run it

```bash
OPENROUTER_API_KEY=... python make_listings.py > listings.sql
psql -d rvbbit -f listings.sql
psql -d rvbbit -f listing_verdict.sql
```

Then paste `queries.sql` into DataRabbit's SQL Scratch. The first run calls the
models (about 4 s per listing for the chained operator, mostly the LLM step);
every later run reads the semantic cache.

## What it shows

- `rvbbit.image_part(photo)` turns a JPEG/PNG data URL (or raw bytes) into an
  image Jev can read; put it in a state array next to text.
- `truth`, `choose` and `clover_decide` work on images unchanged.
  `locate(state, target)` (Jev 1.14) returns `{top,bottom,left,right,confidence}`.
  A box always comes back, even when nothing is wrong, so the operator only
  keeps it when the listing looks dishonest, worn or damaged.
- In DataRabbit a column aliased `photo__box` outlines the `photo` thumbnail in
  the same row; `honest__heat` tints the true/false column.
- Semantic → Operators → `listing_verdict` shows the chain as nodes, with every
  run's inputs, outputs, latency and cost.

On the lab install all fifteen came out right with both `truth()` (Jev 1.13)
and the operator (Jev 1.14): liars scored 0.01–0.23, honest listings 0.61–0.85.
Boxes land well on a single obvious flaw (a stain, a cracked screen, a dent);
with two similar objects in one photo they sometimes pick the wrong one.
