import 'dotenv/config';
import express from 'express';
import cors from 'cors';
import bodyParser from 'body-parser';
import pkg from 'pg';
const { Pool } = pkg;

/* =========================
 * App & DB
 * =======================*/
const app = express();
app.use(bodyParser.json({ limit: '2mb' }));
app.use(cors({ origin: process.env.CORS_ORIGIN || '*', credentials: true }));

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: {
    rejectUnauthorized: false,
  },
});
const q = (sql, params = []) => pool.query(sql, params);
const PORT = process.env.PORT || 5001;

/* =========================
 * Health
 * =======================*/
app.get('/health', (_req, res) => res.json({ ok: true }));

/* =========================
 * Helpers
 * =======================*/
async function loadResortBySlug(slug) {
  const r1 = await q(
    `select
       id, name, slug, latitude, longitude,
       website_url, cover_image_url, description_md,
       region_id, department,
       altitude_min_m, altitude_max_m,
       to_char(season_open_date,'YYYY-MM-DD') as season_open_date,
       to_char(season_close_date,'YYYY-MM-DD') as season_close_date
     from resorts
     where slug = $1`,
    [slug]
  );
  if (!r1.rowCount) return null;

  const resort = r1.rows[0];
  const r2 = await q(`select data from resort_widgets where resort_id=$1`, [resort.id]);
  const widgets = r2.rowCount ? r2.rows[0].data : {};
  return { resort, widgets };
}

/* =========================
 * Référentiels (selects UI)
 * =======================*/
app.get('/api/regions', async (_req, res) => {
  try {
    const { rows } = await q(`select id, name, country_code from regions order by name asc`);
    res.json(rows);
  } catch (e) { res.status(500).send(e.message); }
});

app.get('/api/departments', async (req, res) => {
  try {
    const { region_id } = req.query;
    let rows;
    if (region_id) {
      ({ rows } = await q(
        `select code, name, region_id
         from departments
         where lower(region_id)=lower($1)
         order by name asc`,
        [String(region_id)]
      ));
    } else {
      ({ rows } = await q(`select code, name, region_id from departments order by name asc`));
    }
    res.json(rows);
  } catch (e) { res.status(500).send(e.message); }
});

/* =========================
 * Listing stations (+ alias legacy)
 * =======================*/
app.get(['/api/resorts', '/api/ski/resorts'], async (req, res) => {
  try {
    const qstr = String(req.query.q ?? '').trim();
    const limit = Math.min(parseInt(String(req.query.limit ?? '200'), 10) || 200, 500);
    const offset = parseInt(String(req.query.offset ?? '0'), 10) || 0;

    const params = [];
    let where = '';
    if (qstr) {
      params.push(`%${qstr}%`);
      where = `where name ilike $${params.length} or slug ilike $${params.length}`;
    }
    params.push(limit, offset);

    const sql = `
      select id, name, slug, region_id, department, latitude, longitude
      from resorts
      ${where}
      order by name asc
      limit $${params.length-1} offset $${params.length};
    `;
    const { rows } = await q(sql, params);
    res.json(rows);
  } catch (e) { res.status(500).send(e.message || 'error'); }
});

/* Détail station par slug (lecture) + alias */
app.get(['/api/resorts/:slug', '/api/ski/resorts/:slug'], async (req, res) => {
  try {
    const data = await loadResortBySlug(req.params.slug);
    if (!data) return res.status(404).send('Not found');
    res.json(data);
  } catch (e) { res.status(500).send(e.message); }
});

/* =========================
 * Admin Stations / Resorts (+ alias legacy)
 * =======================*/
for (const base of [
  '/api/admin/stations',
  '/api/admin/resorts',
  '/api/ski/admin/stations',   // alias legacy
  '/api/ski/admin/resorts'     // alias legacy
]) {
  // GET resort + widgets
  app.get(`${base}/:slug`, async (req, res) => {
    try {
      const data = await loadResortBySlug(req.params.slug);
      if (!data) return res.status(404).send('Not found');
      res.json(data);
    } catch (e) { res.status(500).send(e.message); }
  });

  // PATCH resort fields
  app.patch(`${base}/:slug`, async (req, res) => {
    try {
      const { slug } = req.params;
      const found = await loadResortBySlug(slug);
      if (!found) return res.status(404).send('Not found');

      const body = req.body || {};
      const fields = [
        'name','latitude','longitude','website_url','cover_image_url','description_md',
        'region_id','department','altitude_min_m','altitude_max_m','season_open_date','season_close_date'
      ];

      const set = [];
      const vals = [];
      for (const k of fields) {
        if (Object.prototype.hasOwnProperty.call(body, k)) {
          set.push(`${k} = $${set.length + 1}`);
          vals.push(body[k] === '' ? null : body[k]);
        }
      }
      if (set.length) {
        vals.push(slug);
        await q(`update resorts set ${set.join(', ')} where slug = $${vals.length}`, vals);
      }

      const data = await loadResortBySlug(slug);
      res.json(data);
    } catch (e) { res.status(500).send(`PATCH failed: ${e.message}`); }
  });

  // PATCH widgets (jsonb complet)
  app.patch(`${base}/:slug/widgets`, async (req, res) => {
    try {
      const { slug } = req.params;
      const found = await loadResortBySlug(slug);
      if (!found) return res.status(404).send('Not found');

      const widgets = req.body || {};
      await q(
        `insert into resort_widgets (resort_id, data)
         values ($1, $2::jsonb)
         on conflict (resort_id) do update set data = excluded.data`,
        [found.resort.id, widgets]
      );

      const data = await loadResortBySlug(slug);
      res.json(data);
    } catch (e) { res.status(500).send(`PATCH widgets failed: ${e.message}`); }
  });
}

/* =========================
 * Start
 * =======================*/
app.listen(PORT, () => {
  console.log('API listening on http://127.0.0.1:' + PORT);
});
