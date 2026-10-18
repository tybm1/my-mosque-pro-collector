-- My Mosque Pro — jamaat times backend
-- Run this once in Supabase: Dashboard → SQL Editor → New query → paste → Run.

-- ─────────────────────────────────────────────────────────────
-- Mosques (Sunni only — enforced by the import scripts, not the database)
-- ─────────────────────────────────────────────────────────────
create table if not exists mosques (
  id            integer primary key,          -- same id the app uses
  name          text not null,
  address       text,
  town          text not null,
  area          text,                         -- London borough / district
  postcode      text not null,
  lat           double precision not null,
  lng           double precision not null,
  phone         text,
  website       text,
  facebook      text,
  tradition     text,                         -- Deobandi, Barelvi, Salafi …
  asr_method    text not null default 'shafi' check (asr_method in ('shafi','hanafi')),
  sisters       text check (sisters in ('yes','no')),
  capacity      text,
  tier          text not null default 'community' check (tier in ('verified','community')),
  source_note   text,                         -- where the listing came from
  active        boolean not null default true,
  updated_at    timestamptz not null default now()
);
alter table mosques add column if not exists facebook text;   -- safe to re-run on an existing database
alter table mosques add column if not exists place_id text;   -- Google Places id (the only Places data we may keep)
alter table mosques add column if not exists places_checked_at timestamptz;

create index if not exists mosques_town_idx on mosques (town);

-- ─────────────────────────────────────────────────────────────
-- Timetable sources: where each mosque publishes its jamaat times
-- ─────────────────────────────────────────────────────────────
create table if not exists sources (
  id              bigint generated always as identity primary key,
  mosque_id       integer not null references mosques(id) on delete cascade,
  url             text not null,
  kind            text not null default 'auto' check (kind in ('auto','html','pdf','image','masjidbox','mawaqit')),
  -- Some mosques post a new PDF each month at a predictable address.
  -- Use {YYYY}, {MM}, {MON} (e.g. Oct), {MONTH} (e.g. October) and the
  -- collector fills in the current and next month.
  url_pattern     text,
  enabled         boolean not null default true,
  blocked_reason  text,                       -- e.g. 'robots.txt disallows' — never fetched while set
  last_checked    timestamptz,
  last_changed    timestamptz,
  last_hash       text,                       -- content fingerprint: AI only runs when this changes
  last_status     text,                       -- ok / unchanged / no_times / invalid / error / blocked
  last_message    text,
  fail_count      integer not null default 0,
  created_at      timestamptz not null default now(),
  unique (mosque_id, url)
);
-- Learned recipes (collector.learn): how to read this site's timetable without AI.
alter table sources add column if not exists recipe            jsonb;
alter table sources add column if not exists recipe_status     text;        -- null = not learned yet / ok / broken / none
alter table sources add column if not exists recipe_learned_at timestamptz;
create index if not exists sources_recipe_idx on sources (recipe_status);

-- ─────────────────────────────────────────────────────────────
-- Jamaat times: one row per mosque per day
-- ─────────────────────────────────────────────────────────────
create table if not exists jamaat_times (
  mosque_id   integer not null references mosques(id) on delete cascade,
  day         date not null,
  fajr        time,
  dhuhr       time,
  asr         time,
  maghrib     time,
  isha        time,
  jumuah      text,                           -- free text, e.g. '13:15, 14:00'
  source_id   bigint references sources(id) on delete set null,
  source_url  text not null,                  -- shown in the app as "From …"
  fetched_at  timestamptz not null default now(),
  primary key (mosque_id, day)
);
create index if not exists jamaat_day_idx on jamaat_times (day);

-- ─────────────────────────────────────────────────────────────
-- Run log: one row per collector run, so you can see what happened
-- ─────────────────────────────────────────────────────────────
create table if not exists collector_runs (
  id           bigint generated always as identity primary key,
  started_at   timestamptz not null default now(),
  finished_at  timestamptz,
  checked      integer default 0,
  changed      integer default 0,
  days_saved   integer default 0,
  failed       integer default 0,
  skipped      integer default 0,
  ai_calls     integer default 0,
  notes        text
);

-- ─────────────────────────────────────────────────────────────
-- Security: the app (anonymous key) can only READ mosques and times.
-- Only the collector (service key, kept secret in GitHub) can write.
-- ─────────────────────────────────────────────────────────────
alter table mosques        enable row level security;
alter table sources        enable row level security;
alter table jamaat_times   enable row level security;
alter table collector_runs enable row level security;

drop policy if exists "public read mosques" on mosques;
create policy "public read mosques" on mosques for select using (active);

drop policy if exists "public read jamaat" on jamaat_times;
create policy "public read jamaat" on jamaat_times for select using (true);

-- sources and collector_runs have no public policies: invisible to the app.

-- Today's and upcoming times, newest source per mosque — what the app reads.
create or replace view jamaat_upcoming with (security_invoker = true) as
  select j.mosque_id, j.day, j.fajr, j.dhuhr, j.asr, j.maghrib, j.isha, j.jumuah, j.source_url, j.fetched_at
  from jamaat_times j
  where j.day between (now() at time zone 'Europe/London')::date - 1
                  and (now() at time zone 'Europe/London')::date + 35;
grant select on jamaat_upcoming to anon;

-- Housekeeping: drop rows older than 60 days (called by the collector).
create or replace function prune_old_jamaat() returns void language sql
  set search_path = public as $$
  delete from jamaat_times where day < (now() at time zone 'Europe/London')::date - 60;
$$;
revoke execute on function prune_old_jamaat() from public, anon, authenticated;   -- collector only

-- ─────────────────────────────────────────────────────────────
-- Mosque profiles read from their websites (collector.profile)
-- ─────────────────────────────────────────────────────────────
alter table mosques add column if not exists jumuah             jsonb;   -- {slots:[{name,khutbah,jamaat}], note, src, url, at}
alter table mosques add column if not exists facilities         jsonb;   -- ["Wudu area","Parking",...]
alter table mosques add column if not exists sisters_web        text check (sisters_web in ('yes','no'));
alter table mosques add column if not exists sisters_web_note   text;
alter table mosques add column if not exists profile_src        text;
alter table mosques add column if not exists profile_hash       text;
alter table mosques add column if not exists profile_at         timestamptz;
alter table mosques add column if not exists profile_checked_at timestamptz;
alter table mosques add column if not exists profile_priority   integer not null default 0;
alter table sources add column if not exists priority integer not null default 0;   -- learned first when higher

-- Updates sent from the app (checked by the app owner)
create table if not exists submissions (
  id bigint generated always as identity primary key,
  mosque_id integer not null references mosques(id) on delete cascade,
  kind text not null check (kind in ('change','claim')),
  prayer text check (prayer in ('Fajr','Dhuhr','Asr','Maghrib','Isha','Jumuah')),
  time text check (time ~ '^[0-2][0-9]:[0-5][0-9]$'),
  from_date date, role text, name text, contact text,
  status text not null default 'pending' check (status in ('pending','approved','rejected')),
  created_at timestamptz not null default now()
);
alter table submissions enable row level security;

-- Listing checks (collector.verify, Google Places): only the place id and our own outcome are kept
alter table mosques add column if not exists verify_status text check (verify_status in ('open','renamed?','closed_temp','closed','not_found'));
alter table mosques add column if not exists verify_note   text;
alter table mosques add column if not exists verified_at   timestamptz;
-- the app drops hidden listings (closed, duplicates) without needing an app update
create policy "app sees hidden listings" on mosques for select to anon using (active = false);
