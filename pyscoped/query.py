"""Explicit manager integration. Private/raw ORM APIs are not an enforcement boundary."""

from copy import copy

from django.db import models, transaction
from django.db.models.sql import Query

from .context import current_context
from .exceptions import ScopeViolation, UnsupportedOperation
from .registry import registered_models, registration


class _ScopedJoin:
    def as_sql(self, compiler, connection):
        if current_context() is not self._pyscoped_context:
            raise ScopeViolation("A joined query cannot escape its scope context.")
        sql, params = super().as_sql(compiler, connection)
        field = self._pyscoped_field
        column = (
            f"{compiler.quote_name_unless_alias(self.table_alias)}."
            f"{connection.ops.quote_name(field.column)}"
        )
        value = field.get_db_prep_value(self._pyscoped_context.scope, connection)
        # Put scope in ON, not WHERE: an outer join must retain a parent with no
        # visible related rows. Root-table scope remains in the queryset WHERE.
        return f"{sql} AND {column} = %s", [*params, value]


class _CheckedCompiler:
    def as_sql(self, *args, **kwargs):
        self.query.check_context()
        return super().as_sql(*args, **kwargs)

    def get_from_clause(self):
        ctx = self.query._scope_context
        if ctx is None:
            return super().get_from_clause()
        protected = {
            config.model._meta.db_table: config
            for config in registered_models()
            if config.mode == "enforce"
        }
        originals = {}
        try:
            # Called after Django has built all joins, including select_related.
            for alias, join in self.query.alias_map.items():
                config = protected.get(join.table_name)
                if config is not None and getattr(join, "parent_alias", None):
                    wrapped = copy(join)
                    wrapped.__class__ = type("ScopedJoin", (_ScopedJoin, type(join)), {})
                    wrapped._pyscoped_context = ctx
                    wrapped._pyscoped_field = config.model._meta.get_field(config.scope_field)
                    originals[alias] = join
                    self.query.alias_map[alias] = wrapped
            return super().get_from_clause()
        finally:
            self.query.alias_map.update(originals)


class _ScopedQuery(Query):
    _scope_context = None

    def check_context(self):
        if self._scope_context is not None and current_context() is not self._scope_context:
            raise ScopeViolation("A query/subquery cannot escape its originating scope context.")

    def get_compiler(self, *args, **kwargs):
        self.check_context()
        compiler = super().get_compiler(*args, **kwargs)
        compiler.__class__ = type("ScopedCompiler", (_CheckedCompiler, type(compiler)), {})
        return compiler


class ScopedQuerySet(models.QuerySet):
    _scope_context = None

    def __init__(self, model=None, query=None, using=None, hints=None):
        super().__init__(model, query or _ScopedQuery(model), using, hints)

    def _clone(self):
        clone = super()._clone()
        clone._scope_context = self._scope_context
        return clone

    def _check_context(self):
        config = registration(self.model)
        if config.mode == "enforce" and current_context() is not self._scope_context:
            raise ScopeViolation("Evaluate a scoped queryset within the context that created it.")

    def _fetch_all(self):
        self._check_context()
        return super()._fetch_all()

    def iterator(self, *args, **kwargs):
        self._check_context()
        for item in super().iterator(*args, **kwargs):
            self._check_context()
            yield item

    async def aiterator(self, *args, **kwargs):
        self._check_context()
        async for item in super().aiterator(*args, **kwargs):
            self._check_context()
            yield item

    def count(self):
        self._check_context()
        return super().count()

    def exists(self):
        self._check_context()
        return super().exists()

    def aggregate(self, *args, **kwargs):
        self._check_context()
        return super().aggregate(*args, **kwargs)

    def create(self, **kwargs):
        self._check_context()
        return super().create(**kwargs)

    def _check_other(self, other):
        self._check_context()
        if not isinstance(other, ScopedQuerySet):
            raise UnsupportedOperation("Combine only scoped querysets.")
        other._check_context()

    def __or__(self, other):
        self._check_other(other)
        return super().__or__(other)

    def __and__(self, other):
        self._check_other(other)
        return super().__and__(other)

    def __xor__(self, other):
        self._check_other(other)
        return super().__xor__(other)

    def union(self, *others, **kwargs):
        for other in others:
            self._check_other(other)
        return super().union(*others, **kwargs)

    def intersection(self, *others):
        for other in others:
            self._check_other(other)
        return super().intersection(*others)

    def difference(self, *others):
        for other in others:
            self._check_other(other)
        return super().difference(*others)

    def extra(self, *args, **kwargs):
        raise UnsupportedOperation("extra() embeds unscoped SQL; use normal scoped queries.")

    def _mutation_config(self, fields):
        self._check_context()
        self._for_write = True
        current_context()  # audit mode also requires attribution for empty bulk writes
        config = registration(self.model)
        if any(
            name in {config.scope_field, config.scope_attname, "pk", self.model._meta.pk.name}
            for name in fields
        ):
            raise UnsupportedOperation("Queryset mutations cannot change primary keys or scope.")
        if self.query.is_sliced or self.query.combinator or self._fields is not None:
            raise UnsupportedOperation("Mutation requires an unsliced, uncombined model queryset.")
        return config

    def _record_bulk(self, config, before, keys, action):
        from .audit import append_event, check_relations, check_scope, load_row, snapshot

        ctx = current_context()
        for pk in keys:
            row = load_row(self.model, pk, self.db)
            if row is None:
                raise UnsupportedOperation(
                    "A bulk mutation did not leave the expected persisted row."
                )
            check_scope(config, row, ctx)
            check_relations(config, row, ctx, self.db)
            append_event(
                config,
                row,
                using=self.db,
                action=action,
                before=before.get(pk),
                after=snapshot(config, row),
                ctx=ctx,
            )

    def update(self, **kwargs):
        """Native SQL update plus transactional snapshots; retains Django signal semantics."""
        from .audit import snapshot

        config = self._mutation_config(kwargs)
        if not kwargs:
            return 0
        with transaction.atomic(using=self.db):
            rows = self.select_for_update(of=("self",)).order_by("pk")
            before = {row.pk: snapshot(config, row) for row in rows}
            count = (
                models.QuerySet(model=self.model, using=self.db)
                .filter(pk__in=before)
                .update(**kwargs)
            )
            self._record_bulk(config, before, before, "update")
        self._result_cache = None
        return count

    def delete(self):
        self._check_context()
        self._for_write = True
        current_context()
        with transaction.atomic(using=self.db):
            return super().delete()

    def bulk_create(
        self,
        objs,
        batch_size=None,
        ignore_conflicts=False,
        update_conflicts=False,
        update_fields=None,
        unique_fields=None,
    ):
        from .audit import check_relations, check_scope

        config = self._mutation_config(())
        if ignore_conflicts or update_conflicts or update_fields or unique_fields:
            raise UnsupportedOperation(
                "Conflict-handling bulk inserts need an explicit app workflow."
            )
        objs = list(objs)
        ctx = current_context()
        with transaction.atomic(using=self.db):
            for obj in objs:
                check_scope(config, obj, ctx)
                check_relations(config, obj, ctx, self.db)
            result = models.QuerySet(model=self.model, using=self.db).bulk_create(
                objs, batch_size=batch_size
            )
            if any(obj.pk is None for obj in result):
                raise UnsupportedOperation("This backend cannot return bulk-inserted identities.")
            self._record_bulk(config, {}, [obj.pk for obj in result], "create")
        self._result_cache = None
        return result

    def bulk_update(self, objs, fields, batch_size=None):
        from .audit import check_scope, snapshot

        config = self._mutation_config(fields)
        objs = list(objs)
        if not fields:
            raise ValueError("Field names must be supplied to bulk_update().")
        keys = [obj.pk for obj in objs]
        if any(pk is None for pk in keys):
            raise ValueError("Every bulk_update object must have a primary key.")
        if len(set(keys)) != len(keys):
            raise UnsupportedOperation(
                "Duplicate bulk_update identities are ambiguous; deduplicate first."
            )
        ctx = current_context()
        with transaction.atomic(using=self.db):
            # Reject foreign persisted identities instead of silently masking an
            # accidental cross-tenant batch. The caller's additional filters apply.
            rows = (
                models.QuerySet(model=self.model, using=self.db)
                .filter(pk__in=keys)
                .order_by("pk")
                .select_for_update()
            )
            for row in rows:
                check_scope(config, row, ctx)
            selected = self.filter(pk__in=keys).order_by("pk")
            before = {row.pk: snapshot(config, row) for row in selected}
            count = (
                models.QuerySet(model=self.model, using=self.db)
                .filter(pk__in=before)
                .bulk_update(objs, fields, batch_size=batch_size)
            )
            self._record_bulk(config, before, before, "update")
        self._result_cache = None
        return count

    def raw(self, *args, **kwargs):
        raise UnsupportedOperation("Raw queries bypass scoping. Use the scoped queryset API.")


class ScopedManager(models.Manager.from_queryset(ScopedQuerySet)):
    def get_queryset(self):
        config = registration(self.model)
        qs = super().get_queryset()
        if not isinstance(qs, ScopedQuerySet):
            raise UnsupportedOperation("Custom querysets must inherit ScopedQuerySet.")
        if config.mode == "enforce":
            ctx = current_context()
            qs._scope_context = ctx
            qs.query._scope_context = ctx
            qs = qs.filter(**{config.scope_attname: ctx.scope})
        return qs
