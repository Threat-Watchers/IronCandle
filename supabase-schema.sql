-- IronCandle Trading OS Trading OS — Supabase schema
-- Run in Supabase SQL Editor.
-- RLS is deliberately owner-scoped: users can only read/write their own trades.

create table if not exists public.trades (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  entry_date date not null,
  exit_date date not null,
  symbol text not null,
  direction text not null check (direction in ('Long Delivery','Long Intraday','Short Intraday')),
  setup text,
  timeframe text,
  entry_price numeric not null,
  stop_price numeric not null,
  target_price numeric,
  exit_price numeric not null,
  qty integer not null check (qty > 0),
  broker text not null default 'Dhan',
  notes text,
  brokerage numeric default 0,
  stt numeric default 0,
  exchange_txn numeric default 0,
  sebi numeric default 0,
  ipft numeric default 0,
  stamp_duty numeric default 0,
  gst numeric default 0,
  dp_charge numeric default 0,
  total_charges numeric default 0,
  gross_pnl numeric default 0,
  net_pnl numeric default 0,
  risk_amount numeric default 0,
  r_multiple numeric default 0,
  outcome text,
  created_at timestamptz not null default now()
);

alter table public.trades enable row level security;

drop policy if exists "trades_select_own" on public.trades;
drop policy if exists "trades_insert_own" on public.trades;
drop policy if exists "trades_update_own" on public.trades;
drop policy if exists "trades_delete_own" on public.trades;

create policy "trades_select_own" on public.trades for select using (auth.uid() = user_id);
create policy "trades_insert_own" on public.trades for insert with check (auth.uid() = user_id);
create policy "trades_update_own" on public.trades for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "trades_delete_own" on public.trades for delete using (auth.uid() = user_id);

create index if not exists trades_user_exit_date_idx on public.trades(user_id, exit_date desc);
