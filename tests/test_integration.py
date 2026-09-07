import asyncio

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse, StreamingHttpResponse
from django.test import RequestFactory, override_settings

from pyscoped import ScopeContext, current_context, scope
from pyscoped.exceptions import MissingContext
from pyscoped.integration import in_scope
from pyscoped.middleware import ScopedMiddleware
from tests.testapp.models import Invoice

pytestmark = pytest.mark.django_db


def test_service_decorator_uses_existing_arguments():
    @in_scope(lambda user, tenant, amount: ScopeContext(user, tenant))
    def issue(user, tenant, amount):
        return Invoice.objects.create(tenant=tenant, amount=amount)

    assert issue("a", "a", 10).amount == 10
    assert current_context(required=False) is None


async def test_async_service_decorator():
    async def resolver(key):
        return ScopeContext(key, key)

    @in_scope(resolver)
    async def service(key):
        await asyncio.sleep(0)
        return current_context().actor

    assert await service("a") == "a"
    assert current_context(required=False) is None


def test_middleware_requires_trusted_resolver():
    with pytest.raises(ImproperlyConfigured):
        ScopedMiddleware(lambda request: HttpResponse())


def test_middleware_context_and_exception_cleanup():
    def view(request):
        assert current_context() is request.pyscoped_context
        Invoice.objects.create(tenant="a")
        raise RuntimeError("view failed")

    with override_settings(PYSCOPED_CONTEXT_RESOLVER=lambda r: ScopeContext("a", "a")):
        middleware = ScopedMiddleware(view)
        with pytest.raises(RuntimeError):
            middleware(RequestFactory().get("/"))
    assert current_context(required=False) is None


def test_unresolved_request_does_not_inherit_context_or_trust_headers():
    def view(request):
        with pytest.raises(MissingContext):
            Invoice.objects.count()
        return HttpResponse("ok")

    with scope(actor="outer", scope="a"):
        outer = current_context()
        with override_settings(PYSCOPED_CONTEXT_RESOLVER=lambda r: None):
            response = ScopedMiddleware(view)(RequestFactory().get("/", HTTP_X_SCOPED_ACTOR="a"))
        assert current_context() is outer
    assert response.status_code == 200


def test_stream_reenters_exact_query_context_and_does_not_leak():
    with scope(actor="a", scope="a"):
        Invoice.objects.create(tenant="a")

    def view(request):
        qs = Invoice.objects.all()

        def content():
            yield str(qs.count())
            assert current_context().actor == "a"
            yield "done"

        return StreamingHttpResponse(content())

    with override_settings(PYSCOPED_CONTEXT_RESOLVER=lambda r: ScopeContext("a", "a")):
        response = ScopedMiddleware(view)(RequestFactory().get("/"))
    iterator = iter(response.streaming_content)
    assert next(iterator) == b"1"
    assert current_context(required=False) is None
    assert next(iterator) == b"done"
    assert current_context(required=False) is None


async def test_async_middleware_and_stream():
    async def resolver(request):
        return ScopeContext("a", "a")

    async def view(request):
        async def chunks():
            await asyncio.sleep(0)
            yield current_context().actor

        return StreamingHttpResponse(chunks())

    with override_settings(PYSCOPED_CONTEXT_RESOLVER=resolver):
        middleware = ScopedMiddleware(view)
        response = await middleware(RequestFactory().get("/"))
    assert current_context(required=False) is None
    assert [chunk async for chunk in response.streaming_content] == [b"a"]
    assert current_context(required=False) is None


def test_service_decorators_reject_lazy_generators():
    from pyscoped.exceptions import UnsupportedOperation

    def generate():
        yield "work"

    with pytest.raises(UnsupportedOperation):
        in_scope(lambda: ScopeContext("a", "a"))(generate)
    with pytest.raises(UnsupportedOperation):
        scope(actor="a", scope="a")(generate)
