import "dotenv/config";
import pkg from "pg";
import fs from "fs/promises";
const { Pool } = pkg;

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: { rejectUnauthorized: false },
});

const q = (sql, params = []) => pool.query(sql, params);

const OPENSKIMAP_SKI_AREAS_URL = "https://tiles.skimap.org/geojson/ski_areas.geojson";

function normalizeName(v) {
  return String(v || "")
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/['’]/g, "")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function scoreMatch(a, b) {
  if (!a || !b) return 0;
  if (a === b) return 100;
  if (a.includes(b) || b.includes(a)) return 80;

  const aWords = new Set(a.split(" ").filter(Boolean));
  const bWords = new Set(b.split(" ").filter(Boolean));
  let common = 0;
  for (const w of aWords) {
    if (bWords.has(w)) common++;
  }
  if (!aWords.size || !bWords.size) return 0;
  return Math.round((common / Math.max(aWords.size, bWords.size)) * 100);
}

async function fetchGeoJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`Fetch failed ${url} -> HTTP ${r.status}`);
  return r.json();
}

async function run() {
  const resortsRes = await q(`
    select id, slug, name, openskimap_area_name, openskimap_enabled
    from resort
    where coalesce(openskimap_enabled, true) = true
    order by name asc
  `);

  const resorts = resortsRes.rows;

  const skiAreas = await fetchGeoJSON(OPENSKIMAP_SKI_AREAS_URL);
  const areaFeatures = Array.isArray(skiAreas?.features) ? skiAreas.features : [];

  const areaNames = areaFeatures.map((f) => ({
    raw: String(f?.properties?.name || "").trim(),
    norm: normalizeName(f?.properties?.name || ""),
  })).filter((x) => x.raw);

  const report = [];

  for (const resort of resorts) {
    const sourceName = resort.openskimap_area_name || resort.name;
    const normResort = normalizeName(sourceName);

    let best = null;
    for (const area of areaNames) {
      const score = scoreMatch(normResort, area.norm);
      if (!best || score > best.score) {
        best = { area_name: area.raw, score };
      }
    }

    report.push({
      slug: resort.slug,
      resort_name: resort.name,
      openskimap_area_name: resort.openskimap_area_name || "",
      best_match: best?.area_name || "",
      score: best?.score || 0,
      status:
        (best?.score || 0) >= 100
          ? "exact"
          : (best?.score || 0) >= 80
          ? "review"
          : "not_found",
    });
  }

  await fs.writeFile(
    "./openskimap-match-report.json",
    JSON.stringify(report, null, 2),
    "utf8"
  );

  console.log("Rapport généré : openskimap-match-report.json");
  await pool.end();
}

run().catch(async (e) => {
  console.error(e);
  try {
    await pool.end();
  } catch {}
  process.exit(1);
});
