"""
Step 3 of the pipeline: add the human-facing extras (common name + photo) that
make the map nice to browse.

Input:  data/processed/<group>.parquet   (cleaned records from process.py —
                                          still carries GBIF's 'media' + names)
Output: web/data/<group>.geojson         (same points, now with commonName,
                                          image and imageCredit properties)

Why a separate step from process.py?
  process.py is about *geometry and cleaning* (which records are valid, where
  they are). enrich.py is about *presentation* (what the user reads/sees in a
  popup). Keeping them apart means we can re-run the cosmetic step without
  re-cleaning, and the cleaning logic stays uncluttered.

Nice surprise: GBIF's occurrence search already returned photo URLs inline in
the 'media' field, so we do NOT need a second API call to get images. We just
reshape what we already fetched.

Run with:  python -m pipeline.enrich
"""

import json
import time

import pandas as pd
import requests

import config

# Wikipedia asks API users to identify themselves with a User-Agent.
WIKI_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"
WIKI_HEADERS = {"User-Agent": "interactive-wildlife-map/0.1 (educational project)"}

# Disk cache of Wikipedia results (species -> {image, title}) so re-runs of the
# pipeline don't re-fetch every species. Delete this file to force a refresh.
WIKI_CACHE = config.DATA_RAW.parent / "wiki_cache.json"  # i.e. data/wiki_cache.json

# Shown when a sighting has no photo of its own AND Wikipedia has none either.
# A local file (created alongside this pipeline) so it always loads.
PLACEHOLDER_IMAGE = "img/no-photo.svg"


def load_processed() -> pd.DataFrame:
    """Read the cleaned records process.py wrote (these still include the raw
    'media' JSON and 'vernacularName', which it carried through but didn't put
    into the map file)."""
    path = config.DATA_PROCESSED / f"{config.OUTPUT_NAME}.parquet"
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found — run `python -m pipeline.process` first")
    return pd.read_parquet(path)


def smaller_image_url(url: str) -> str:
    """Swap a full-size photo URL for a smaller variant so hover is snappy.

    iNaturalist (the source of most of our photos) serves several sizes at
    predictable paths, e.g. .../photos/123/original.jpg -> .../medium.jpg.
    For any other host we just return the URL unchanged.
    """
    if "inaturalist" in url and "/original." in url:
        return url.replace("/original.", "/medium.")
    return url


def pick_image(media_json: str | None) -> tuple[str | None, str | None]:
    """From GBIF's 'media' blob (a JSON string of a list of media items), pick
    the first still image and return (image_url, credit_line).

    Returns (None, None) when a record has no usable photo.
    """
    if not media_json:
        return None, None
    try:
        items = json.loads(media_json)
    except (ValueError, TypeError):
        return None, None

    for item in items:
        # We only want still photos with an actual file URL ('identifier').
        is_image = item.get("type") == "StillImage" or str(item.get("format", "")).startswith("image/")
        url = item.get("identifier")
        if is_image and url:
            # Build a short attribution line. The photo licences are CC, which
            # require crediting the photographer — keep this with the image.
            who = item.get("creator") or item.get("rightsHolder") or "Unknown"
            where = item.get("publisher")
            credit = f"© {who}" + (f" / {where}" if where else "")
            return smaller_image_url(url), credit

    return None, None


def build_name_map(df: pd.DataFrame) -> dict[str, str]:
    """Map each species -> its common name.

    GBIF fills 'vernacularName' inconsistently: one Roe Deer record has it,
    the next (often the one with the photo) doesn't. So we collect every name
    that appears for a species and apply it to ALL of that species' records.
    This turns ~84 named records into full coverage for every named species.
    """
    name_map: dict[str, str] = {}
    for species, name in zip(df["species"], df["vernacularName"]):
        if species not in name_map and isinstance(name, str) and name.strip():
            name_map[species] = name.strip()
    return name_map


def wiki_lookup(scientific_name: str, retries: int = 3) -> tuple[str | None, str | None]:
    """Look up a species on Wikipedia, retrying transient failures.

    We query by scientific name ("Vulpes vulpes"), which reliably redirects to
    the right article. Returns (thumbnail_url, page_title) — the title is a
    decent common name ("Red fox") we can fall back to. Either may be None.

    With ~150 species we sometimes get rate-limited or a flaky connection, so we
    retry a few times with a short backoff. A genuine 404 (no article) is final.
    """
    url = WIKI_SUMMARY + scientific_name.replace(" ", "_")
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=WIKI_HEADERS, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                image = (data.get("thumbnail") or {}).get("source")
                # Only treat the title as a common name if it's NOT just the
                # scientific name echoed back (Wikipedia returns the latinate
                # title when there's no English article).
                title = data.get("title")
                if title and title.lower() == scientific_name.lower():
                    title = None
                return image, title
            if resp.status_code == 404:
                return None, None  # no such article — don't bother retrying
        except requests.RequestException:
            pass
        time.sleep(1 + attempt)  # back off, then retry (handles 429 / flaky net)
    return None, None


def _load_cache() -> dict[str, dict]:
    if WIKI_CACHE.is_file():
        return json.loads(WIKI_CACHE.read_text())
    return {}


def build_species_wiki(species: list[str]) -> dict[str, dict]:
    """Fetch one representative photo + common name per unique species.

    Results are cached to disk (data/wiki_cache.json) so re-running the pipeline
    doesn't hammer Wikipedia again — only species we don't already have are
    fetched. Failed lookups aren't cached, so a later run retries them.
    """
    cache = _load_cache()
    unique = sorted(set(species))
    todo = [s for s in unique if s not in cache]
    print(f"Wikipedia: {len(unique) - len(todo)} cached, fetching {len(todo)}...")

    for name in todo:
        image, title = wiki_lookup(name)
        if image or title:                       # only cache successes
            cache[name] = {"image": image, "title": title}
        print(f"  wiki for {name}: photo={'ok' if image else 'none'} name={title or '-'}")
        time.sleep(0.2)  # be gentle on Wikipedia's free API

    WIKI_CACHE.write_text(json.dumps(cache, indent=2))
    # Return an entry for every requested species (missing ones come back empty).
    info = {name: cache.get(name, {"image": None, "title": None}) for name in unique}
    return info


def to_geojson(df: pd.DataFrame) -> dict:
    """Build the enriched GeoJSON FeatureCollection for the web app."""
    name_map = build_name_map(df)
    # One Wikipedia lookup per species, reused for stock photos AND as a common
    # name fallback (GBIF's vernacularName is patchy, especially after de-dup).
    print("Fetching photos + names from Wikipedia...")
    wiki = build_species_wiki(list(df["species"]))

    features = []
    for row in df.itertuples(index=False):
        month = int(row.month) if pd.notna(row.month) else None
        year = int(row.year) if pd.notna(row.year) else None

        # Common name: GBIF's vernacularName (per species), else the Wikipedia
        # page title, else None (the frontend then shows the scientific name).
        common = name_map.get(row.species) or wiki[row.species]["title"]

        # Image priority:
        #   1. this record's own GBIF observation photo (the real sighting),
        #   2. a representative species photo from Wikipedia (a stock image),
        #   3. a local placeholder so EVERY sighting has something to show.
        image, credit = pick_image(getattr(row, "media", None))
        is_stock = False
        if not image:
            image = wiki[row.species]["image"]
            if image:
                credit = "Representative photo · Wikimedia Commons"
                is_stock = True
            else:
                image = PLACEHOLDER_IMAGE
                credit = None
                is_stock = True

        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [row.decimalLongitude, row.decimalLatitude],
            },
            "properties": {
                "scientificName": row.species,
                "commonName": common,
                "group": row.group,
                "image": image,             # always set now
                "imageCredit": credit,      # attribution line, or None
                "imageIsStock": is_stock,   # True = not the actual sighting's photo
                "month": month,
                "year": year,
                "detections": int(row.detections),  # times seen at this spot
                "sensitive": bool(row.sensitive),
                "gbifKey": int(row.key) if pd.notna(row.key) else None,
            },
        })
    return {"type": "FeatureCollection", "features": features}


def main():
    df = load_processed()
    geojson = to_geojson(df)

    out = config.WEB_DATA / f"{config.OUTPUT_NAME}.geojson"
    out.write_text(json.dumps(geojson))

    # A quick summary so you can sanity-check the enrichment immediately.
    props = [f["properties"] for f in geojson["features"]]
    n = len(props)
    real_photo = sum(1 for p in props if not p["imageIsStock"])
    stock_photo = sum(1 for p in props if p["imageIsStock"] and p["image"] != PLACEHOLDER_IMAGE)
    placeholder = sum(1 for p in props if p["image"] == PLACEHOLDER_IMAGE)
    with_name = sum(1 for p in props if p["commonName"])
    print(f"Enriched {n} sightings -> {out}")
    print(f"  own GBIF photo:       {real_photo}")
    print(f"  stock species photo:  {stock_photo}")
    print(f"  placeholder fallback: {placeholder}")
    print(f"  with a common name:   {with_name}")


if __name__ == "__main__":
    main()
