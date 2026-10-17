-- プロフィール
create table public.profiles (
  id bigint primary key generated always as identity,
  name text not null,
  email text unique,
  created_at timestamptz default now()
);

create table public.audit_logs (
  id bigserial primary key,
  profile_id bigint references public.profiles (id),
  action text not null check (action <> '')
);

create index audit_logs_profile_idx on public.audit_logs (profile_id);

create trigger profiles_audit after update on public.profiles
  for each row execute function public.log_profile_change();

select cron.schedule('purge-audit', '0 4 * * *', $$ delete from public.audit_logs where id < 0 $$);
