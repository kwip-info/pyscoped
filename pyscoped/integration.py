"""Context hooks for existing service functions. Resolvers must authorize membership."""

from functools import wraps
from inspect import isasyncgenfunction, isawaitable, iscoroutinefunction, isgeneratorfunction

from asgiref.sync import async_to_sync
from django.core.exceptions import ImproperlyConfigured

from .context import ScopeContext, scope
from .exceptions import UnsupportedOperation


def validate_context(ctx):
    if not isinstance(ctx, ScopeContext):
        raise ImproperlyConfigured("A PyScoped resolver must return ScopeContext.")
    return ctx


async def _await_result(result):
    return await result


def resolve_sync(resolver, *args, **kwargs):
    result = resolver(*args, **kwargs)
    return async_to_sync(_await_result)(result) if isawaitable(result) else result


def in_scope(resolver):
    """Decorator that resolves authorized context from the function's existing arguments."""

    def decorate(function):
        if isgeneratorfunction(function) or isasyncgenfunction(function):
            raise UnsupportedOperation(
                "Use scope inside generators, or ScopedMiddleware streaming."
            )
        if iscoroutinefunction(function):

            @wraps(function)
            async def asynchronous(*args, **kwargs):
                ctx = resolver(*args, **kwargs)
                if isawaitable(ctx):
                    ctx = await ctx
                async with scope(**validate_context(ctx).__dict__):
                    return await function(*args, **kwargs)

            return asynchronous

        @wraps(function)
        def synchronous(*args, **kwargs):
            ctx = validate_context(resolve_sync(resolver, *args, **kwargs))
            with scope(**ctx.__dict__):
                return function(*args, **kwargs)

        return synchronous

    return decorate
