-- PotentialPro: notification settings + the morning alert (23 Oct 2026).
-- Run ONCE in Supabase: Dashboard -> SQL Editor -> New query -> paste -> Run.
-- Safe to re-run. Needs 003 (and 004) first.
--
-- push_prefs: one row per user. No row = every alert on, no favourite
-- leagues - so nobody who turned notifications on before this existed loses
-- anything. Written by webapp/js/auth.js (account settings, Matches stars),
-- read by worker/push.js with the SERVICE key. Own-row RLS like the others.

create table if not exists public.push_prefs (
  user_id     uuid primary key default auth.uid()
              references auth.users (id) on delete cascade,
  morning     boolean not null default true,   -- "today's picks are out", ~07:00 Lagos
  leagues_on  boolean not null default true,   -- the favourite-leagues line in that alert
  kickoff     boolean not null default true,
  price       boolean not null default true,
  results     boolean not null default true,
  leagues     text[]  not null default '{}'    -- matches.league values starred on Matches
              check (cardinality(leagues) <= 50),
  updated_at  timestamptz not null default now()
);

alter table public.push_prefs enable row level security;
grant select, insert, update, delete on public.push_prefs to authenticated;

drop policy if exists "own prefs: select" on public.push_prefs;
drop policy if exists "own prefs: insert" on public.push_prefs;
drop policy if exists "own prefs: update" on public.push_prefs;
drop policy if exists "own prefs: delete" on public.push_prefs;

create policy "own prefs: select" on public.push_prefs
  for select to authenticated using ((select auth.uid()) = user_id);
create policy "own prefs: insert" on public.push_prefs
  for insert to authenticated with check ((select auth.uid()) = user_id);
create policy "own prefs: update" on public.push_prefs
  for update to authenticated using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);
create policy "own prefs: delete" on public.push_prefs
  for delete to authenticated using ((select auth.uid()) = user_id);

-- The morning alert is recorded once per user per day in push_sent.
alter table public.push_sent drop constraint if exists push_sent_kind_check;
alter table public.push_sent add constraint push_sent_kind_check
  check (kind in ('kickoff', 'settled', 'price', 'morning'));
