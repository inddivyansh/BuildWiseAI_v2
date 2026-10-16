-- ArchiGen Supabase Schema
-- Run via Supabase SQL Editor or migration

-- Projects
create table if not exists projects (
    id uuid primary key default gen_random_uuid(),
    user_id uuid references auth.users(id) on delete cascade,
    name text not null,
    description text,
    locale text default 'CA-QC',
    created_at timestamptz default now(),
    updated_at timestamptz default now()
);

-- Floor plan versions (each generation/edit creates a new version)
create table if not exists floor_plan_versions (
    id uuid primary key default gen_random_uuid(),
    project_id uuid references projects(id) on delete cascade,
    generation_id text unique not null,
    version_number int not null default 1,
    
    -- The complete spec and result as JSONB
    spec jsonb not null,           -- FloorPlanSpec
    floor_plan jsonb,              -- GeneratedFloorPlan
    compliance jsonb,              -- ComplianceResult
    
    -- Metadata
    generator_model text,
    generation_time_s float,
    prompt text,                   -- Original NL prompt
    parent_version_id uuid references floor_plan_versions(id),  -- For edit chains
    
    created_at timestamptz default now()
);

-- Generated files (IFC, DXF, SVG exports)
create table if not exists generated_files (
    id uuid primary key default gen_random_uuid(),
    version_id uuid references floor_plan_versions(id) on delete cascade,
    format text not null,          -- 'ifc', 'dxf', 'svg', 'pdf'
    storage_path text not null,    -- Supabase Storage path
    file_size_bytes bigint,
    created_at timestamptz default now()
);

-- Uploaded plans (for ingestion)
create table if not exists uploaded_plans (
    id uuid primary key default gen_random_uuid(),
    project_id uuid references projects(id) on delete cascade,
    file_type text not null,       -- 'image', 'pdf', 'dxf', 'ifc'
    storage_path text not null,
    parsed_spec jsonb,             -- Result of ingestion pipeline
    confidence float,
    created_at timestamptz default now()
);

-- Indexes
create index if not exists idx_projects_user on projects(user_id);
create index if not exists idx_versions_project on floor_plan_versions(project_id);
create index if not exists idx_versions_generation on floor_plan_versions(generation_id);
create index if not exists idx_files_version on generated_files(version_id);

-- RLS policies
alter table projects enable row level security;
alter table floor_plan_versions enable row level security;
alter table generated_files enable row level security;
alter table uploaded_plans enable row level security;

create policy "Users can CRUD own projects"
    on projects for all
    using (auth.uid() = user_id);

create policy "Users can read own versions"
    on floor_plan_versions for select
    using (project_id in (select id from projects where user_id = auth.uid()));

create policy "Users can insert own versions"
    on floor_plan_versions for insert
    with check (project_id in (select id from projects where user_id = auth.uid()));

create policy "Users can read own files"
    on generated_files for select
    using (version_id in (
        select fpv.id from floor_plan_versions fpv
        join projects p on fpv.project_id = p.id
        where p.user_id = auth.uid()
    ));

create policy "Users can CRUD own uploads"
    on uploaded_plans for all
    using (project_id in (select id from projects where user_id = auth.uid()));

-- Storage bucket for generated files
insert into storage.buckets (id, name, public) 
values ('archigen-files', 'archigen-files', false)
on conflict do nothing;
