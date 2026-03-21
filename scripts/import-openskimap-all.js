import "dotenv/config";
import { spawn } from "node:child_process";
import pkg from "pg";
const { Pool } = pkg;

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: { rejectUnauthorized: false },
});

const q = (sql, params = []) => pool.query(sql, params);

function runImport(slug) {
  return new Promise((resolve) => {
    const child = spawn("node", ["scripts/import-openskimap.js", slug], {
      stdio: "inherit",
      env: process.env,
    });

    child.on("close", (code) => {
      resolve({
        slug,
        ok: code === 0,
        code,
      });
    });
  });
}

async function run() {
  const resortsRes = await q(`
    select slug, name
    from resort
    where latitude is not null
      and longitude is not null
      and coalesce(openskimap_enabled, true) = true
    order by name asc
  `);

  const resorts = resortsRes.rows;

  console.log(`Stations à traiter : ${resorts.length}`);

  const results = [];

  for (const resort of resorts) {
    console.log(`\n=== Import ${resort.slug} (${resort.name}) ===`);
    const result = await runImport(resort.slug);
    results.push(result);
  }

  const okCount = results.filter((r) => r.ok).length;
  const koCount = results.filter((r) => !r.ok).length;

  console.log(`\n=== Résumé ===`);
  console.log(`OK : ${okCount}`);
  console.log(`Erreurs : ${koCount}`);

  if (koCount > 0) {
    console.log(`\nStations en erreur :`);
    for (const r of results.filter((x) => !x.ok)) {
      console.log(`- ${r.slug} (code ${r.code})`);
    }
    process.exitCode = 1;
  }

  await pool.end();
}

run().catch(async (e) => {
  console.error(e);
  try {
    await pool.end();
  } catch {}
  process.exit(1);
});
