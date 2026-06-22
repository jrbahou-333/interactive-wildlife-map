"""
Step 4 of the pipeline: bin sightings into H3 hexagons to make "hotspots".

Input:  web/data/<group>.geojson   (the enriched POINTS from enrich.py — each
                                    already has commonName + image)
Output: web/data/hexes.geojson     (hexagon POLYGONS, one per occupied cell,
                                    carrying a precomputed summary)

Why hexagons? H3 (Uber's grid) snaps every lat/lon to a hexagonal cell id at a
chosen resolution. Group the points by that id and you've got tidy, equal-area
"bins" — perfect for showing where wildlife clusters when the map is zoomed out.
Because we precompute each hex's summary here, the map's hover popup is trivial:
it just reads properties we already worked out.

Run with:  python -m pipeline.aggregate
"""

import json
from collections import defaultdict

import h3

import config


def load_points() -> list[dict]:
    """Load the enriched point features enrich.py wrote."""
    path = config.WEB_DATA / f"{config.OUTPUT_NAME}.geojson"
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found — run `python -m pipeline.enrich` first")
    return json.loads(path.read_text())["features"]


def hex_polygon(cell: str) -> dict:
    """Turn an H3 cell id into a GeoJSON Polygon for drawing.

    h3 gives the corners as (lat, lng) pairs; GeoJSON wants [lng, lat] and the
    ring must be closed (last point repeats the first).
    """
    ring = [[lng, lat] for lat, lng in h3.cell_to_boundary(cell)]
    ring.append(ring[0])
    return {"type": "Polygon", "coordinates": [ring]}


def aggregate(features: list[dict]) -> dict:
    """Group point features into hexagons and summarise each one."""
    # 1) Drop every point into its hexagon bucket.
    buckets: dict[str, list[dict]] = defaultdict(list)
    for f in features:
        lng, lat = f["geometry"]["coordinates"]  # GeoJSON order is [lng, lat]
        cell = h3.latlng_to_cell(lat, lng, config.HEX_RESOLUTION)
        buckets[cell].append(f["properties"])

    # 2) Summarise each occupied hexagon.
    hexes = []
    for cell, props in buckets.items():
        # Group this hex's sightings by display name (common, else scientific).
        by_species: dict[str, list[dict]] = defaultdict(list)
        for p in props:
            by_species[p["commonName"] or p["scientificName"]].append(p)

        # Build one entry per species: its name, group, how many sightings, and
        # a representative photo (preferring a real observation over a stock one
        # — sorting by imageIsStock puts False/real first).
        species = []
        for name, plist in by_species.items():
            plist.sort(key=lambda p: p.get("imageIsStock", True))
            species.append({
                "name": name,
                "group": plist[0]["group"],
                "count": len(plist),
                "image": plist[0]["image"],
            })
        species.sort(key=lambda s: s["count"], reverse=True)  # most-sighted first

        properties = {
            "hex": cell,
            "count": len(props),        # total sightings in this hex
            "richness": len(species),   # number of distinct species
            # Per-species breakdown for the hover card. Stored as a JSON string:
            # MapLibre hands back nested feature properties as strings anyway, so
            # the map JSON.parses this. Each entry is {name, group, count, image}.
            "species": json.dumps(species),
        }
        # Per-group totals (e.g. count_Mammals, count_Birds) — numeric props the
        # map's group filter sums to colour/hide hexes for the active groups.
        for s in species:
            key = f"count_{s['group']}"
            properties[key] = properties.get(key, 0) + s["count"]

        hexes.append({
            "type": "Feature",
            "geometry": hex_polygon(cell),
            "properties": properties,
        })

    return {"type": "FeatureCollection", "features": hexes}


def main():
    features = load_points()
    fc = aggregate(features)

    out = config.WEB_DATA / "hexes.geojson"
    out.write_text(json.dumps(fc))

    counts = [f["properties"]["count"] for f in fc["features"]]
    print(f"Aggregated {len(features)} sightings into {len(fc['features'])} "
          f"hexagons (H3 resolution {config.HEX_RESOLUTION}) -> {out}")
    if counts:
        print(f"  busiest hex: {max(counts)} sightings · quietest: {min(counts)}")


if __name__ == "__main__":
    main()
