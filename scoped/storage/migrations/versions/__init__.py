"""Migration version files.

Each migration is a module with:
- version: int — unique, monotonically increasing
- name: str — human-readable description
- up(backend) — apply the migration
- down(backend) — reverse the migration
"""
