-- HargaIkan v2 migration 003: tables for deferred outside services.
--
-- Run after 001 and 002. v2 writes no rows to these tables (Requirement 25.1); they exist so
-- later phases need no schema change. Re-runnable like 001 and 002.
--
-- weather_observations, weather_forecasts, seasonal_events: public read, service_role write.
-- travel_estimates: owner-only (user_id = auth.uid()), part of the user's "delete all" data.

create table if not exists public.weather_observations (
  id uuid primary key default gen_random_uuid(),
  location_id uuid not null references public.locations(id),
  observed_at timestamptz not null,
  metrics jsonb not null default '{}'::jsonb,
  source text not null
);

create table if not exists public.weather_forecasts (
  id uuid primary key default gen_random_uuid(),
  location_id uuid not null references public.locations(id),
  forecast_for timestamptz not null,
  metrics jsonb not null default '{}'::jsonb,
  source text not null,
  fetched_at timestamptz not null default now(),
  expires_at timestamptz not null,
  check (expires_at >= fetched_at)
);

create table if not exists public.seasonal_events (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  kind text not null,
  start_date date not null,
  end_date date not null,
  source text not null,
  check (end_date >= start_date)
);

create table if not exists public.travel_estimates (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  origin_location_id uuid references public.locations(id),
  destination_location_id uuid references public.locations(id),
  mode text not null,
  distance_km numeric check (distance_km is null or distance_km >= 0),
  components jsonb not null default '{}'::jsonb,
  source text not null,
  created_at timestamptz default now()
);

create index if not exists weather_observations_location_idx on public.weather_observations (location_id, observed_at);
create index if not exists weather_forecasts_location_idx on public.weather_forecasts (location_id, forecast_for);
create index if not exists travel_estimates_user_idx on public.travel_estimates (user_id);

-- ---------------------------------------------------------------------------
-- Row level security and privileges
-- ---------------------------------------------------------------------------

alter table public.weather_observations enable row level security;
alter table public.weather_forecasts enable row level security;
alter table public.seasonal_events enable row level security;
alter table public.travel_estimates enable row level security;

revoke insert, update, delete, truncate, references, trigger on table
  public.weather_observations, public.weather_forecasts, public.seasonal_events
  from anon, authenticated;
grant select on table
  public.weather_observations, public.weather_forecasts, public.seasonal_events
  to anon, authenticated;
grant select, insert, update, delete on table
  public.weather_observations, public.weather_forecasts, public.seasonal_events
  to service_role;

revoke all on table public.travel_estimates from anon;
grant select, insert, update, delete on table public.travel_estimates to authenticated;
grant select, insert, update, delete on table public.travel_estimates to service_role;

-- Public read
drop policy if exists weather_observations_public_read on public.weather_observations;
create policy weather_observations_public_read on public.weather_observations
  for select to anon, authenticated using (true);
drop policy if exists weather_forecasts_public_read on public.weather_forecasts;
create policy weather_forecasts_public_read on public.weather_forecasts
  for select to anon, authenticated using (true);
drop policy if exists seasonal_events_public_read on public.seasonal_events;
create policy seasonal_events_public_read on public.seasonal_events
  for select to anon, authenticated using (true);

-- Service write
drop policy if exists weather_observations_service_write on public.weather_observations;
create policy weather_observations_service_write on public.weather_observations
  for all to service_role using (true) with check (true);
drop policy if exists weather_forecasts_service_write on public.weather_forecasts;
create policy weather_forecasts_service_write on public.weather_forecasts
  for all to service_role using (true) with check (true);
drop policy if exists seasonal_events_service_write on public.seasonal_events;
create policy seasonal_events_service_write on public.seasonal_events
  for all to service_role using (true) with check (true);

-- Owner-only travel estimates
drop policy if exists travel_estimates_owner_select on public.travel_estimates;
create policy travel_estimates_owner_select on public.travel_estimates
  for select to authenticated using (user_id = auth.uid());
drop policy if exists travel_estimates_owner_insert on public.travel_estimates;
create policy travel_estimates_owner_insert on public.travel_estimates
  for insert to authenticated with check (user_id = auth.uid());
drop policy if exists travel_estimates_owner_update on public.travel_estimates;
create policy travel_estimates_owner_update on public.travel_estimates
  for update to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());
drop policy if exists travel_estimates_owner_delete on public.travel_estimates;
create policy travel_estimates_owner_delete on public.travel_estimates
  for delete to authenticated using (user_id = auth.uid());
