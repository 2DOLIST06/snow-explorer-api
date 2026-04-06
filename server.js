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

function normalizeForfaitColumns(columns) {
  if (!Array.isArray(columns)) return [];
  return columns.map((column, index) => {
    const safe = column && typeof column === 'object' ? column : {};
    const generatedId = `c-${index + 1}`;
    return {
      id: safe.id ? String(safe.id) : generatedId,
      label: safe.label == null ? '' : String(safe.label),
      value: safe.value == null ? '' : String(safe.value),
    };
  });
}

function normalizeForfaitItem(item, index) {
  const safe = item && typeof item === 'object' ? item : {};
  const normalizedColumns = normalizeForfaitColumns(safe.columns);

  if (normalizedColumns.length > 0) {
    return {
      ...safe,
      id: safe.id ? String(safe.id) : `f-${index + 1}`,
      columns: normalizedColumns,
    };
  }

  // Compatibilité rétroactive : ancien format title/price/url
  const columns = [];
  if (safe.title != null && safe.title !== '') {
    columns.push({ id: `c-${index + 1}-1`, label: 'title', value: String(safe.title) });
  }
  if (safe.price != null && safe.price !== '') {
    columns.push({ id: `c-${index + 1}-2`, label: 'price', value: String(safe.price) });
  }
  if (safe.url != null && safe.url !== '') {
    columns.push({ id: `c-${index + 1}-3`, label: 'url', value: String(safe.url) });
  }

  return {
    ...safe,
    id: safe.id ? String(safe.id) : `f-${index + 1}`,
    columns,
  };
}

function normalizeWidgetsConfig(widgets) {
  const safeWidgets = widgets && typeof widgets === 'object' ? { ...widgets } : {};
  const forfaits = safeWidgets.forfaits && typeof safeWidgets.forfaits === 'object'
    ? { ...safeWidgets.forfaits }
    : {};
  const items = Array.isArray(forfaits.items) ? forfaits.items : [];

  forfaits.items = items.map((item, index) => normalizeForfaitItem(item, index));
  safeWidgets.forfaits = forfaits;
  return safeWidgets;
}

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
       id,
       name,
       slug,
       latitude,
       longitude,
       website_url,
       cover_image_url,
       logo_url,
       description_md,
       region_id,
       department,
       altitude_base_m,
       altitude_top_m,
       altitude_min_m,
       altitude_max_m,
       ski_area_km,
       lifts_count,
       pistes_count,
       to_char(season_open_date,'YYYY-MM-DD')  as season_open_date,
       to_char(season_close_date,'YYYY-MM-DD') as season_close_date
     from resort
     where slug = $1`,
    [slug]
  );

  if (!r1.rowCount) return null;

  const resort = r1.rows[0];

  const r2 = await q(
    `select *
     from piste
     where resort_id = $1
     order by id asc`,
    [resort.id]
  );

  const r3 = await q(
    `select *
     from lift
     where resort_id = $1
     order by id asc`,
    [resort.id]
  );

  const r4 = await q(
    `select config
     from station_widgets
     where station_slug = $1`,
    [slug]
  );

  const widgets = r4.rowCount
    ? (() => {
        try {
          const parsed = typeof r4.rows[0].config === 'string'
            ? JSON.parse(r4.rows[0].config)
            : (r4.rows[0].config || {});
          return normalizeWidgetsConfig(parsed);
        } catch {
          return {};
        }
      })()
    : {};

  return {
    resort,
    pistes: r2.rows,
    lifts: r3.rows,
    widgets,
  };
}

/* =========================
 * Référentiels (selects UI)
 * =======================*/
app.get('/api/regions', async (_req, res) => {
  try {
    const { rows } = await q(`select id, name, country_code from regions order by name asc`);
    res.json(rows);
  } catch (e) {
    res.status(500).send(e.message);
  }
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
  } catch (e) {
    res.status(500).send(e.message);
  }
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
      from resort
      ${where}
      order by name asc
      limit $${params.length - 1} offset $${params.length};
    `;
    const { rows } = await q(sql, params);
    res.json(rows);
  } catch (e) {
    res.status(500).send(e.message || 'error');
  }
});

/* Détail station par slug (lecture) + alias */
app.get(['/api/resorts/:slug', '/api/ski/resorts/:slug'], async (req, res) => {
  try {
    const data = await loadResortBySlug(req.params.slug);
    if (!data) return res.status(404).send('Not found');
    res.json(data);
  } catch (e) {
    res.status(500).send(e.message);
  }
});

/* =========================
 * Admin Stations / Resorts (+ alias legacy)
 * =======================*/
for (const base of [
  '/api/admin/stations',
  '/api/admin/resorts',
  '/api/ski/admin/stations',
  '/api/ski/admin/resorts'
]) {
  // GET resort + widgets + pistes + lifts
  app.get(`${base}/:slug`, async (req, res) => {
    try {
      const data = await loadResortBySlug(req.params.slug);
      if (!data) return res.status(404).send('Not found');
      res.json(data);
    } catch (e) {
      res.status(500).send(e.message);
    }
  });

  // PATCH resort fields
  app.patch(`${base}/:slug`, async (req, res) => {
    try {
      const { slug } = req.params;
      const found = await loadResortBySlug(slug);
      if (!found) return res.status(404).send('Not found');

      const body = req.body || {};
      const fields = [
        'name',
        'latitude',
        'longitude',
        'website_url',
        'cover_image_url',
        'logo_url',
        'description_md',
        'region_id',
        'department',
        'altitude_base_m',
        'altitude_top_m',
        'altitude_min_m',
        'altitude_max_m',
        'ski_area_km',
        'lifts_count',
        'pistes_count',
        'season_open_date',
        'season_close_date',
        'pistes_small_map_url',
        'pistes_large_map_url'
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
        await q(`update resort set ${set.join(', ')} where slug = $${vals.length}`, vals);
      }

      const data = await loadResortBySlug(slug);
      res.json(data);
    } catch (e) {
      res.status(500).send(`PATCH failed: ${e.message}`);
    }
  });

  // GET widgets
  app.get(`${base}/:slug/widgets`, async (req, res) => {
    try {
      const { slug } = req.params;
      const found = await loadResortBySlug(slug);
      if (!found) return res.status(404).send('Not found');
      res.json(found.widgets || {});
    } catch (e) {
      res.status(500).send(`GET widgets failed: ${e.message}`);
    }
  });

  // PATCH widgets
  app.patch(`${base}/:slug/widgets`, async (req, res) => {
    try {
      const { slug } = req.params;
      const found = await loadResortBySlug(slug);
      if (!found) return res.status(404).send('Not found');

      const widgets = normalizeWidgetsConfig(req.body || {});

      await q(
        `insert into station_widgets (station_slug, config)
         values ($1, $2::jsonb)
         on conflict (station_slug) do update set config = excluded.config`,
        [slug, JSON.stringify(widgets)]
      );

      const data = await loadResortBySlug(slug);
      res.json(data);
    } catch (e) {
      res.status(500).send(`PATCH widgets failed: ${e.message}`);
    }
  });
}

/* =========================
 * Start
 * =======================*/
app.listen(PORT, () => {
  console.log('API listening on http://127.0.0.1:' + PORT);
});
