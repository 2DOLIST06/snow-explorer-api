-- Regions
create table if not exists regions (
  id text primary key,
  name text not null,
  slug text not null unique,
  country_code varchar(2) not null default 'FR',
  seo_text text,
  meta_title varchar(70),
  meta_description varchar(170),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
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
  season_close_date date,
  is_active boolean not null default true
);

-- Widgets par station (JSONB)
create table if not exists resort_widgets (
  resort_id uuid primary key references resorts(id) on delete cascade,
  data jsonb not null default '{}'
);

-- Extensions utiles
create extension if not exists "pgcrypto";

-- Données de base (optionnelles)
insert into regions (id, name, slug, country_code) values
  ('provence-alpes-cote-d-azur','Provence-Alpes-Côte d’Azur','provence-alpes-cote-d-azur','FR'),
  ('auvergne-rhone-alpes','Auvergne-Rhône-Alpes','auvergne-rhone-alpes','FR')
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
