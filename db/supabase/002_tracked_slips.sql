-- PotentialPro: tracked ACCUMULATORS in My Record (28 Sep 2026).
-- Run ONCE in Supabase: Dashboard -> SQL Editor -> New query -> paste -> Run.
-- Safe to re-run. Needs 001_tracked_picks.sql to have been run first.
--
-- WHY A SEPARATE TABLE: most punters here play accumulators, where one losing
-- leg loses the whole slip. Tracking the legs as separate single picks would
-- report a slip that lost as "4 of 5 won" - true per leg, and exactly the
-- flattering number this product refuses to show. A slip is one bet, stored as
-- one row, settled as one result.
--
-- legs is a JSON array, one object per leg, written by webapp/js/auth.js:
--   {match_id, home_team, away_team, league, kickoff_utc, market, selection,
--    probability, odds}
-- odds = the SportyBet price when the slip was tracked (null if not listed).
-- Legs are settled by the API (/api/slip/settle) against the same results as
-- the public record; nothing here is computed in the database.

create table if not exists public.tracked_slips (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null default auth.uid()
              references auth.users (id) on delete cascade,
  source      text not null check (source in ('builder', 'checker')),
  legs        jsonb not null
              check (jsonb_typeof(legs) = 'array'
                     and jsonb_array_length(legs) between 1 and 30),
  total_odds  numeric check (total_odds is null or total_odds > 1),  -- only when EVERY leg was priced
  stake       numeric check (stake is null or stake >= 0),
  created_at  timestamptz not null default now()
);

create index if not exists tracked_slips_user_idx on public.tracked_slips (user_id, created_at desc);

alter table public.tracked_slips enable row level security;

-- Same lesson as 001: new projects don't auto-grant tables to the API roles.
grant select, insert, update, delete on public.tracked_slips to authenticated;

drop policy if exists "own slips: select" on public.tracked_slips;
drop policy if exists "own slips: insert" on public.tracked_slips;
drop policy if exists "own slips: update" on public.tracked_slips;
drop policy if exists "own slips: delete" on public.tracked_slips;

create policy "own slips: select" on public.tracked_slips
  for select to authenticated using ((select auth.uid()) = user_id);
create policy "own slips: insert" on public.tracked_slips
  for insert to authenticated with check ((select auth.uid()) = user_id);
create policy "own slips: update" on public.tracked_slips
  for update to authenticated using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);
create policy "own slips: delete" on public.tracked_slips
  for delete to authenticated using ((select auth.uid()) = user_id);

-- delete_my_account() (001) removes the auth user; slips go with it via
-- on delete cascade above, so no change is needed there.
