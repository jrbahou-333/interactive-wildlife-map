# Interactive Wildlife Map

A casual, discover-first map of what wildlife you can spot where. Mobile-first PWA,
seeded from open biodiversity data (GBIF / NBN Atlas). UK fauna only (no plants);
first build covers mammals and birds around Crosby, Merseyside. Add more animal
classes via `GROUPS` in `config.py`.

## Architecture

```
Python pipeline   ->   static data   ->   MapLibre frontend (JS)
(fetch/clean/        (GeoJSON/JSON)       (the map UI)
 hexbin/enrich)
```

The Python side does all the heavy lifting; the JS side is a thin, fast map view.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Pipeline

1. Set your study area in `config.py` (the bounding box).
2. `python -m pipeline.fetch` — pull raw sightings from GBIF into `data/raw/`.
3. `python -m pipeline.process` — clean + filter to the target class, coarsen
   sensitive records, write map-ready GeoJSON.
4. `python -m pipeline.enrich` — add common names + photos (pulled from the
   `media` GBIF already returned) and write the points GeoJSON for the map.
5. `python -m pipeline.aggregate` — bin the points into H3 hexagons and write
   `web/data/hexes.geojson` (the zoomed-out "hotspots", with a summary per hex).

The map shows wildlife as shaded H3 hexagons; click one for a panel of its top
species (each card expands to show key facts). `HEX_RESOLUTION` in `config.py`
controls hexagon size.

(Later: backfilling common names via the GBIF species API for any the
occurrence records didn't already carry; multiple hex resolutions.)

## Notes / responsibilities

- Sensitive/protected species locations must be coarsened — handled in `process.py`.
- Data is CC BY-NC etc.: keep the app free + attribute sources.
