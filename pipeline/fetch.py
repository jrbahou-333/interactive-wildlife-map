"""
Step 1 of the pipeline: pull raw wildlife sightings from GBIF for our area.

GBIF (Global Biodiversity Information Facility) aggregates hundreds of millions
of species "occurrence" records. The UK's NBN Atlas data flows into it too, so
GBIF is a convenient single source for the MVP.

Run with:  python -m pipeline.fetch
(the "-m" form lets the script import config.py from the project root)
"""

import json
import socket
import sys
import time

import pandas as pd
from pygbif import occurrences

import config

# pygbif doesn't set an HTTP timeout, so a stalled connection would hang forever.
# Setting a default socket timeout makes any blocked network read raise instead.
socket.setdefaulttimeout(30)

# These are the only columns we care about. GBIF returns ~100 fields per record;
# keeping just these makes the file small and the next step simpler.
KEEP_COLUMNS = [
    "key",              # unique GBIF id for the record
    "scientificName",   # e.g. "Vulpes vulpes"
    "vernacularName",   # common name, e.g. "Red Fox" (often missing — we'll fix later)
    "kingdom", "phylum", "class", "order", "family", "genus", "species",
    "decimalLatitude", "decimalLongitude",
    "coordinateUncertaintyInMeters",  # how fuzzy the location is
    "eventDate",        # when it was seen
    "basisOfRecord",    # e.g. HUMAN_OBSERVATION
    "iucnRedListCategory",  # conservation status, e.g. LC / NT / EN
    "informationWithheld",  # set when GBIF deliberately coarsened a sensitive record
    "license",          # per-record licence, drives attribution
    "media",            # list of photos (+ their own licences) — flattened below
]


def build_wkt_polygon() -> str:
    """Turn our bounding box into the WKT polygon string GBIF expects.

    WKT ("Well-Known Text") describes shapes as text. A box is a closed ring of
    5 points (the last repeats the first). GBIF wants lon/lat pairs, counter-
    clockwise.
    """
    w, s, e, n = config.MIN_LON, config.MIN_LAT, config.MAX_LON, config.MAX_LAT
    return (
        f"POLYGON(({w} {s}, {e} {s}, {e} {n}, {w} {n}, {w} {s}))"
    )


def search_page(geometry: str, offset: int, page_size: int, retries: int = 3):
    """One GBIF search request, retried a few times if the network hiccups."""
    for attempt in range(1, retries + 1):
        try:
            return occurrences.search(
                geometry=geometry,
                taxonKey=config.ANIMALIA_TAXON_KEY,  # animals only
                hasCoordinate=True,                  # must have a location
                hasGeospatialIssue=False,            # skip records flagged as dodgy
                limit=page_size,
                offset=offset,
            )
        except Exception as exc:  # timeout, connection reset, etc.
            wait = 2 * attempt  # back off a little longer each time
            print(f"\n  request failed ({exc}); retry {attempt}/{retries} in {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"GBIF request failed {retries} times at offset {offset}")


def fetch() -> pd.DataFrame:
    """Page through GBIF results for our area and return them as a DataFrame."""
    geometry = build_wkt_polygon()
    print(f"Fetching animal sightings for: {config.AREA_NAME}")
    print(f"Bounding box WKT: {geometry}")
    print(f"Cap: {config.MAX_RECORDS:,} records\n", flush=True)

    records = []
    offset = 0
    page_size = 300  # GBIF's maximum page size

    while offset < config.MAX_RECORDS:
        resp = search_page(geometry, offset, page_size)

        batch = resp.get("results", [])
        if not batch:
            break  # no more data

        records.extend(batch)
        offset += page_size
        # Print a fresh line every ~10 pages with flush=True so progress is
        # visible even when output is redirected to a file (not a terminal).
        if offset % (page_size * 10) == 0:
            print(f"  fetched {len(records):,} records...", flush=True)

        # GBIF tells us when we've reached the last page.
        if resp.get("endOfRecords"):
            break

        time.sleep(0.2)  # be polite to the free API

    print(f"Done. {len(records):,} raw records pulled.")

    if not records:
        print("No records found — is your bounding box right in config.py?")
        sys.exit(1)

    # Build a DataFrame, then keep only the columns we listed (any missing ones
    # are created as empty so the schema is always consistent).
    df = pd.DataFrame(records)
    for col in KEEP_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA

    # 'media' arrives as a list of dicts (nested), which Parquet can't store
    # cleanly. Serialise it to a JSON string now; we can json.loads() it back
    # in the enrich step when we want the photo URLs.
    df["media"] = df["media"].apply(
        lambda m: json.dumps(m, default=str) if isinstance(m, list) and m else None
    )

    return df[KEEP_COLUMNS]


def main():
    df = fetch()
    out = config.DATA_RAW / "occurrences.parquet"
    df.to_parquet(out, index=False)
    print(f"Saved -> {out}")
    print("\nQuick peek at what we got:")
    # A small summary so you can sanity-check the data immediately.
    print(df["class"].value_counts().head(10))


if __name__ == "__main__":
    main()
