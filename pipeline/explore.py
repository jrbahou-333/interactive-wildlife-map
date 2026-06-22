"""
A throwaway exploration script: test the GBIF connection and report what the
data actually looks like (the "schema").

Run with:  python -m pipeline.explore
"""

import json

from pygbif import occurrences

import config
from pipeline.fetch import build_wkt_polygon


def main():
    geometry = build_wkt_polygon()
    print(f"Testing GBIF connection for: {config.AREA_NAME}\n")

    # Ask for just ONE page of 5 records — enough to inspect the schema, fast.
    resp = occurrences.search(
        geometry=geometry,
        taxonKey=config.ANIMALIA_TAXON_KEY,
        hasCoordinate=True,
        hasGeospatialIssue=False,
        limit=5,
    )

    # 1) Connection + how many records exist in total for this area/filters
    print("CONNECTION OK")
    print(f"Total matching records available: {resp['count']:,}")
    print(f"Records returned this page:       {len(resp['results'])}\n")

    if not resp["results"]:
        print("No records — check the bounding box in config.py")
        return

    # 2) The schema: every field name present on the first record
    first = resp["results"][0]
    print(f"SCHEMA — {len(first)} fields on a single occurrence record:")
    for field in sorted(first.keys()):
        # show the field name and the python type of its value
        print(f"  {field:<32} ({type(first[field]).__name__})")

    # 3) A full sample record, pretty-printed, so you see real values
    print("\nSAMPLE RECORD (first result):")
    print(json.dumps(first, indent=2, default=str)[:2000])


if __name__ == "__main__":
    main()
