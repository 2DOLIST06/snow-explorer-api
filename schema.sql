-- Regions
create table if not exists regions (
  id text primary key,
  name text not null,
  country_code text default 'FR'
);

-- Departments
create table if not exists departments (
  code text,
  name text not null,
  region_id text references regions(id) on delete set null,
  primary key (name, region_id)
);

-- Resorts / Stations
create table if not exists resorts (
  id uuid default gen_random_uuid() primary key,
  name text,
  slug text unique not null,
  latitude double precision,
  longitude double precision,
  website_url text,
  cover_image_url text,
  description_md text,
  region_id text references regions(id) on delete set null,
  department text,
  altitude_min_m integer,
  altitude_max_m integer,
  season_open_date date,
  season_close_date date
);

-- Widgets par station (JSONB)
create table if not exists resort_widgets (
  resort_id uuid primary key references resorts(id) on delete cascade,
  data jsonb not null default '{}'
);

-- Extensions utiles
create extension if not exists "pgcrypto";

-- Données de base (optionnelles)
insert into regions (id, name, country_code) values
  ('provence-alpes-cote-d-azur','Provence-Alpes-Côte d’Azur','FR'),
  ('auvergne-rhone-alpes','Auvergne-Rhône-Alpes','FR')
on conflict (id) do nothing;

insert into departments (code, name, region_id) values
  ('06','Alpes-Maritimes','provence-alpes-cote-d-azur'),
  ('73','Savoie','auvergne-rhone-alpes'),
  ('74','Haute-Savoie','auvergne-rhone-alpes')
on conflict do nothing;

insert into resorts (name, slug, region_id, department, latitude, longitude)
values ('Station Démo','station-demo','auvergne-rhone-alpes','Savoie',45.29,6.65)
on conflict (slug) do nothing;

insert into resort_widgets (resort_id, data)
select id, '{}'::jsonb from resorts where slug='station-demo'
on conflict (resort_id) do nothing;
