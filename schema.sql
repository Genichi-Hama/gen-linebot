-- メッセージ履歴テーブル
create table messages (
  id uuid default gen_random_uuid() primary key,
  user_id text not null,
  role text not null check (role in ('user', 'assistant')),
  content text not null,
  created_at timestamptz default now()
);

-- 記憶サマリーテーブル
create table memories (
  id uuid default gen_random_uuid() primary key,
  user_id text not null,
  summary text not null,
  created_at timestamptz default now()
);

-- インデックス
create index on messages (user_id, created_at desc);
create index on memories (user_id, created_at desc);
