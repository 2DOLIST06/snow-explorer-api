import "dotenv/config";
import pkg from "pg";
const { Pool } = pkg;

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: { rejectUnauthorized: false },
});

const q = (sql, params = []) => pool.query(sql, params);

const OVERPASS_URL = "https://overpass-api.de/api/interpreter";
const DEFAULT_RADIUS_M = 12000;

function toNumber(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function normalizeDifficulty(raw) {
  const v = String(raw || "").toLowerCase().trim();
  if (!v) return null;

  if (["novice", "easy", "beginner", "green", "vert", "verte"].includes(v)) return "green";
  if (["intermediate", "blue", "bleu", "bleue"].includes(v)) return "blue";
  if (["advanced", "red", "rouge"].includes(v)) return "red";
  if (["expert", "extreme", "black", "noir", "noire"].includes(v)) return "black";

  return v;
}

function normalizeLiftType(raw) {
  const v = String(raw || "").toLowerCase().trim();
  if (!v) return null;

  if (
    [
      "drag_lift",
      "t-bar",
      "j-bar",
      "platter",
      "rope_tow",
      "magic_carpet",
    ].includes(v)
  ) {
    return "drag";
  }

  if (["chair_lift", "mixed_lift"].includes(v)) {
    return "chair";
  }

  if (
    [
      "gondola",
      "cable_car",
      "goods",
      "zip_line",
      "jig_back",
      "yes",
      "station",
    ].includes(v)
  ) {
    return "cable";
  }

  if (v.includes("chair")) return "chair";
  if (
    v.includes("drag") ||
    v.includes("t-bar") ||
    v.includes("j-bar") ||
    v.includes("platter") ||
    v.includes("rope") ||
    v.includes("carpet")
  ) {
    return "drag";
  }

  return "cable";
}

function haversineDistanceMeters(lat1, lon1, lat2, lon2) {
  const R = 6371000;
  const toRad = (d) => (d * Math.PI) / 180;

  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;

  return 2 * R * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function buildOverpassQuery(lat, lon, radiusM) {
  return `
[out:json][timeout:120];

(
  way(around:${radiusM},${lat},${lon})["piste:type"="downhill"];
  relation(around:${radiusM},${lat},${lon})["route"="piste"]["piste:type"="downhill"];

  way(around:${radiusM},${lat},${lon})["aerialway"];
  relation(around:${radiusM},${lat},${lon})["aerialway"];
);

out tags center geom;
`;
}

async function fetchOverpass(lat, lon, radiusM) {
  const query = buildOverpassQuery(lat, lon, radiusM);

  const r = await fetch(OVERPASS_URL, {
    method: "POST",
    headers: {
      "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
    },
    body: `data=${encodeURIComponent(query)}`,
  });

  if (!r.ok) {
    throw new Error(`Overpass HTTP ${r.status}`);
  }

  return r.json();
}

function elementCenter(el) {
  if (typeof el?.center?.lat === "number" && typeof el?.center?.lon === "number") {
    return { lat: el.center.lat, lon: el.center.lon };
  }

  if (Array.isArray(el?.geometry) && el.geometry.length > 0) {
    const pts = el.geometry.filter(
      (p) => typeof p?.lat === "number" && typeof p?.lon === "number"
    );
    if (pts.length) {
      const lat = pts.reduce((s, p) => s + p.lat, 0) / pts.length;
      const lon = pts.reduce((s, p) => s + p.lon, 0) / pts.length;
      return { lat, lon };
    }
  }

  return null;
}

function estimateLengthMeters(el) {
  if (!Array.isArray(el?.geometry) || el.geometry.length < 2) return null;

  let total = 0;
  for (let i = 1; i < el.geometry.length; i++) {
    const a = el.geometry[i - 1];
    const b = el.geometry[i];
    if (
      typeof a?.lat === "number" &&
      typeof a?.lon === "number" &&
      typeof b?.lat === "number" &&
      typeof b?.lon === "number"
    ) {
      total += haversineDistanceMeters(a.lat, a.lon, b.lat, b.lon);
    }
  }

  return total > 0 ? Math.round(total) : null;
}

function estimateElevationDiff(tags) {
  const top =
    toNumber(tags?.["piste:top"]) ??
    toNumber(tags?.["ele:top"]) ??
    toNumber(tags?.["top"]) ??
    null;

  const bottom =
    toNumber(tags?.["piste:bottom"]) ??
    toNumber(tags?.["ele:bottom"]) ??
    toNumber(tags?.["bottom"]) ??
    null;

  if (top !== null && bottom !== null) {
    return Math.max(0, Math.round(top - bottom));
  }

  return null;
}

function dedupeByKey(items, keyFn) {
  const seen = new Set();
  const out = [];

  for (const item of items) {
    const key = keyFn(item);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    out.push(item);
  }

  return out;
}

async function run() {
  const slug = process.argv[2];
  if (!slug) {
    throw new Error("Utilise: node scripts/import-openskimap.js arc-2000");
  }

  const resortRes = await q(
    `select id, name, slug, latitude, longitude
     from resort
     where slug = $1
     limit 1`,
    [slug]
  );

  if (!resortRes.rowCount) {
    throw new Error(`Station introuvable en base pour slug=${slug}`);
  }

  const resort = resortRes.rows[0];

  if (!Number.isFinite(Number(resort.latitude)) || !Number.isFinite(Number(resort.longitude))) {
    throw new Error(`Coordonnées manquantes pour ${resort.slug}`);
  }

  const lat = Number(resort.latitude);
  const lon = Number(resort.longitude);

  console.log(`Import OSM/Overpass pour: ${resort.name} (${resort.slug})`);

  const overpass = await fetchOverpass(lat, lon, DEFAULT_RADIUS_M);
  const elements = Array.isArray(overpass?.elements) ? overpass.elements : [];

  const rawRuns = elements.filter((el) => {
    const tags = el?.tags || {};
    return tags["piste:type"] === "downhill" || tags.route === "piste";
  });

  const rawLifts = elements.filter((el) => {
    const tags = el?.tags || {};
    return Boolean(tags.aerialway) && tags.aerialway !== "station";
  });

  const matchedRuns = dedupeByKey(rawRuns, (el) => `${el.type}:${el.id}`);
  const matchedLifts = dedupeByKey(rawLifts, (el) => `${el.type}:${el.id}`);

  console.log(`Runs trouvés: ${matchedRuns.length}`);
  console.log(`Lifts trouvés: ${matchedLifts.length}`);

  await q("begin");

  try {
    await q(`delete from piste where resort_id = $1`, [resort.id]);
    await q(`delete from lift where resort_id = $1`, [resort.id]);

    for (const el of matchedRuns) {
      const tags = el?.tags || {};

      const name =
        tags.name ||
        tags["piste:name"] ||
        tags.ref ||
        `Piste ${el.id}`;

      const difficulty = normalizeDifficulty(
        tags["piste:difficulty"] ||
          tags.difficulty ||
          tags.colour ||
          tags.color
      );

      const lengthM =
        toNumber(tags["piste:length"]) ||
        toNumber(tags.length) ||
        estimateLengthMeters(el);

      const elevationDiffM =
        estimateElevationDiff(tags);

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
          name || null,
          difficulty,
          lengthM,
          elevationDiffM,
        ]
      );
    }

    for (const el of matchedLifts) {
      const tags = el?.tags || {};

      const name =
        tags.name ||
        tags.ref ||
        `Lift ${el.id}`;

      const type = normalizeLiftType(tags.aerialway || tags.type);
      const capacity =
        toNumber(tags.capacity) ||
        toNumber(tags["aerialway:capacity"]) ||
        toNumber(tags["capacity:persons"]) ||
        null;

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
          name || null,
          type,
          capacity,
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
