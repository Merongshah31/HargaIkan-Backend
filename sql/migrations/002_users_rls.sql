-- HargaIkan v2 migration 002: user tables with owner-only row level security.
--
-- Run after 001 (user_preferences and purchase_history reference species and locations).
-- Re-runnable: `if not exists`, `create or replace`, `drop ... if exists` + `create`.
--
-- Users sign in with Supabase Anonymous Sign-ins, so every app user has the `authenticated`
-- role and a real auth.uid(). anon (no session) gets no access at all. Every policy is
-- `user_id = auth.uid()`, so a user can only see and change their own rows.

create table if not exists public.user_profiles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  consent_allergies boolean not null default false,
  consent_allergies_at timestamptz,
  consent_location boolean not null default false,
  consent_location_at timestamptz,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table if not exists public.user_preferences (
  user_id uuid primary key references auth.users(id) on delete cascade,
  preferred_species_ids uuid[] default '{}',
  allergy_species_ids uuid[] default '{}',
  allergy_terms text[] default '{}',
  allergy_groups text[] default '{}',
  budget_rm_per_kg numeric check (budget_rm_per_kg is null or budget_rm_per_kg > 0),
  preferred_location_id uuid references public.locations(id),
  updated_at timestamptz default now()
);

create table if not exists public.purchase_history (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  species_id uuid references public.species(id),
  location_id uuid references public.locations(id),
  price_rm_per_kg numeric check (price_rm_per_kg > 0),
  qty_kg numeric,
  purchased_on date,
  created_at timestamptz default now()
);

create index if not exists purchase_history_user_idx on public.purchase_history (user_id);

-- ---------------------------------------------------------------------------
-- Row level security and privileges
-- ---------------------------------------------------------------------------

alter table public.user_profiles enable row level security;
alter table public.user_preferences enable row level security;
alter table public.purchase_history enable row level security;

revoke all on table public.user_profiles, public.user_preferences, public.purchase_history from anon;
grant select, insert, update, delete on table
  public.user_profiles, public.user_preferences, public.purchase_history to authenticated;
grant select, insert, update, delete on table
  public.user_profiles, public.user_preferences, public.purchase_history to service_role;

drop policy if exists user_profiles_owner_select on public.user_profiles;
create policy user_profiles_owner_select on public.user_profiles
  for select to authenticated using (user_id = auth.uid());
drop policy if exists user_profiles_owner_insert on public.user_profiles;
create policy user_profiles_owner_insert on public.user_profiles
  for insert to authenticated with check (user_id = auth.uid());
drop policy if exists user_profiles_owner_update on public.user_profiles;
create policy user_profiles_owner_update on public.user_profiles
  for update to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());
drop policy if exists user_profiles_owner_delete on public.user_profiles;
create policy user_profiles_owner_delete on public.user_profiles
  for delete to authenticated using (user_id = auth.uid());

drop policy if exists user_preferences_owner_select on public.user_preferences;
create policy user_preferences_owner_select on public.user_preferences
  for select to authenticated using (user_id = auth.uid());
drop policy if exists user_preferences_owner_insert on public.user_preferences;
create policy user_preferences_owner_insert on public.user_preferences
  for insert to authenticated with check (user_id = auth.uid());
drop policy if exists user_preferences_owner_update on public.user_preferences;
create policy user_preferences_owner_update on public.user_preferences
  for update to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());
drop policy if exists user_preferences_owner_delete on public.user_preferences;
create policy user_preferences_owner_delete on public.user_preferences
  for delete to authenticated using (user_id = auth.uid());

drop policy if exists purchase_history_owner_select on public.purchase_history;
create policy purchase_history_owner_select on public.purchase_history
  for select to authenticated using (user_id = auth.uid());
drop policy if exists purchase_history_owner_insert on public.purchase_history;
create policy purchase_history_owner_insert on public.purchase_history
  for insert to authenticated with check (user_id = auth.uid());
drop policy if exists purchase_history_owner_update on public.purchase_history;
create policy purchase_history_owner_update on public.purchase_history
  for update to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());
drop policy if exists purchase_history_owner_delete on public.purchase_history;
create policy purchase_history_owner_delete on public.purchase_history
  for delete to authenticated using (user_id = auth.uid());

-- ---------------------------------------------------------------------------
-- enforce_consent: the database rejects allergy data or a preferred location without consent
-- ---------------------------------------------------------------------------
-- Runs before every insert or update of user_preferences. A missing user_profiles row counts as
-- "no consent". SECURITY DEFINER so the consent lookup does not depend on the caller's RLS view.
-- The error message is a stable code; the app shows the Bahasa Melayu text from messages.py.

create or replace function public.enforce_consent()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_consent_allergies boolean;
  v_consent_location boolean;
begin
  select p.consent_allergies, p.consent_location
    into v_consent_allergies, v_consent_location
    from public.user_profiles p
   where p.user_id = new.user_id;

  if (coalesce(cardinality(new.allergy_species_ids), 0) > 0
      or coalesce(cardinality(new.allergy_terms), 0) > 0
      or coalesce(cardinality(new.allergy_groups), 0) > 0)
     and not coalesce(v_consent_allergies, false) then
    raise exception 'consent_allergies_required'
      using errcode = 'check_violation',
            detail = 'Allergy fields require user_profiles.consent_allergies = true.';
  end if;

  if new.preferred_location_id is not null and not coalesce(v_consent_location, false) then
    raise exception 'consent_location_required'
      using errcode = 'check_violation',
            detail = 'preferred_location_id requires user_profiles.consent_location = true.';
  end if;

  return new;
end;
$$;

-- No EXECUTE revoke needed: a trigger function cannot be called directly, only fired as a trigger.

drop trigger if exists enforce_consent on public.user_preferences;
create trigger enforce_consent
  before insert or update on public.user_preferences
  for each row execute function public.enforce_consent();
