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
  Amphibians: "🐸",
  Reptiles: "🦎",
};

// How many species cards to show before the panel is expanded.
const COLLAPSED_CARDS = 3;

// H3 resolutions and their zoom ranges (must match config.HEX_RESOLUTIONS in
// the Python pipeline). Each resolution gets its own fill + outline layer pair
// that is only visible in its zoom range.
const HEX_ZOOM_MAP = {
  3: [0, 8],
  5: [8, 10],
  7: [10, 12],
  8: [12, 24],
};

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

  // Create a fill + outline layer pair per H3 resolution, each visible only in
  // its zoom range. The "resolution" property on each feature (set by
  // aggregate.py) controls which layer it appears in via a filter.
  const fillLayerIds = [];
  const outlineLayerIds = [];
  for (const [res, [minz, maxz]] of Object.entries(HEX_ZOOM_MAP)) {
    const r = Number(res);
    const fillId = `hex-fill-${r}`;
    const outlineId = `hex-outline-${r}`;
    fillLayerIds.push(fillId);
    outlineLayerIds.push(outlineId);

    map.addLayer({
      id: fillId,
      type: "fill",
      source: "hexes",
      minzoom: minz,
      maxzoom: maxz,
      filter: ["==", ["get", "resolution"], r],
      paint: {
        "fill-color": hexRamp(["get", "count"]),
        "fill-opacity": 0.6,
      },
    });
    map.addLayer({
      id: outlineId,
      type: "line",
      source: "hexes",
      minzoom: minz,
      maxzoom: maxz,
      filter: ["==", ["get", "resolution"], r],
      paint: { "line-color": "#1f7a3d", "line-width": 1, "line-opacity": 0.5 },
    });
  }

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

  // Build the key-facts HTML for an expanded species card. Data comes from the
  // pipeline: rarity is a local-frequency tier, iucn is the IUCN Red List
  // category, and extract is the opening line of the Wikipedia article.
  function speciesFacts(s) {
    const rows = [];
    // Rarity — how often this species is seen across the whole dataset.
    if (s.rarity) rows.push(["Rarity", s.rarity]);
    // IUCN Red List status — this is the GLOBAL assessment, not UK-specific.
    // A species can be globally endangered but locally common (e.g. rabbit).
    if (s.iucn) rows.push(["IUCN (global)", s.iucn]);
    // Hand-curated fun fact from data/fun_facts.json.
    if (s.funFact) rows.push(["Did you know?", s.funFact]);
    // Fallback if the pipeline didn't supply any facts yet.
    if (!rows.length) rows.push(["Info", "No details available yet."]);
    return rows
      .map(([k, v]) => `<div class="fact"><span class="fact-k">${k}</span><span class="fact-v">${v}</span></div>`)
      .join("");
  }

  // Show a photo full-screen. Click anywhere to dismiss.
  function openLightbox(src, alt) {
    const overlay = document.createElement("div");
    overlay.className = "lightbox";
    overlay.innerHTML = `<img src="${src}" alt="${alt || ""}" />`;
    overlay.addEventListener("click", () => overlay.remove());
    document.body.appendChild(overlay);
  }

  // Build one expandable species card. The header toggles the facts open; when
  // open, the larger photo can be clicked to enlarge it in the lightbox.
  function speciesCard(s) {
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
      `<div class="hex-card-facts" hidden>` +
        `<img class="hex-card-photo" src="${s.image}" alt="${s.name}" title="Click to enlarge" ` +
          `onerror="this.onerror=null;this.src='img/no-photo.svg'" />` +
        speciesFacts(s) +
      `</div>`;

    const head = card.querySelector(".hex-card-head");
    const facts = card.querySelector(".hex-card-facts");
    const chev = card.querySelector(".hex-card-chev");
    head.addEventListener("click", () => {
      const opening = facts.hidden;
      facts.hidden = !opening;
      card.classList.toggle("open", opening);
      chev.textContent = opening ? "▾" : "▸";
    });
    card.querySelector(".hex-card-photo")
      .addEventListener("click", () => openLightbox(s.image, s.name));
    return card;
  }

  // Build the panel DOM for one hexagon. Collapsed it shows the top few species;
  // "Show all" expands to a scrollable list grouped by animal group.
  function buildHexPanel(p) {
    // The species list is already filtered by applyFilter() — just parse it.
    const species = JSON.parse(p.species);
    const total = species.reduce((sum, s) => sum + s.count, 0);

    const root = document.createElement("div");
    root.className = "hex-panel";
    root.innerHTML =
      `<div class="hex-title">${total} sighting${total === 1 ? "" : "s"} · ` +
      `${species.length} species here</div>`;

    const list = document.createElement("div");
    list.className = "hex-cards";
    root.appendChild(list);

    // Re-render the card list for the current expanded/collapsed state.
    let expanded = false;
    function render() {
      list.innerHTML = "";
      list.classList.toggle("scroll", expanded); // cap height + scroll when expanded
      if (!expanded) {
        // Collapsed: the top species overall (already sorted by count).
        species.slice(0, COLLAPSED_CARDS).forEach((s) => list.appendChild(speciesCard(s)));
      } else {
        // Expanded: every species, grouped by animal group (in GROUP_ICONS order).
        // Each group header is clickable — toggles its cards open/closed.
        for (const group of Object.keys(GROUP_ICONS)) {
          const inGroup = species.filter((s) => s.group === group);
          if (!inGroup.length) continue;

          // Wrapper holds the header + its cards so we can show/hide cards together.
          const section = document.createElement("div");
          section.className = "hex-section";

          const header = document.createElement("div");
          header.className = "hex-group";
          // Chevron shows collapse state; starts open (▾).
          header.innerHTML =
            `<span class="hex-group-label">${GROUP_ICONS[group]} ${group} (${inGroup.length})</span>` +
            `<span class="hex-group-chev">▾</span>`;
          section.appendChild(header);

          const cardWrap = document.createElement("div");
          inGroup.forEach((s) => cardWrap.appendChild(speciesCard(s)));
          section.appendChild(cardWrap);

          // Click the header to collapse/expand this group's cards.
          header.addEventListener("click", () => {
            const hiding = !cardWrap.hidden;
            cardWrap.hidden = hiding;
            header.querySelector(".hex-group-chev").textContent = hiding ? "▸" : "▾";
            section.classList.toggle("collapsed", hiding);
          });

          list.appendChild(section);
        }
      }
    }
    render();

    // "Show all / Show fewer" toggle, only when there's more than the collapsed set.
    if (species.length > COLLAPSED_CARDS) {
      const toggle = document.createElement("button");
      toggle.className = "hex-toggle";
      const label = () =>
        (toggle.textContent = expanded ? "Show fewer ▴" : `Show all ${species.length} species ▾`);
      label();
      toggle.addEventListener("click", () => {
        expanded = !expanded;
        render();
        label();
      });
      root.appendChild(toggle);
    }
    return root;
  }

  // One click handler: open/replace the panel when a hexagon is clicked, or
  // dismiss it when clicking empty space. Query all fill layers.
  map.on("click", (e) => {
    const hits = map.queryRenderedFeatures(e.point, { layers: fillLayerIds });
    if (hits.length) {
      panel.setLngLat(e.lngLat).setDOMContent(buildHexPanel(hits[0].properties)).addTo(map);
    } else {
      panel.remove();
    }
  });

  // Pointer cursor over clickable hexagons (register on each fill layer).
  for (const id of fillLayerIds) {
    map.on("mouseenter", id, () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", id, () => (map.getCanvas().style.cursor = ""));
  }

  // --- 4. Filters -----------------------------------------------------------
  // Filters work at the species level: group, rarity tier, and IUCN status.
  // When any filter changes we recompute per-hex counts from only the matching
  // species, push a new filtered copy of the hex data to the map source, and
  // recolour accordingly.

  // Pre-parse every hex's species list once (expensive to repeat on each click).
  const hexSpecies = hexgeo.features.map((f) => JSON.parse(f.properties.species));

  // Collect all distinct rarity and IUCN values across the dataset so the
  // dropdowns are data-driven — add a new group or rarity tier in the pipeline
  // and it flows through automatically.
  const allRarities = [...new Set(hexSpecies.flat().map((s) => s.rarity).filter(Boolean))];
  const allIucn = [...new Set(hexSpecies.flat().map((s) => s.iucn).filter(Boolean))];

  // Order rarity from common → rare so the dropdown reads naturally.
  const RARITY_ORDER = ["Very common", "Common", "Uncommon", "Scarce", "Rare"];
  allRarities.sort((a, b) => RARITY_ORDER.indexOf(a) - RARITY_ORDER.indexOf(b));

  // Current filter state — "all" means no restriction on that axis.
  let filterGroup = "all";
  let filterRarity = "all";
  let filterIucn = "all";

  // Return only the species in a hex that pass the active filters.
  function filterSpecies(speciesList) {
    return speciesList.filter((s) =>
      (filterGroup === "all" || s.group === filterGroup) &&
      (filterRarity === "all" || s.rarity === filterRarity) &&
      (filterIucn === "all" || s.iucn === filterIucn)
    );
  }

  // Recompute hex counts from filtered species and push to the map.
  function applyFilter() {
    const updated = JSON.parse(JSON.stringify(hexgeo)); // deep copy
    for (let i = 0; i < updated.features.length; i++) {
      const matched = filterSpecies(hexSpecies[i]);
      const count = matched.reduce((sum, s) => sum + s.count, 0);
      // Overwrite the species JSON so buildHexPanel reads the filtered set.
      updated.features[i].properties.species = JSON.stringify(matched);
      updated.features[i].properties.count = count;
    }
    map.getSource("hexes").setData(updated);
    // Recolour and hide empty hexes across all resolution layers.
    for (const id of fillLayerIds) {
      map.setPaintProperty(id, "fill-color", hexRamp(["get", "count"]));
      // Combine the resolution filter with a count > 0 filter.
      const res = Number(id.split("-").pop());
      map.setFilter(id, ["all", ["==", ["get", "resolution"], res], [">", ["get", "count"], 0]]);
    }
    for (const id of outlineLayerIds) {
      const res = Number(id.split("-").pop());
      map.setFilter(id, ["all", ["==", ["get", "resolution"], res], [">", ["get", "count"], 0]]);
    }
    panel.remove();
    updateHud(updated);
  }

  // Build the filter panel: three <select> dropdowns.
  const filterEl = document.getElementById("filter");
  function buildSelect(label, options, onChange) {
    const wrap = document.createElement("div");
    wrap.className = "filter-row";
    const lbl = document.createElement("label");
    lbl.textContent = label;
    const sel = document.createElement("select");
    sel.innerHTML = `<option value="all">All</option>` +
      options.map((o) => `<option value="${o}">${o}</option>`).join("");
    sel.addEventListener("change", () => { onChange(sel.value); applyFilter(); });
    wrap.appendChild(lbl);
    wrap.appendChild(sel);
    return wrap;
  }

  // Group filter — list each group with its emoji.
  const groupOptions = Object.entries(GROUP_ICONS).map(([g, e]) => `${e} ${g}`);
  const groupValues = Object.keys(GROUP_ICONS);
  const groupWrap = document.createElement("div");
  groupWrap.className = "filter-row";
  const groupLbl = document.createElement("label");
  groupLbl.textContent = "Group";
  const groupSel = document.createElement("select");
  groupSel.innerHTML = `<option value="all">All groups</option>` +
    groupValues.map((g, i) => `<option value="${g}">${groupOptions[i]}</option>`).join("");
  groupSel.addEventListener("change", () => { filterGroup = groupSel.value; applyFilter(); });
  groupWrap.appendChild(groupLbl);
  groupWrap.appendChild(groupSel);
  filterEl.appendChild(groupWrap);

  // Rarity filter.
  filterEl.appendChild(buildSelect("Rarity", allRarities, (v) => { filterRarity = v; }));

  // Conservation status filter.
  filterEl.appendChild(buildSelect("Conservation", allIucn, (v) => { filterIucn = v; }));

  // --- HUD: totals derived from the (filtered) hexagons --------------------
  function updateHud(geo) {
    const totalObs = geo.features.reduce((sum, f) => sum + f.properties.count, 0);
    const speciesSet = new Set();
    for (const f of geo.features) {
      for (const s of JSON.parse(f.properties.species)) speciesSet.add(s.name);
    }
    document.getElementById("hud").textContent =
      `${totalObs.toLocaleString()} observations · ${speciesSet.size} species around Crosby`;
  }
  updateHud(hexgeo);
});
