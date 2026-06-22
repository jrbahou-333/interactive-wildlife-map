"""
Step 2 of the pipeline: turn raw GBIF records into clean, map-ready data.

Input:  data/raw/occurrences.parquet   (everything fetch.py pulled)
Output: data/processed/<group>.geojson (cleaned points for the map)
        data/processed/<group>.parquet (same data, handy for inspection)

We output GeoJSON because that's exactly what MapLibre wants to draw on a map.
A GeoJSON "FeatureCollection" is just a list of features, each a point with a
location plus any properties we attach (species name, month seen, etc.).

Run with:  python -m pipeline.process
"""

import json

import pandas as pd

import config


def load_raw() -> pd.DataFrame:
    path = config.DATA_RAW / "occurrences.parquet"
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found — run `python -m pipeline.fetch` first")
    return pd.read_parquet(path)


def clean(df: pd.DataFrame) -> pd.DataFrame:
    """Filter and tidy the raw records. Prints what each step removes so you can
    see the data shrinking and sanity-check it."""
    start = len(df)
    print(f"Starting with {start:,} raw records")

    # 1) Keep only the classes we're mapping, and tag each row with its group
    #    label (e.g. "Mammals", "Birds") for the app to use.
    df = df[df["class"].isin(config.GROUPS)].copy()
    df["group"] = df["class"].map(config.GROUPS)
    print(f"  in groups {list(config.GROUPS.values())}: {len(df):,}")

    # 2) Need a real species (genus-only records aren't useful on a species map).
    df = df[df["species"].notna() & (df["species"] != "")]
    print(f"  with a named species: {len(df):,}")

    # 3) Drop unwanted species (domestic animals, etc.).
    df = df[~df["species"].isin(config.EXCLUDE_SPECIES)]
    print(f"  after excluding {config.EXCLUDE_SPECIES}: {len(df):,}")

    # 4) Drop records whose location is too fuzzy. NaN uncertainty is kept on
    #    purpose — a missing value means "unknown", not "bad".
    too_fuzzy = df["coordinateUncertaintyInMeters"] > config.MAX_COORD_UNCERTAINTY_M
    df = df[~too_fuzzy]
    print(f"  within {config.MAX_COORD_UNCERTAINTY_M} m accuracy: {len(df):,}")

    # 5) Parse the date. 'coerce' turns unparseable dates into NaT (missing)
    #    rather than crashing. format="mixed" is essential: GBIF mixes date
    #    formats (full timestamps, minute-precision, date-only), and without it
    #    pandas locks onto ONE inferred format and silently NaTs all the rest.
    #    We keep month/year (for "best month" later) and a calendar 'day' for
    #    de-duplication below.
    dates = pd.to_datetime(df["eventDate"], errors="coerce", utc=True, format="mixed")
    df = df.assign(month=dates.dt.month, year=dates.dt.year, day=dates.dt.date)

    df = deduplicate(df)

    print(f"Kept {len(df):,} of {start:,} records ({df['species'].nunique()} species)")
    return df


def deduplicate(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse duplicate and repeated sightings into one marker per place.

    Two things inflate the raw counts:
      A) The SAME observation appears in two GBIF datasets, one with a full
         timestamp ("2025-03-27T10:41:09Z") and one date-only ("2025-03-27").
         Comparing the raw eventDate string misses these — comparing the
         calendar 'day' catches them.
      B) A fixed sensor (e.g. a hedgehog camera/footprint tunnel) records the
         same species at the SAME exact point on many days — 29 "sightings"
         that are really one spot. For a "what can I see where" map we want one
         marker per species per place.

    So we keep one row per (species, location), recording how many distinct
    days it was seen there as 'detections', and keep the most recent as the
    representative row.
    """
    before = len(df)

    # A) One row per species/place/day kills the timestamp-vs-date-only dupes.
    #    (Rows with no parseable day keep eventDate as a fallback key so we
    #    don't over-merge undated records.)
    day_key = df["day"].astype("string").fillna(df["eventDate"].astype("string"))
    df = df.assign(_daykey=day_key)
    df = df.drop_duplicates(subset=["species", "decimalLatitude", "decimalLongitude", "_daykey"])
    print(f"  after removing same-day duplicates: {len(df):,}")

    # B) Count distinct-day detections per (species, place), then keep just the
    #    most recent row for each so a fixed sensor becomes a single marker.
    place = ["species", "decimalLatitude", "decimalLongitude"]
    df = df.assign(detections=df.groupby(place)["_daykey"].transform("size"))
    df = df.sort_values("day", na_position="first")
    df = df.drop_duplicates(subset=place, keep="last").drop(columns=["_daykey"])
    print(f"  after collapsing repeat detections at one point: {len(df):,}")

    print(f"  (removed {before - len(df):,} duplicate/repeat rows)")
    return df


def coarsen_sensitive(df: pd.DataFrame) -> pd.DataFrame:
    """Protect sensitive species by blurring their exact location.

    GBIF sets 'informationWithheld' when a record was already coarsened upstream.
    We honour that, and round those coordinates ourselves as a safety net so a
    precise pin can never leak into the published map.
    """
    is_sensitive = df["informationWithheld"].notna() & (df["informationWithheld"] != "")
    n = int(is_sensitive.sum())
    df = df.assign(sensitive=is_sensitive)
    if n:
        dp = config.SENSITIVE_COORD_DECIMALS
        df.loc[is_sensitive, "decimalLatitude"] = df.loc[is_sensitive, "decimalLatitude"].round(dp)
        df.loc[is_sensitive, "decimalLongitude"] = df.loc[is_sensitive, "decimalLongitude"].round(dp)
        print(f"Coarsened {n} sensitive record(s) to {dp} dp (~1 km)")
    else:
        print("No records flagged sensitive by GBIF")
    return df


def to_geojson(df: pd.DataFrame) -> dict:
    """Build a GeoJSON FeatureCollection from the cleaned records."""
    features = []
    for row in df.itertuples(index=False):
        # month/year may be missing (NaT-derived NaN); convert to int or None.
        month = int(row.month) if pd.notna(row.month) else None
        year = int(row.year) if pd.notna(row.year) else None
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                # GeoJSON is always [longitude, latitude] — the reverse of how
                # we usually say "lat, lon". A classic source of bugs.
                "coordinates": [row.decimalLongitude, row.decimalLatitude],
            },
            "properties": {
                "scientificName": row.species,
                "group": row.group,
                "month": month,
                "year": year,
                "detections": int(row.detections),  # times seen at this spot
                "sensitive": bool(row.sensitive),
                "gbifKey": int(row.key) if pd.notna(row.key) else None,
                # commonName/photo get filled in by enrich.py later.
            },
        })
    return {"type": "FeatureCollection", "features": features}


def main():
    df = load_raw()
    df = clean(df)
    df = coarsen_sensitive(df)

    parquet_out = config.DATA_PROCESSED / f"{config.OUTPUT_NAME}.parquet"
    geojson_out = config.WEB_DATA / f"{config.OUTPUT_NAME}.geojson"  # next to the web app

    df.to_parquet(parquet_out, index=False)
    geojson_out.write_text(json.dumps(to_geojson(df)))

    print(f"\nSaved -> {parquet_out}")
    print(f"Saved -> {geojson_out}  ({len(df):,} points)")


if __name__ == "__main__":
    main()
