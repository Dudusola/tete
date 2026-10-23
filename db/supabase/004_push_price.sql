-- Price-moved alerts (23 Oct 2026): allow push_sent.kind = 'price'.
-- Run once in the Supabase SQL editor. Until it runs, worker/push.js skips
-- price alerts (and logs "price alerts off"); kickoff/settled are unaffected.
alter table push_sent drop constraint if exists push_sent_kind_check;
alter table push_sent add constraint push_sent_kind_check
  check (kind in ('kickoff', 'settled', 'price'));
