from inspect import isawaitable

from asgiref.sync import iscoroutinefunction, markcoroutinefunction
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

from .context import _bind, _current
from .integration import resolve_sync, validate_context


class ScopedMiddleware:
    """Attach context supplied by the application's authenticated membership resolver."""

    sync_capable = True
    async_capable = True

    def __init__(self, get_response):
        self.get_response = get_response
        self.resolver = getattr(settings, "PYSCOPED_CONTEXT_RESOLVER", None)
        if isinstance(self.resolver, str):
            self.resolver = import_string(self.resolver)
        if not callable(self.resolver):
            raise ImproperlyConfigured(
                "Configure PYSCOPED_CONTEXT_RESOLVER with a trusted callable."
            )
        self.is_async = iscoroutinefunction(get_response)
        if self.is_async:
            markcoroutinefunction(self)

    def __call__(self, request):
        if self.is_async:
            return self.__acall__(request)
        # A request must not inherit a prior request's actor, including resolver=None results.
        token = _current.set(None)
        try:
            ctx = resolve_sync(self.resolver, request)
            if ctx is None:
                return self._stream(self.get_response(request), None)
            ctx = validate_context(ctx)
            with _bind(ctx):
                request.pyscoped_context = ctx
                response = self.get_response(request)
            return self._stream(response, ctx)
        finally:
            _current.reset(token)

    async def __acall__(self, request):
        token = _current.set(None)
        try:
            ctx = self.resolver(request)
            if isawaitable(ctx):
                ctx = await ctx
            if ctx is None:
                return self._stream(await self.get_response(request), None)
            ctx = validate_context(ctx)
            with _bind(ctx):
                request.pyscoped_context = ctx
                response = await self.get_response(request)
            return self._stream(response, ctx)
        finally:
            _current.reset(token)

    @staticmethod
    def _stream(response, ctx):
        if not getattr(response, "streaming", False):
            return response
        source = response.streaming_content
        if response.is_async:

            async def chunks():
                iterator = aiter(source)
                try:
                    while True:
                        with _bind(ctx):
                            try:
                                chunk = await anext(iterator)
                            except StopAsyncIteration:
                                break
                        yield chunk
                finally:
                    if hasattr(iterator, "aclose"):
                        with _bind(ctx):
                            await iterator.aclose()

            response.streaming_content = chunks()
        else:

            def chunks():
                iterator = iter(source)
                try:
                    while True:
                        with _bind(ctx):
                            try:
                                chunk = next(iterator)
                            except StopIteration:
                                break
                        yield chunk
                finally:
                    if hasattr(iterator, "close"):
                        with _bind(ctx):
                            iterator.close()

            response.streaming_content = chunks()
        return response
