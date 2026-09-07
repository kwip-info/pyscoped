# Layer 7: Temporal & Rollback

## Purpose

Temporal is the undo button for the entire system. Because every action is traced (Layer 6) and every object is versioned (Layer 3), the framework can reconstruct any state at any point in time and reverse any action.

This isn't "soft undo." This is: **pick any timestamp, and the framework can show you exactly what everything looked like, then optionally restore it.**

## Core Concepts

### Point-in-Time Reconstruction

Given an object ID and a timestamp, the temporal layer:
1. Queries the audit trail for all trace entries targeting this object up to the timestamp
2. Finds the most recent `after_state` before the timestamp
3. Returns the reconstructed state

This works for any entity: objects, scopes, rules, memberships, environments. If it was traced, it can be reconstructed.

### Rollback

A rollback reverses an action or a series of actions. Rollbacks are themselves traced — you can see who rolled back what, and you can roll back a rollback.

Types of rollback:

**Single-action rollback** — reverse a specific traced action.
- "Undo the last edit to this document" → restore the previous object version

**Point-in-time rollback** — restore an entity to its state at a specific timestamp.
- "Restore this scope to how it looked yesterday at 3 PM" → reverse all changes since then

**Cascading rollback** — rolling back one action triggers rollback of dependent actions.
- Rolling back a scope creation rolls back all memberships, projections, and objects that were created within it
- Rolling back a rule change re-evaluates all access decisions that were affected

### Rollback Constraints

Not everything can be rolled back. Rules (Layer 5) can prohibit rollback:
- "Deployments to production cannot be rolled back after 24 hours"
- "Secret rotations cannot be rolled back" (the old secret may be compromised)
- "Audit trail entries cannot be rolled back" (they're immutable by design)

When a rollback is attempted, the temporal layer checks with the rule engine. If a constraint blocks it, `RollbackDeniedError` is raised.

### Rollback Resolution

For cascading rollbacks, the temporal layer builds a dependency graph:
1. Identify the target action to roll back
2. Find all downstream actions that depend on it (via `parent_trace_id` chain in audit)
3. Check rollback constraints for each
4. Execute rollbacks in reverse chronological order
5. Trace the entire rollback as a single nested operation

## How It Connects

### To Layer 3 (Objects)
Object versioning is the foundation. Rolling back an object means setting `current_version` to a previous version. The rolled-back-from version is retained.

### To Layer 5 (Rules)
Rules govern what can be rolled back. Rule changes are themselves rollbackable (restoring a previous rule version).

### To Layer 6 (Audit)
The audit trail is both the *source data* for reconstruction and the *target* for rollback traces. Rollback operations produce their own trace entries, including before/after states.

### To Layer 8 (Environments)
Discarding an environment is a kind of rollback — returning to the state before the environment existed. Environment snapshots (Layer 8) are temporal checkpoints.

### To Layer 9 (Flow)
Stage transitions can be rolled back — moving an object back to a previous stage. Promotions can be reversed — un-projecting an object from a scope.

### To Layer 10 (Deployments)
Deployment rollbacks are a specific case of temporal rollback — reverting a deployment creates a new deployment record that references the original.

## Files

```
scoped/temporal/
    __init__.py
    reconstruction.py  # Rebuild any entity's state at timestamp T
    rollback.py        # Execute rollback with cascade resolution
    constraints.py     # Check if rollback is permitted by rules
```

## Invariants

1. Any traced action can be reconstructed at any point in time.
2. Rollbacks are themselves traced actions (auditable, rollbackable).
3. Cascading rollbacks follow the dependency chain completely.
4. Rollback constraints (rules) are checked before execution.
5. Rolled-back state is retained — nothing is lost, even in a rollback.
