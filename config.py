"""
Central configuration for the wildlife map pipeline.

Everything that you might want to tweak lives here so the scripts
(fetch.py, process.py, ...) stay generic and reusable.
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# Project paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).parent
DATA_RAW = ROOT / "data" / "raw"            # untouched data straight from GBIF
DATA_PROCESSED = ROOT / "data" / "processed"  # cleaned data (parquet, for inspection)
WEB_DATA = ROOT / "web" / "data"            # GeoJSON the web app actually loads

# Make sure those folders exist (creates them on first import; harmless if present)
DATA_RAW.mkdir(parents=True, exist_ok=True)
DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
WEB_DATA.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Study area  <-- CHANGE THIS to your local area
# ---------------------------------------------------------------------------
# A bounding box is just two corners: south-west and north-east.
# The default below is roughly the Berkhamsted / Ashridge area in Hertfordshire
# (the place I used in the mockup). Tell me your area and I'll set these for you,
# or look up a box yourself at https://boundingbox.klokantech.com (pick "CSV" format).
AREA_NAME = "Crosby, Merseyside"
# Box covers Crosby and surrounds: the estuary coast to the west,
# Waterloo/Seaforth to the south, Hightown/dunes to the north, inland to the east.
MIN_LON, MIN_LAT = -3.10, 53.45   # south-west corner (lon, lat)
MAX_LON, MAX_LAT = -2.93, 53.54   # north-east corner (lon, lat)

# ---------------------------------------------------------------------------
# What to fetch
# ---------------------------------------------------------------------------
# GBIF taxon key 1 = kingdom Animalia. We only want fauna for now.
ANIMALIA_TAXON_KEY = 1

# ---------------------------------------------------------------------------
# Processing options (used by process.py)
# ---------------------------------------------------------------------------
# Which animal classes to map (keys match GBIF's 'class' field), each mapped to
# a friendly group label. The label drives the popup text AND which icon is
# shown — see GROUP_ICONS in web/app.js. Add a line here (e.g.
# "Amphibia": "Amphibians") to include another group.
GROUPS = {
    "Mammalia": "Mammals",
    "Aves": "Birds",
    "Amphibia": "Amphibians",
    "Reptilia": "Reptiles",
}

# Name of the combined output files: web/data/<OUTPUT_NAME>.geojson, etc.
OUTPUT_NAME = "sightings"

# Drop records whose location is fuzzier than this many metres. Records with no
# stated uncertainty are kept (very common — absence of the field isn't a fault).
MAX_COORD_UNCERTAINTY_M = 5000

# Non-wild / unwanted species to drop (domestic animals, etc.).
EXCLUDE_SPECIES = ["Felis catus"]  # domestic cat

# Sensitive records get their coordinates rounded to this many decimal places
# (~1 km at 2 dp) so we never publish a precise location for a protected species.
SENSITIVE_COORD_DECIMALS = 2

# Safety cap. GBIF's occurrence search gets slow/flaky at deep offsets, so we
# keep paging shallow. 10k records is plenty for a local MVP map.
MAX_RECORDS = 10_000

# ---------------------------------------------------------------------------
# Aggregation options (used by aggregate.py + the map)
# ---------------------------------------------------------------------------
# We bin sightings into H3 hexagons at MULTIPLE resolutions so the map can show
# large regional hexes when zoomed out and fine neighbourhood hexes when zoomed in.
# Each entry maps an H3 resolution to the MapLibre zoom range [minzoom, maxzoom)
# where it's visible.  Approximate hex sizes for reference:
#   res 3 ~41 km edge  (UK fits in ~6-8 hexes)
#   res 5 ~6 km edge   (county-sized bins)
#   res 7 ~0.9 km edge (neighbourhood-sized)
#   res 8 ~0.46 km edge (street-level detail)
HEX_RESOLUTIONS = {
    3: (0, 8),     # zoomed way out: very few, large hexes
    5: (8, 10),    # medium: roughly council-area-sized
    7: (10, 12),   # zoomed in: neighbourhood scale
    8: (12, 24),   # max detail: the original resolution
}
# Keep a single-resolution alias for any code that still references it.
HEX_RESOLUTION = 8
