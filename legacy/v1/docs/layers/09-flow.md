# Layer 9: Stages & Flow

## Purpose

Flow is the river. It defines **how information moves** between environments, scopes, and stages through governed, directional channels.

If environments (Layer 8) are where work pools before entering the system, and scopes (Layer 4) are the banks that contain the river, then flow is the current itself — the mechanics of movement, the stages of maturation, and the explicit channels through which things travel.

## Core Concepts

### Pipeline

A named sequence of stages that defines how work matures.

| Field | Purpose |
|-------|---------|
| `id` | Globally unique identifier |
| `name` | Human-readable label ("Code Review", "Content Approval", "Release Pipeline") |
| `owner_id` | Principal who defined this pipeline |

Pipelines are registered constructs. Applications define their own pipelines — the framework provides the machinery for executing them.

### Stage

A named state within a pipeline.

| Field | Purpose |
|-------|---------|
| `pipeline_id` | Which pipeline this stage belongs to |
| `name` | Stage name ("draft", "review", "approved", "deployed") |
| `ordinal` | Position in the pipeline (determines natural ordering) |

Stages are ordered but not strictly linear — rules can allow skipping stages or moving backward. The ordinal defines the "normal" progression; rules define the actual permitted transitions.

### Stage Transition

The act of moving an object from one stage to the next.

| Field | Purpose |
|-------|---------|
| `object_id` | What's being transitioned |
| `from_stage_id` | Where it was (null for initial placement) |
| `to_stage_id` | Where it's going |
| `transitioned_by` | Who did it |
| `reason` | Why |

Every transition is a traced action. Transitions are governed by rules — "who can move an object from 'review' to 'approved'?" is a rule evaluation.

### Flow Channel

An explicit, directional pipe between two points in the system.

| Field | Purpose |
|-------|---------|
| `name` | Channel label |
| `source_type` / `source_id` | Where flow originates (environment, scope, or stage) |
| `target_type` / `target_id` | Where flow goes |
| `allowed_types` | JSON array of object types that can travel through this channel |
| `owner_id` | Who controls this channel |

Flow channels are explicit — information doesn't leak from one place to another. It travels through defined channels. Channels can be:
- **Environment → Scope**: promotion path (throwaway work → persistent world)
- **Scope → Scope**: sharing path (one team's scope → another team's scope)
- **Stage → Stage**: maturation path (draft → review → approved)
- **Environment → Environment**: collaboration path (one workspace → another)

### Promotion

The specific act of moving work from an ephemeral environment into the persistent world.

| Field | Purpose |
|-------|---------|
| `object_id` | What's being promoted |
| `source_env_id` | The environment it came from |
| `target_scope_id` | The scope it's being promoted into |
| `target_stage_id` | Optional: initial stage placement in the target pipeline |
| `promoted_by` | Who promoted it |

Promotion is selective — you promote specific objects, not the whole environment. This is deliberate: the environment might contain working notes, intermediate results, and failed attempts. Only the good stuff gets promoted.

### The River in Practice

A typical flow looks like:

```
1. User spawns an environment (throwaway workspace)
2. Work happens — objects are created, modified, reviewed within the env
3. User selects results to promote
4. Promotion flows through a channel: environment → target scope
5. Objects arrive in the target scope at the "draft" stage
6. Pipeline kicks in: draft → review → approved → deployed
7. At each stage, rules govern who can transition
8. Final stage triggers a deployment (Layer 10)
```

Each step is traced, rule-checked, and rollbackable.

## How It Connects

### To Layer 4 (Tenancy)
Flow channels connect scopes. Promotions project objects into scopes. Scopes are the containers through which the river flows.

### To Layer 5 (Rules)
Stage transitions are rule-governed. Flow channels have capacity rules. Promotions require authorization. Rules are the dams and locks that control the current.

### To Layer 6 (Audit)
Every stage transition, flow push, and promotion produces a trace entry.

### To Layer 7 (Temporal)
Stage transitions are rollbackable — you can move an object back to a previous stage. Promotions can be reversed.

### To Layer 8 (Environments)
Environments are where work pools before entering the flow. Promotion is the bridge between the ephemeral world and the persistent world.

### To Layer 10 (Deployments)
Deployments are the terminal stage — the final flow out of the Scoped world. A deployment might be triggered by reaching the last stage in a pipeline.

### To Layer 13 (Connector)
Connector traffic is a type of flow — objects moving between organizations through governed channels.

## Files

```
scoped/flow/
    __init__.py
    models.py          # Stage, StageTransition, Flow, FlowChannel, Promotion
    pipeline.py        # Define and execute stage pipelines
    engine.py          # Flow resolution — can X flow from A to B?
    promotion.py       # Promote objects from environments into scopes
```

## Schema

```sql
CREATE TABLE pipelines (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    owner_id        TEXT NOT NULL REFERENCES principals(id),
    created_at      TEXT NOT NULL,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE'
);

CREATE TABLE stages (
    id              TEXT PRIMARY KEY,
    pipeline_id     TEXT NOT NULL,
    name            TEXT NOT NULL,
    ordinal         INTEGER NOT NULL,
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    UNIQUE(pipeline_id, name)
);

CREATE TABLE stage_transitions (
    id              TEXT PRIMARY KEY,
    object_id       TEXT NOT NULL REFERENCES scoped_objects(id),
    from_stage_id   TEXT REFERENCES stages(id),
    to_stage_id     TEXT NOT NULL REFERENCES stages(id),
    transitioned_at TEXT NOT NULL,
    transitioned_by TEXT NOT NULL,
    reason          TEXT NOT NULL DEFAULT ''
);

CREATE TABLE flow_channels (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    source_type     TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    target_type     TEXT NOT NULL,
    target_id       TEXT NOT NULL,
    allowed_types   TEXT NOT NULL DEFAULT '[]',
    owner_id        TEXT NOT NULL REFERENCES principals(id),
    created_at      TEXT NOT NULL,
    lifecycle       TEXT NOT NULL DEFAULT 'ACTIVE'
);

CREATE TABLE promotions (
    id              TEXT PRIMARY KEY,
    object_id       TEXT NOT NULL REFERENCES scoped_objects(id),
    source_env_id   TEXT NOT NULL REFERENCES environments(id),
    target_scope_id TEXT NOT NULL REFERENCES scopes(id),
    target_stage_id TEXT REFERENCES stages(id),
    promoted_at     TEXT NOT NULL,
    promoted_by     TEXT NOT NULL
);
```

## Invariants

1. Information moves only through explicit flow channels. No implicit leaking.
2. Stage transitions are rule-governed and traced.
3. Promotions are selective — specific objects, not entire environments.
4. Flow channels are directional — source and target are explicit.
5. Every movement through the system is auditable and rollbackable.
