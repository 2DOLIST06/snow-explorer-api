import "dotenv/config";
import pkg from "pg";
const { Pool } = pkg;

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: { rejectUnauthorized: false },
});

const q = (sql, params = []) => pool.query(sql, params);

const OPENSKIMAP_SKI_AREAS_URL = "https://tiles.skimap.org/geojson/ski_areas.geojson";
const OPENSKIMAP_RUNS_URL = "https://tiles.skimap.org/geojson/runs.geojson";
const OPENSKIMAP_LIFTS_URL = "https://tiles.skimap.org/geojson/lifts.geojson";

function flattenCoords(coords, out = []) {
  if (!Array.isArray(coords)) return out;
  if (typeof coords[0] === "number" && typeof coords[1] === "number") {
    out.push(coords);
    return out;
  }
  for (const c of coords) flattenCoords(c, out);
  return out;
}

function getBBoxFromGeometry(geometry) {
  const pts = flattenCoords(geometry?.coordinates || []);
  if (!pts.length) return null;

  let minLng = Infinity;
  let minLat = Infinity;
  let maxLng = -Infinity;
  let maxLat = -Infinity;

  for (const [lng, lat] of pts) {
    if (lng < minLng) minLng = lng;
    if (lat < minLat) minLat = lat;
    if (lng > maxLng) maxLng = lng;
    if (lat > maxLat) maxLat = lat;
  }

  return { minLng, minLat, maxLng, maxLat };
}

function expandBBox(bbox, pad = 0.01) {
  return {
    minLng: bbox.minLng - pad,
    minLat: bbox.minLat - pad,
    maxLng: bbox.maxLng + pad,
    maxLat: bbox.maxLat + pad,
  };
}

function bboxesIntersect(a, b) {
  return !(
    a.maxLng < b.minLng ||
    a.minLng > b.maxLng ||
    a.maxLat < b.minLat ||
    a.minLat > b.maxLat
  );
}

function normalizeDifficulty(raw) {
  const v = String(raw || "").toLowerCase().trim();
  if (!v) return null;
  if (["green", "easy", "novice", "beginner", "verte", "vert"].includes(v)) return "green";
  if (["blue", "intermediate", "bleue", "bleu"].includes(v)) return "blue";
  if (["red", "advanced", "rouge"].includes(v)) return "red";
  if (["black", "expert", "extreme", "double_black", "noire", "noir"].includes(v)) return "black";
  return v;
}

function normalizeLiftType(raw) {
  const v = String(raw || "").toLowerCase().trim();
  if (!v) return null;

  if (
    v.includes("drag") ||
    v.includes("surface") ||
    v.includes("platter") ||
    v.includes("button") ||
    v.includes("rope") ||
    v.includes("t-bar") ||
    v.includes("j-bar") ||
    v.includes("magic carpet") ||
    v.includes("tire")
  ) {
    return "drag";
  }

  if (
    v.includes("chair") ||
    v.includes("telesiege") ||
    v.includes("télésiège")
  ) {
    return "chair";
  }

  if (
    v.includes("gondola") ||
    v.includes("tram") ||
    v.includes("cable") ||
    v.includes("telepherique") ||
    v.includes("téléphérique") ||
    v.includes("funitel") ||
    v.includes("funicular") ||
    v.includes("aerial")
  ) {
    return "cable";
  }

  return v;
}

function toNumber(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

async function fetchGeoJSON(url) {
  const r = await fetch(url);
  if (!r.ok) {
    throw new Error(`Fetch failed ${url} -> HTTP ${r.status}`);
  }
  return r.json();
}

async function run() {
  const slug = process.argv[2];
  if (!slug) {
    throw new Error("Utilise: node scripts/import-openskimap.js arc-2000");
  }

  const resortRes = await q(
    `select id, name, slug
     from resort
     where slug = $1
     limit 1`,
    [slug]
  );

  if (!resortRes.rowCount) {
    throw new Error(`Station introuvable en base pour slug=${slug}`);
  }

  const resort = resortRes.rows[0];
  console.log(`Import OpenSkiMap pour: ${resort.name} (${resort.slug})`);

  const [skiAreas, runs, lifts] = await Promise.all([
    fetchGeoJSON(OPENSKIMAP_SKI_AREAS_URL),
    fetchGeoJSON(OPENSKIMAP_RUNS_URL),
    fetchGeoJSON(OPENSKIMAP_LIFTS_URL),
  ]);

  const areaFeatures = Array.isArray(skiAreas?.features) ? skiAreas.features : [];
  const runFeatures = Array.isArray(runs?.features) ? runs.features : [];
  const liftFeatures = Array.isArray(lifts?.features) ? lifts.features : [];

  const normalizedResortName = resort.name.toLowerCase().trim();

  const matchedArea =
    areaFeatures.find((f) => String(f?.properties?.name || "").toLowerCase().trim() === normalizedResortName) ||
    areaFeatures.find((f) => String(f?.properties?.name || "").toLowerCase().includes(normalizedResortName)) ||
    areaFeatures.find((f) => normalizedResortName.includes(String(f?.properties?.name || "").toLowerCase().trim()));

  if (!matchedArea) {
    throw new Error(`Aucune ski_area OpenSkiMap trouvée pour ${resort.name}`);
  }

  const areaName = matchedArea?.properties?.name || resort.name;
  const areaBBoxRaw = getBBoxFromGeometry(matchedArea.geometry);
  if (!areaBBoxRaw) {
    throw new Error(`Impossible de calculer la bbox pour ${areaName}`);
  }
  const areaBBox = expandBBox(areaBBoxRaw, 0.005);

  console.log(`Zone trouvée: ${areaName}`);

  const matchedRuns = runFeatures.filter((f) => {
    const bbox = getBBoxFromGeometry(f.geometry);
    return bbox ? bboxesIntersect(areaBBox, bbox) : false;
  });

  const matchedLifts = liftFeatures.filter((f) => {
    const bbox = getBBoxFromGeometry(f.geometry);
    return bbox ? bboxesIntersect(areaBBox, bbox) : false;
  });

  console.log(`Runs trouvés: ${matchedRuns.length}`);
  console.log(`Lifts trouvés: ${matchedLifts.length}`);

  await q("begin");

  try {
    await q(`delete from piste where resort_id = $1`, [resort.id]);
    await q(`delete from lift where resort_id = $1`, [resort.id]);

    for (const f of matchedRuns) {
      const p = f?.properties || {};

      await q(
        `insert into piste (
          id,
          resort_id,
          name,
          difficulty,
          length_m,
          elevation_diff_m
        )
        values (
          gen_random_uuid(),
          $1,
          $2,
          $3,
          $4,
          $5
        )`,
        [
          resort.id,
          p.name || null,
          normalizeDifficulty(p.difficulty || p.piste_difficulty || p.color),
          toNumber(p.length_m || p.length),
          toNumber(p.elevation_diff_m || p.vertical_drop || p.vertical)
        ]
      );
    }

    for (const f of matchedLifts) {
      const p = f?.properties || {};

      await q(
        `insert into lift (
          id,
          resort_id,
          name,
          type,
          capacity_per_hour
        )
        values (
          gen_random_uuid(),
          $1,
          $2,
          $3,
          $4
        )`,
        [
          resort.id,
          p.name || null,
          normalizeLiftType(p.type || p.lift_type || p.aerialway),
          toNumber(p.capacity_per_hour || p.capacity)
        ]
      );
    }

    await q(
      `update resort
       set pistes_count = $2,
           lifts_count = $3
       where id = $1`,
      [resort.id, matchedRuns.length, matchedLifts.length]
    );

    await q("commit");
    console.log("Import terminé");
  } catch (e) {
    await q("rollback");
    throw e;
  } finally {
    await pool.end();
  }
}

run().catch(async (e) => {
  console.error(e);
  try {
    await pool.end();
  } catch {}
  process.exit(1);
});
