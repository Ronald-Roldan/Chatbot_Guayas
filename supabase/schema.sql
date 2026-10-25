-- Ejecutar una vez en Supabase SQL Editor.
create extension if not exists vector with schema extensions;

create table if not exists public.documents (
  id bigint generated always as identity primary key,
  content text not null,
  metadata jsonb not null default '{}'::jsonb,
  embedding extensions.vector(768) not null,
  created_at timestamptz not null default now()
);

-- Keep exact vector search for this small corpus. IVFFlat with 100 lists and
-- the default probes=1 can miss relevant chunks when the table has only a few
-- hundred rows. Reconsider an ANN index after the corpus grows substantially.

drop function if exists public.match_documents_hybrid(extensions.vector, text, int, float, int);

alter table public.documents enable row level security;
revoke all on public.documents from anon, authenticated;

create or replace function public.match_documents_hybrid(
  query_embedding extensions.vector(768),
  query_text text,
  match_threshold float default 0.25,
  match_count int default 5
)
returns table (id bigint, content text, metadata jsonb, similarity float)
language sql stable
set search_path = ''
as $$
  with query_terms as (
    select nullif(string_agg(distinct term, ' | '), '')::tsquery as tsquery
    from regexp_split_to_table(lower(coalesce(query_text, '')), '[^[:alnum:]]+') as terms(term)
    where length(term) >= 3
  ),
  ranked as (
    select d.id,
           d.metadata->>'source' as source_name,
           coalesce(d.metadata->>'document_id', d.metadata->>'source', d.id::text) as document_key,
           case when d.metadata->>'chunk' ~ '^[0-9]+$'
                then (d.metadata->>'chunk')::int end as chunk_number,
           d.content,
           d.metadata,
           1 - (d.embedding operator(extensions.<=>) query_embedding) as similarity
    from public.documents d
    where 1 - (d.embedding operator(extensions.<=>) query_embedding) >= match_threshold
    order by d.embedding operator(extensions.<=>) query_embedding
    limit least(greatest(match_count, 2), 2)
  ),
  lexical as (
    select d.id,
           d.metadata->>'source' as source_name,
           coalesce(d.metadata->>'document_id', d.metadata->>'source', d.id::text) as document_key,
           case when d.metadata->>'chunk' ~ '^[0-9]+$'
                then (d.metadata->>'chunk')::int end as chunk_number,
           d.content,
           d.metadata,
           1 - (d.embedding operator(extensions.<=>) query_embedding) as similarity
    from public.documents d
    cross join query_terms q
    where q.tsquery is not null
      and to_tsvector('simple', d.content) @@ q.tsquery
    order by ts_rank_cd(to_tsvector('simple', d.content), q.tsquery) desc
    limit 2
  ),
  seeds as (
    select * from ranked
    union
    select * from lexical
  ),
  expanded as (
    select d.id,
           d.content,
           d.metadata,
           1 - (d.embedding operator(extensions.<=>) query_embedding) as similarity
    from seeds r
    join public.documents d
      on coalesce(d.metadata->>'document_id', d.metadata->>'source', d.id::text) = r.document_key
     and d.metadata->>'chunk' ~ '^[0-9]+$'
     and r.chunk_number is not null
     and (d.metadata->>'chunk')::int between r.chunk_number - 2 and r.chunk_number + 2
    group by d.id, d.content, d.metadata, d.embedding
  )
  select id, content, metadata, similarity
  from expanded
  order by metadata->>'source', metadata->>'document_id', (metadata->>'chunk')::int
  limit 20;
$$;

revoke all on function public.match_documents_hybrid(extensions.vector, text, float, int) from public, anon, authenticated;
grant execute on function public.match_documents_hybrid(extensions.vector, text, float, int) to service_role;
