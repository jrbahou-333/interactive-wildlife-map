// ===========================================================================
// Crosby Wildlife Map — MapLibre GL
// ---------------------------------------------------------------------------
// The map shows wildlife as HOTSPOT HEXAGONS (aggregated by aggregate.py), not
// individual sightings. This file:
//   1. Creates a map.
//   2. Loads the hexagons as a "source" and draws them shaded by sighting count.
//   3. On CLICK, opens a pinned panel listing the hex's top species — each an
//      expandable card with a few key facts.
//   4. Offers a group filter (Mammals / Birds) that recolours/hides hexagons.
//
// Mental model: a SOURCE is the data; a LAYER is a drawing rule for that data.
//
// (We used to also draw an icon per individual observation. That was dropped —
// the per-species hex summary is the unit now. The points still exist in
// data/sightings.geojson if we ever want them back.)
// ===========================================================================

// The hexagon file aggregate.py wrote. Path is relative to index.html (/web/).
const HEX_URL = "data/hexes.geojson";

// [longitude, latitude] — MapLibre, like GeoJSON, always puts lon first.
const CROSBY = [-3.02, 53.49];

// Animal groups, each with an emoji used in the filter menu. Object.keys is
// also our list of group names. Add a group here (e.g. Amphibians: "🐸") and it
// flows through the filter automatically (given a matching count_<group> in the
// hex data).
const GROUP_ICONS = {
  Mammals: "🐇",
  Birds: "🐦",
};

// How many species cards to show per hexagon panel.
const TOP_SPECIES = 5;

// Green colour ramp for the hexagons, applied to whatever count expression we
// pass in — the total normally, or a filtered per-group sum when the group
// filter is active. Counts are very skewed, so breakpoints are spread roughly
// log-wise (1 -> 10 -> 50 -> 250).
function hexRamp(countExpr) {
  return [
    "interpolate", ["linear"], countExpr,
    1, "#e8f3e0",
    10, "#a8d79b",
    50, "#5fae63",
    250, "#1f7a3d",
  ];
}

// --- 1. Create the map -----------------------------------------------------
const map = new maplibregl.Map({
  container: "map",
  // Free, no-API-key colourful basemap from OpenFreeMap; renders landcover
  // (woods, water, wetlands) so habitats show behind the data.
  style: "https://tiles.openfreemap.org/styles/liberty",
  center: CROSBY,
  zoom: 12,
  // Keep the map flat (top-down) so the hexagons sit on one plane.
  pitch: 0,
  maxPitch: 0,
});

map.addControl(new maplibregl.NavigationControl(), "top-right");

// Wait for the basemap before adding our own source/layers.
map.on("load", async () => {
  // Which groups are currently shown. Starts with all; the filter menu (set up
  // below) updates this and calls applyFilter().
  let activeGroups = Object.keys(GROUP_ICONS);

  // --- 2. Load the hexagons ------------------------------------------------
  let hexgeo;
  try {
    hexgeo = await (await fetch(HEX_URL)).json();
  } catch (err) {
    document.getElementById("hud").textContent = "Couldn't load wildlife data";
    console.error("Failed to load", HEX_URL, err);
    return;
  }

  map.addSource("hexes", { type: "geojson", data: hexgeo });

  // Filled hexagons shaded by sighting count (a density "heatmap").
  map.addLayer({
    id: "hex-fill",
    type: "fill",
    source: "hexes",
    paint: {
      "fill-color": hexRamp(["get", "count"]), // recoloured when filtering
      "fill-opacity": 0.6, // let the habitat basemap show through a little
    },
  });

  // A thin outline so neighbouring hexagons read as separate tiles.
  map.addLayer({
    id: "hex-outline",
    type: "line",
    source: "hexes",
    paint: { "line-color": "#1f7a3d", "line-width": 1, "line-opacity": 0.5 },
  });

  // --- 3. Click a hexagon -> pinned species panel --------------------------
  // A pinned popup (not hover-driven) so the cursor can enter it and click the
  // cards. We handle open/close ourselves (below) rather than via closeOnClick,
  // which can race with the click that opens the panel.
  const panel = new maplibregl.Popup({
    closeButton: true,
    closeOnClick: false,
    maxWidth: "300px",
    className: "hex-popup",
  });

  // Placeholder "key facts" shown when a card is expanded. Wire these to real
  // sources later — we already have IUCN red-list status in the GBIF data, and
  // a fun fact could come from Wikipedia.
  function factsPlaceholder(species) {
    const rows = [
      ["Rarity", "Frequently seen locally"],
      ["Ecological status", "Native · Least Concern"],
      ["Did you know?", `A fun fact about the ${species.name} will go here.`],
    ];
    return rows
      .map(([k, v]) => `<div class="fact"><span class="fact-k">${k}</span><span class="fact-v">${v}</span></div>`)
      .join("");
  }

  // Build the panel DOM for one hexagon. Returns a node (so we can attach click
  // handlers to the cards) for popup.setDOMContent().
  function buildHexPanel(p) {
    // aggregate.py stored the per-species breakdown as a JSON string. Keep only
    // active groups, then recompute totals so the panel matches the map.
    const species = JSON.parse(p.species) // [{name, group, count, image}, ...]
      .filter((s) => activeGroups.includes(s.group));
    const total = species.reduce((sum, s) => sum + s.count, 0);

    const root = document.createElement("div");
    root.className = "hex-panel";
    root.innerHTML =
      `<div class="hex-title">${total} sighting${total === 1 ? "" : "s"} · ` +
      `${species.length} species here</div>`;

    for (const s of species.slice(0, TOP_SPECIES)) {
      const card = document.createElement("div");
      card.className = "hex-card";
      card.innerHTML =
        `<div class="hex-card-head">` +
          `<img class="hex-card-img" src="${s.image}" alt="${s.name}" ` +
            `onerror="this.onerror=null;this.src='img/no-photo.svg'" />` +
          `<div class="hex-card-text">` +
            `<div class="hex-card-name">${s.name}</div>` +
            `<div class="hex-card-count">${s.count} sighting${s.count === 1 ? "" : "s"}</div>` +
          `</div>` +
          `<div class="hex-card-chev">▸</div>` +
        `</div>` +
        `<div class="hex-card-facts" hidden>${factsPlaceholder(s)}</div>`;

      // Click the card header to expand/collapse its facts.
      const head = card.querySelector(".hex-card-head");
      const facts = card.querySelector(".hex-card-facts");
      const chev = card.querySelector(".hex-card-chev");
      head.addEventListener("click", () => {
        const opening = facts.hidden;
        facts.hidden = !opening;
        card.classList.toggle("open", opening);
        chev.textContent = opening ? "▾" : "▸";
      });

      root.appendChild(card);
    }

    if (species.length > TOP_SPECIES) {
      const more = document.createElement("div");
      more.className = "hex-more";
      more.textContent = `+${species.length - TOP_SPECIES} more species`;
      root.appendChild(more);
    }
    return root;
  }

  // One click handler: open/replace the panel when a hexagon is clicked, or
  // dismiss it when clicking empty space.
  map.on("click", (e) => {
    const hits = map.queryRenderedFeatures(e.point, { layers: ["hex-fill"] });
    if (hits.length) {
      panel.setLngLat(e.lngLat).setDOMContent(buildHexPanel(hits[0].properties)).addTo(map);
    } else {
      panel.remove();
    }
  });

  // Pointer cursor over clickable hexagons.
  map.on("mouseenter", "hex-fill", () => (map.getCanvas().style.cursor = "pointer"));
  map.on("mouseleave", "hex-fill", () => (map.getCanvas().style.cursor = ""));

  // --- 4. Group filter -----------------------------------------------------
  // Sum of the active groups' per-hex counts, e.g.
  //   ["+", 0, coalesce(count_Mammals,0), coalesce(count_Birds,0)]
  // (the leading 0 keeps it valid when no groups are selected).
  function activeCountExpr() {
    return ["+", 0, ...activeGroups.map((g) => ["coalesce", ["get", `count_${g}`], 0])];
  }

  function applyFilter() {
    const counts = activeCountExpr();
    map.setPaintProperty("hex-fill", "fill-color", hexRamp(counts));
    map.setFilter("hex-fill", [">", counts, 0]);
    map.setFilter("hex-outline", [">", counts, 0]);
    panel.remove(); // close any open panel so it can't show now-hidden content
  }

  // Build a checkbox per group from GROUP_ICONS.
  const filterEl = document.getElementById("filter");
  filterEl.innerHTML = Object.entries(GROUP_ICONS)
    .map(([group, emoji]) =>
      `<label><input type="checkbox" value="${group}" checked /> ${emoji} ${group}</label>`)
    .join("");
  filterEl.addEventListener("change", () => {
    activeGroups = [...filterEl.querySelectorAll("input:checked")].map((i) => i.value);
    applyFilter();
  });

  // --- HUD: totals derived from the hexagons -------------------------------
  const totalObs = hexgeo.features.reduce((sum, f) => sum + f.properties.count, 0);
  const speciesSet = new Set();
  for (const f of hexgeo.features) {
    for (const s of JSON.parse(f.properties.species)) speciesSet.add(s.name);
  }
  document.getElementById("hud").textContent =
    `${totalObs.toLocaleString()} observations · ${speciesSet.size} species around Crosby`;
});
