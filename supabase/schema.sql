-- Ejecutar una vez en Supabase SQL Editor.
create extension if not exists vector with schema extensions;

create table if not exists public.documents (
  id bigint generated always as identity primary key,
  content text not null,
  metadata jsonb not null default '{}'::jsonb,
  embedding extensions.vector(768) not null,
  created_at timestamptz not null default now()
);

create index if not exists documents_embedding_ivfflat
  on public.documents using ivfflat (embedding extensions.vector_cosine_ops) with (lists = 100);

alter table public.documents enable row level security;
revoke all on public.documents from anon, authenticated;

create or replace function public.match_documents(
  query_embedding extensions.vector(768),
  match_threshold float default 0.25,
  match_count int default 5
)
returns table (id bigint, content text, metadata jsonb, similarity float)
language sql stable
set search_path = ''
as $$
  select d.id, d.content, d.metadata,
         1 - (d.embedding operator(extensions.<=>) query_embedding) as similarity
  from public.documents d
  where 1 - (d.embedding operator(extensions.<=>) query_embedding) >= match_threshold
  order by d.embedding operator(extensions.<=>) query_embedding
  limit least(greatest(match_count, 1), 20);
$$;

revoke all on function public.match_documents(extensions.vector, float, int) from public, anon, authenticated;
grant execute on function public.match_documents(extensions.vector, float, int) to service_role;
