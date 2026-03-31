"""Tests for Django ScopedContextMiddleware."""

from __future__ import annotations

import pytest

django = pytest.importorskip("django")

from django.http import JsonResponse
from django.test import RequestFactory

from scoped.contrib.django import get_backend, reset_backend
from scoped.contrib.django.middleware import ScopedContextMiddleware
from scoped.identity.context import ScopedContext
from scoped.identity.principal import PrincipalStore


@pytest.fixture(autouse=True)
def _reset():
    reset_backend()
    yield
    reset_backend()


@pytest.fixture
def backend():
    b = get_backend()
    b.initialize()
    return b


@pytest.fixture
def user(backend):
    store = PrincipalStore(backend)
    return store.create_principal(kind="user", display_name="Test User")


@pytest.fixture
def factory():
    return RequestFactory()


def _view(request):
    ctx = ScopedContext.current_or_none()
    if ctx:
        return JsonResponse({"principal_id": ctx.principal_id})
    return JsonResponse({"principal_id": None})


class TestScopedContextMiddleware:
    def test_sets_context_from_header(self, factory, user, backend):
        middleware = ScopedContextMiddleware(_view)
        request = factory.get("/", HTTP_X_SCOPED_PRINCIPAL_ID=user.id)

        response = middleware(request)

        import json
        data = json.loads(response.content)
        assert data["principal_id"] == user.id

    def test_no_header_no_context(self, factory, backend):
        middleware = ScopedContextMiddleware(_view)
        request = factory.get("/")

        response = middleware(request)

        import json
        data = json.loads(response.content)
        assert data["principal_id"] is None

    def test_exempt_path_skips(self, factory, user, backend):
        middleware = ScopedContextMiddleware(_view)
        request = factory.get("/exempt/foo", HTTP_X_SCOPED_PRINCIPAL_ID=user.id)

        response = middleware(request)

        import json
        data = json.loads(response.content)
        assert data["principal_id"] is None

    def test_context_cleaned_up_after_request(self, factory, user, backend):
        middleware = ScopedContextMiddleware(_view)
        request = factory.get("/", HTTP_X_SCOPED_PRINCIPAL_ID=user.id)

        middleware(request)

        # Context should be cleared after request
        assert ScopedContext.current_or_none() is None

    def test_custom_resolver(self, factory, user, backend):
        def resolver(request):
            return user

        from django.conf import settings
        original = getattr(settings, "SCOPED_PRINCIPAL_RESOLVER", None)
        settings.SCOPED_PRINCIPAL_RESOLVER = resolver

        try:
            middleware = ScopedContextMiddleware(_view)
            middleware._resolved = False  # reset cache
            middleware._resolver = None
            request = factory.get("/")

            response = middleware(request)

            import json
            data = json.loads(response.content)
            assert data["principal_id"] == user.id
        finally:
            if original is None:
                delattr(settings, "SCOPED_PRINCIPAL_RESOLVER")
            else:
                settings.SCOPED_PRINCIPAL_RESOLVER = original

    def test_unknown_principal_returns_no_context(self, factory, backend):
        middleware = ScopedContextMiddleware(_view)
        request = factory.get("/", HTTP_X_SCOPED_PRINCIPAL_ID="nonexistent-id")

        response = middleware(request)

        import json
        data = json.loads(response.content)
        assert data["principal_id"] is None

    def test_attaches_context_to_request(self, factory, user, backend):
        captured = {}

        def capture_view(request):
            captured["ctx"] = getattr(request, "scoped_context", None)
            return JsonResponse({})

        middleware = ScopedContextMiddleware(capture_view)
        request = factory.get("/", HTTP_X_SCOPED_PRINCIPAL_ID=user.id)

        middleware(request)

        assert captured["ctx"] is not None
        assert captured["ctx"].principal_id == user.id
