-- PotentialPro user accounts: per-user "My Record" (27 Sep 2026).
-- Run ONCE in Supabase: Dashboard -> SQL Editor -> New query -> paste -> Run.
--
-- WHY THIS IS NOT IN db/sports_predictor.db: that file is committed to the
-- repo and rewritten by GitHub Actions every day. User rows there would be
-- readable by anyone with repo access and overwritten by the next run
-- (docs/history.md "What's next" item 8). Supabase holds ONLY user-owned data; the
-- model, its predictions and the public track record stay where they are.
--
-- WHAT IS STORED: the picks a signed-in user chose to track, with an optional
-- stake. Identity lives in Supabase Auth (Google sign-in): we keep no
-- passwords and no profile beyond what auth.users already holds.

create table if not exists public.tracked_picks (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null default auth.uid()
              references auth.users (id) on delete cascade,
  match_id    text not null,                 -- matches.match_id in the ledger
  home_team   text not null,
  away_team   text not null,
  league      text,
  kickoff_utc text,                          -- 'YYYY-MM-DD HH:MM', as the API serves it
  selection   text not null                  -- 'Home Win' | 'Draw' | 'Away Win'
              check (selection in ('Home Win', 'Draw', 'Away Win')),
  model_prob  numeric check (model_prob between 0 and 1),
  odds        numeric check (odds is null or odds > 1),   -- SportyBet price when tracked
  stake       numeric check (stake is null or stake >= 0),
  created_at  timestamptz not null default now(),
  unique (user_id, match_id, selection)
);

create index if not exists tracked_picks_user_idx on public.tracked_picks (user_id, created_at desc);

-- Row-level security: this is what makes the public (publishable) key safe to
-- ship in the web app. Every row is visible and editable ONLY by its owner.
alter table public.tracked_picks enable row level security;

-- Table privileges. Newer Supabase projects do NOT auto-grant new tables to
-- the API roles, so without this line even a signed-in user gets
-- "permission denied for table tracked_picks" (seen 28 Sep 2026: anon was
-- refused at the privilege level, not by RLS). Only `authenticated` is
-- granted; anonymous visitors get nothing. RLS above still restricts every
-- signed-in user to their OWN rows.
grant select, insert, update, delete on public.tracked_picks to authenticated;

drop policy if exists "own rows: select" on public.tracked_picks;
drop policy if exists "own rows: insert" on public.tracked_picks;
drop policy if exists "own rows: update" on public.tracked_picks;
drop policy if exists "own rows: delete" on public.tracked_picks;

create policy "own rows: select" on public.tracked_picks
  for select to authenticated using ((select auth.uid()) = user_id);
create policy "own rows: insert" on public.tracked_picks
  for insert to authenticated with check ((select auth.uid()) = user_id);
create policy "own rows: update" on public.tracked_picks
  for update to authenticated using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);
create policy "own rows: delete" on public.tracked_picks
  for delete to authenticated using ((select auth.uid()) = user_id);

-- "Delete my account": removes the auth user; tracked_picks go with it
-- (on delete cascade). SECURITY DEFINER because a signed-in user cannot
-- delete from auth.users directly - but it can only ever delete THEMSELVES.
create or replace function public.delete_my_account()
returns void
language sql
security definer
set search_path = ''
as $$
  delete from auth.users where id = auth.uid();
$$;

revoke all on function public.delete_my_account() from public, anon;
grant execute on function public.delete_my_account() to authenticated;
