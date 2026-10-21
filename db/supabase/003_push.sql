-- PotentialPro: web push for tracked picks and slips (21 Oct 2026).
-- Run ONCE in Supabase: Dashboard -> SQL Editor -> New query -> paste -> Run.
-- Safe to re-run. Needs 001 and 002 to have been run first.
--
-- push_subscriptions: one row per device a signed-in user turned
-- notifications on for. Written by webapp/js/auth.js, read by the Worker
-- (worker/push.js) with the SERVICE key. Same row-level security as the
-- tracked tables: a user sees and edits only their own devices.
--
-- push_sent: what the Worker already announced, so each pick/slip gets each
-- notification once. Worker-only: RLS on and NO grants, so the publishable
-- key cannot read or write it at all (the service key bypasses RLS).

create table if not exists public.push_subscriptions (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null default auth.uid()
              references auth.users (id) on delete cascade,
  endpoint    text not null check (endpoint like 'https://%'),
  p256dh      text not null,
  auth        text not null,
  created_at  timestamptz not null default now(),
  unique (user_id, endpoint)
);

alter table public.push_subscriptions enable row level security;
grant select, insert, update, delete on public.push_subscriptions to authenticated;

drop policy if exists "own subs: select" on public.push_subscriptions;
drop policy if exists "own subs: insert" on public.push_subscriptions;
drop policy if exists "own subs: update" on public.push_subscriptions;
drop policy if exists "own subs: delete" on public.push_subscriptions;

create policy "own subs: select" on public.push_subscriptions
  for select to authenticated using ((select auth.uid()) = user_id);
create policy "own subs: insert" on public.push_subscriptions
  for insert to authenticated with check ((select auth.uid()) = user_id);
create policy "own subs: update" on public.push_subscriptions
  for update to authenticated using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);
create policy "own subs: delete" on public.push_subscriptions
  for delete to authenticated using ((select auth.uid()) = user_id);

create table if not exists public.push_sent (
  user_id  uuid not null references auth.users (id) on delete cascade,
  kind     text not null check (kind in ('kickoff', 'settled')),
  ref      uuid not null,                     -- tracked_picks.id or tracked_slips.id
  sent_at  timestamptz not null default now(),
  primary key (user_id, kind, ref)
);

alter table public.push_sent enable row level security;
revoke all on public.push_sent from anon, authenticated;

-- The Worker's service key bypasses RLS but NOT table privileges, and new
-- projects grant nothing by default (the 001 gotcha) - so grant it exactly
-- what worker/push.js does.
grant select, delete on public.push_subscriptions to service_role;
grant select, insert on public.push_sent to service_role;
grant select on public.tracked_picks, public.tracked_slips to service_role;
