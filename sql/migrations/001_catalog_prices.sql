-- HargaIkan v2 migration 001: catalog and fact tables, RLS, publish functions, schema report.
--
-- Run in the Supabase SQL editor (as the default `postgres` role), before 002 and 003.
-- Re-runnable: every statement is `if not exists`, `create or replace`, `drop ... if exists`
-- followed by `create`, or an idempotent grant/revoke/enable. Re-running changes no rows.
--
-- Access model:
--   * anon + authenticated: select only. Fact tables (price_records, price_bands,
--     price_predictions, model_metrics) expose only rows with publish_status = 'live'.
--   * service_role (offline scripts/notebooks only): all writes, plus promote_batch,
--     discard_batch and schema_report.

-- ---------------------------------------------------------------------------
-- Catalog tables
-- ---------------------------------------------------------------------------

create table if not exists public.species (
  id uuid primary key,
  code text unique not null,
  canonical_name_ms text not null,
  canonical_name_en text,
  scientific_name text,
  allergen_groups text[] not null default '{}',
  active boolean not null default true
);

create table if not exists public.species_aliases (
  id uuid primary key default gen_random_uuid(),
  species_id uuid not null references public.species(id) on delete cascade,
  alias text not null,
  alias_norm text not null,
  language text not null check (language in ('ms', 'en', 'local', 'raw')),
  unique (species_id, alias_norm)
);

create table if not exists public.fish_profiles (
  species_id uuid primary key references public.species(id) on delete cascade,
  texture text,
  taste text,
  bone_level text,
  cooking_methods text[] default '{}',
  allergen_groups text[] not null default '{}',
  notes text
);
-- FishProfile.allergen_groups (domain model). Kept for databases created from an earlier draft.
alter table public.fish_profiles add column if not exists allergen_groups text[] not null default '{}';

create table if not exists public.fish_similarity (
  from_species_id uuid references public.species(id) on delete cascade,
  to_species_id uuid references public.species(id) on delete cascade,
  similarity_score numeric not null check (similarity_score between 0 and 1),
  reason text not null,
  basis text[] not null,
  verified boolean not null default false,
  primary key (from_species_id, to_species_id),
  check (from_species_id <> to_species_id)
);

create table if not exists public.locations (
  id uuid primary key,
  name text not null,
  level text not null check (level in ('pasar', 'premis', 'kawasan', 'negeri', 'nasional')),
  parent_id uuid references public.locations(id),
  lat double precision,
  lng double precision,
  chain text,
  branch text,
  unique (level, name),
  check ((level = 'nasional') = (parent_id is null))
);

-- ---------------------------------------------------------------------------
-- Fact tables (published in batches: staged -> live -> retired)
-- ---------------------------------------------------------------------------

create table if not exists public.price_records (
  id uuid primary key default gen_random_uuid(),
  species_id uuid not null references public.species(id),
  location_id uuid not null references public.locations(id),
  raw_item_name text not null,
  grade text,
  unit text not null check (unit = 'kg'),
  price_rm_per_kg numeric not null check (price_rm_per_kg > 0),
  price_low numeric,
  price_high numeric,
  price_min numeric,
  price_max numeric,
  record_count int not null check (record_count >= 1),
  date_start date not null,
  date_end date not null check (date_end >= date_start),
  source_name text not null,
  source_snapshot int not null,
  quality_status text not null check (quality_status in
    ('ok', 'invalid_price', 'invalid_count', 'invalid_date', 'inconsistent_range', 'outlier')),
  batch_id uuid not null,
  publish_status text not null check (publish_status in ('staged', 'live', 'retired'))
);

create table if not exists public.price_bands (
  id uuid primary key default gen_random_uuid(),
  species_id uuid not null references public.species(id),
  location_id uuid not null references public.locations(id),
  grade text,
  p25 numeric not null,
  median numeric not null,
  p75 numeric not null,
  record_count int not null check (record_count >= 1),
  confidence_level text not null check (confidence_level in ('rendah', 'sederhana', 'tinggi')),
  period_start date not null,
  period_end date not null,
  band_source text not null,
  batch_id uuid not null,
  publish_status text not null check (publish_status in ('staged', 'live', 'retired')),
  check (p25 > 0 and p25 <= median and median <= p75)
);

create table if not exists public.price_predictions (
  id uuid primary key default gen_random_uuid(),
  species_id uuid not null references public.species(id),
  location_id uuid not null references public.locations(id),
  target_start date not null,
  target_end date not null,
  predicted_median numeric not null check (predicted_median > 0),
  p10 numeric,
  p90 numeric,
  method text not null check (method in ('random_forest', 'baseline_median4')),
  model_version text not null,
  mae numeric not null,
  last_data_date date not null,
  valid_until date not null,
  batch_id uuid not null,
  publish_status text not null check (publish_status in ('staged', 'live', 'retired')),
  check (p10 is null or p10 <= p90)
);

create table if not exists public.model_metrics (
  id uuid primary key default gen_random_uuid(),
  model_version text not null,
  scope text not null check (scope in ('overall', 'series')),
  species_id uuid,
  location_id uuid,
  mae numeric,
  rmse numeric,
  baseline_mae numeric,
  baseline_rmse numeric,
  interval_coverage numeric,
  n_test int,
  decision text not null,
  batch_id uuid not null,
  publish_status text not null check (publish_status in ('staged', 'live', 'retired')),
  created_at timestamptz default now()
);

-- ---------------------------------------------------------------------------
-- Indexes: at most one live row per natural key; batch lookups; live reads
-- ---------------------------------------------------------------------------

create unique index if not exists price_records_live_key on public.price_records
  (source_name, location_id, raw_item_name, date_start, date_end) where publish_status = 'live';
create unique index if not exists price_bands_live_key on public.price_bands
  (species_id, location_id, coalesce(grade, ''), period_start, period_end) where publish_status = 'live';
create unique index if not exists price_predictions_live_key on public.price_predictions
  (species_id, location_id, target_start, target_end) where publish_status = 'live';
create unique index if not exists model_metrics_live_key on public.model_metrics
  (model_version, scope,
   coalesce(species_id, '00000000-0000-0000-0000-000000000000'::uuid),
   coalesce(location_id, '00000000-0000-0000-0000-000000000000'::uuid))
  where publish_status = 'live';

create index if not exists price_records_batch_idx on public.price_records (batch_id, publish_status);
create index if not exists price_bands_batch_idx on public.price_bands (batch_id, publish_status);
create index if not exists price_predictions_batch_idx on public.price_predictions (batch_id, publish_status);
create index if not exists model_metrics_batch_idx on public.model_metrics (batch_id, publish_status);

create index if not exists price_records_series_idx on public.price_records
  (species_id, location_id, date_start) where publish_status = 'live';
create index if not exists price_bands_species_idx on public.price_bands
  (species_id) where publish_status = 'live';
create index if not exists price_predictions_species_idx on public.price_predictions
  (species_id, location_id) where publish_status = 'live';
create index if not exists species_aliases_species_idx on public.species_aliases (species_id);
create index if not exists locations_parent_idx on public.locations (parent_id);

-- ---------------------------------------------------------------------------
-- Row level security
-- ---------------------------------------------------------------------------

alter table public.species enable row level security;
alter table public.species_aliases enable row level security;
alter table public.fish_profiles enable row level security;
alter table public.fish_similarity enable row level security;
alter table public.locations enable row level security;
alter table public.price_records enable row level security;
alter table public.price_bands enable row level security;
alter table public.price_predictions enable row level security;
alter table public.model_metrics enable row level security;

-- Table privileges: the public roles may only read; service_role may write.
revoke insert, update, delete, truncate, references, trigger on table
  public.species, public.species_aliases, public.fish_profiles, public.fish_similarity, public.locations,
  public.price_records, public.price_bands, public.price_predictions, public.model_metrics
  from anon, authenticated;
grant select on table
  public.species, public.species_aliases, public.fish_profiles, public.fish_similarity, public.locations,
  public.price_records, public.price_bands, public.price_predictions, public.model_metrics
  to anon, authenticated;
grant select, insert, update, delete on table
  public.species, public.species_aliases, public.fish_profiles, public.fish_similarity, public.locations,
  public.price_records, public.price_bands, public.price_predictions, public.model_metrics
  to service_role;

-- Catalog tables: public read of every row.
drop policy if exists species_public_read on public.species;
create policy species_public_read on public.species
  for select to anon, authenticated using (true);
drop policy if exists species_aliases_public_read on public.species_aliases;
create policy species_aliases_public_read on public.species_aliases
  for select to anon, authenticated using (true);
drop policy if exists fish_profiles_public_read on public.fish_profiles;
create policy fish_profiles_public_read on public.fish_profiles
  for select to anon, authenticated using (true);
drop policy if exists fish_similarity_public_read on public.fish_similarity;
create policy fish_similarity_public_read on public.fish_similarity
  for select to anon, authenticated using (true);
drop policy if exists locations_public_read on public.locations;
create policy locations_public_read on public.locations
  for select to anon, authenticated using (true);

-- Fact tables: public read of live rows only, so staged and retired rows stay invisible to the app.
drop policy if exists price_records_public_read on public.price_records;
create policy price_records_public_read on public.price_records
  for select to anon, authenticated using (publish_status = 'live');
drop policy if exists price_bands_public_read on public.price_bands;
create policy price_bands_public_read on public.price_bands
  for select to anon, authenticated using (publish_status = 'live');
drop policy if exists price_predictions_public_read on public.price_predictions;
create policy price_predictions_public_read on public.price_predictions
  for select to anon, authenticated using (publish_status = 'live');
drop policy if exists model_metrics_public_read on public.model_metrics;
create policy model_metrics_public_read on public.model_metrics
  for select to anon, authenticated using (publish_status = 'live');

-- Writes: service_role only (it also bypasses RLS in Supabase; the policies make the intent explicit).
drop policy if exists species_service_write on public.species;
create policy species_service_write on public.species
  for all to service_role using (true) with check (true);
drop policy if exists species_aliases_service_write on public.species_aliases;
create policy species_aliases_service_write on public.species_aliases
  for all to service_role using (true) with check (true);
drop policy if exists fish_profiles_service_write on public.fish_profiles;
create policy fish_profiles_service_write on public.fish_profiles
  for all to service_role using (true) with check (true);
drop policy if exists fish_similarity_service_write on public.fish_similarity;
create policy fish_similarity_service_write on public.fish_similarity
  for all to service_role using (true) with check (true);
drop policy if exists locations_service_write on public.locations;
create policy locations_service_write on public.locations
  for all to service_role using (true) with check (true);
drop policy if exists price_records_service_write on public.price_records;
create policy price_records_service_write on public.price_records
  for all to service_role using (true) with check (true);
drop policy if exists price_bands_service_write on public.price_bands;
create policy price_bands_service_write on public.price_bands
  for all to service_role using (true) with check (true);
drop policy if exists price_predictions_service_write on public.price_predictions;
create policy price_predictions_service_write on public.price_predictions
  for all to service_role using (true) with check (true);
drop policy if exists model_metrics_service_write on public.model_metrics;
create policy model_metrics_service_write on public.model_metrics
  for all to service_role using (true) with check (true);

-- ---------------------------------------------------------------------------
-- promote_batch(p_batch): staged -> live swap in one transaction
-- ---------------------------------------------------------------------------
-- For every fact table that has staged rows of p_batch ("touched" tables):
--   1. delete rows already 'retired' (the previous rollback copy),
--   2. set the current 'live' rows to 'retired' (kept for a one-step manual rollback),
--   3. set the staged rows of p_batch to 'live'.
-- Tables without staged rows of p_batch are left unchanged. A batch with no staged rows at all
-- raises an error, so a mistaken call can never empty the live data. The function body runs in
-- the caller's single transaction: any error (e.g. a duplicate live natural key) rolls back every
-- step and the previous live rows stay live.

create or replace function public.promote_batch(p_batch uuid)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_tables constant text[] := array['price_records', 'price_bands', 'price_predictions', 'model_metrics'];
  v_table text;
  v_staged bigint;
  v_deleted bigint;
  v_retired bigint;
  v_promoted bigint;
  v_report jsonb := '{}'::jsonb;
begin
  if p_batch is null then
    raise exception 'promote_batch: p_batch must not be null' using errcode = '22004';
  end if;

  -- Serialise concurrent promote/discard calls.
  perform pg_advisory_xact_lock(hashtext('hargaikan.publish'));

  foreach v_table in array v_tables loop
    execute format(
      'select count(*) from public.%I where batch_id = $1 and publish_status = ''staged''', v_table)
      into v_staged using p_batch;
    if v_staged = 0 then
      continue;
    end if;

    execute format('delete from public.%I where publish_status = ''retired''', v_table);
    get diagnostics v_deleted = row_count;

    execute format(
      'update public.%I set publish_status = ''retired'' where publish_status = ''live'' and batch_id <> $1',
      v_table) using p_batch;
    get diagnostics v_retired = row_count;

    execute format(
      'update public.%I set publish_status = ''live'' where batch_id = $1 and publish_status = ''staged''',
      v_table) using p_batch;
    get diagnostics v_promoted = row_count;

    v_report := v_report || jsonb_build_object(v_table, jsonb_build_object(
      'deleted_retired', v_deleted, 'retired', v_retired, 'promoted', v_promoted));
  end loop;

  if v_report = '{}'::jsonb then
    raise exception 'promote_batch: batch % has no staged rows', p_batch using errcode = 'P0002';
  end if;

  return jsonb_build_object('batch_id', p_batch, 'tables', v_report);
end;
$$;

revoke execute on function public.promote_batch(uuid) from public, anon, authenticated;
grant execute on function public.promote_batch(uuid) to service_role;

-- ---------------------------------------------------------------------------
-- discard_batch(p_batch): delete the staged rows of a batch (live/retired rows untouched)
-- ---------------------------------------------------------------------------

create or replace function public.discard_batch(p_batch uuid)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_tables constant text[] := array['price_records', 'price_bands', 'price_predictions', 'model_metrics'];
  v_table text;
  v_deleted bigint;
  v_report jsonb := '{}'::jsonb;
begin
  if p_batch is null then
    raise exception 'discard_batch: p_batch must not be null' using errcode = '22004';
  end if;

  perform pg_advisory_xact_lock(hashtext('hargaikan.publish'));

  foreach v_table in array v_tables loop
    execute format(
      'delete from public.%I where batch_id = $1 and publish_status = ''staged''', v_table)
      using p_batch;
    get diagnostics v_deleted = row_count;
    v_report := v_report || jsonb_build_object(v_table, v_deleted);
  end loop;

  return jsonb_build_object('batch_id', p_batch, 'deleted', v_report);
end;
$$;

revoke execute on function public.discard_batch(uuid) from public, anon, authenticated;
grant execute on function public.discard_batch(uuid) to service_role;

-- ---------------------------------------------------------------------------
-- schema_report(): tables, columns and RLS flags of schema public, for scripts/check_schema.py
-- ---------------------------------------------------------------------------
-- Shape: {"tables": {"<table>": {"columns": ["<col>", ...], "rls": true|false}, ...}}
-- Reads pg_catalog directly so the result does not depend on the caller's column privileges.

create or replace function public.schema_report()
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  select jsonb_build_object('tables', coalesce(jsonb_object_agg(t.relname, jsonb_build_object(
           'columns', t.cols, 'rls', t.rls)), '{}'::jsonb))
  from (
    select c.relname::text as relname,
           c.relrowsecurity as rls,
           coalesce((select jsonb_agg(a.attname::text order by a.attnum)
                     from pg_catalog.pg_attribute a
                     where a.attrelid = c.oid and a.attnum > 0 and not a.attisdropped), '[]'::jsonb) as cols
    from pg_catalog.pg_class c
    join pg_catalog.pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public' and c.relkind in ('r', 'p')
  ) t;
$$;

revoke execute on function public.schema_report() from public, anon, authenticated;
grant execute on function public.schema_report() to service_role;
