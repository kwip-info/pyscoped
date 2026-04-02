"""Tests for the stability marker system."""

from __future__ import annotations

import warnings

import pytest

from scoped._stability import (
    ExperimentalAPIWarning,
    PreviewAPIWarning,
    StabilityLevel,
    _WARNED,
    experimental,
    get_stability_level,
    preview,
    stable,
)


@pytest.fixture(autouse=True)
def _clear_warned():
    """Reset the warned set between tests so warnings fire fresh."""
    _WARNED.clear()
    yield
    _WARNED.clear()


# ---------------------------------------------------------------------------
# Decorator behaviour
# ---------------------------------------------------------------------------


class TestExperimentalDecorator:
    def test_class_warns_on_first_instantiation(self):
        @experimental("not stable yet")
        class MyClass:
            pass

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            MyClass()
            assert len(w) == 1
            assert issubclass(w[0].category, ExperimentalAPIWarning)
            assert "experimental" in str(w[0].message).lower()
            assert "not stable yet" in str(w[0].message)

    def test_class_warns_only_once(self):
        @experimental()
        class MyClass:
            pass

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            MyClass()
            MyClass()
            MyClass()
            assert len(w) == 1

    def test_function_warns_on_first_call(self):
        @experimental("WIP")
        def my_func():
            return 42

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = my_func()
            assert result == 42
            assert len(w) == 1
            assert issubclass(w[0].category, ExperimentalAPIWarning)
            assert "WIP" in str(w[0].message)

    def test_function_warns_only_once(self):
        @experimental()
        def my_func():
            return 1

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            my_func()
            my_func()
            assert len(w) == 1

    def test_sets_stability_attribute(self):
        @experimental()
        class MyClass:
            pass

        assert get_stability_level(MyClass) == StabilityLevel.EXPERIMENTAL


class TestPreviewDecorator:
    def test_class_warns_with_preview_category(self):
        @preview("near-final")
        class MyClass:
            pass

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            MyClass()
            assert len(w) == 1
            assert issubclass(w[0].category, PreviewAPIWarning)
            assert "preview" in str(w[0].message).lower()

    def test_sets_stability_attribute(self):
        @preview()
        class MyClass:
            pass

        assert get_stability_level(MyClass) == StabilityLevel.PREVIEW


class TestStableDecorator:
    def test_no_warning_emitted(self):
        @stable(since="0.6.0")
        class MyClass:
            pass

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            MyClass()
            # No stability warnings (there may be other warnings)
            stability_warnings = [
                x for x in w
                if issubclass(x.category, (ExperimentalAPIWarning, PreviewAPIWarning))
            ]
            assert len(stability_warnings) == 0

    def test_sets_stability_attribute(self):
        @stable(since="0.6.0")
        class MyClass:
            pass

        assert get_stability_level(MyClass) == StabilityLevel.STABLE


# ---------------------------------------------------------------------------
# Warning filtering
# ---------------------------------------------------------------------------


class TestWarningFiltering:
    def test_can_suppress_experimental(self):
        @experimental()
        class MyClass:
            pass

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            warnings.filterwarnings("ignore", category=ExperimentalAPIWarning)
            MyClass()
            experimental_warnings = [
                x for x in w if issubclass(x.category, ExperimentalAPIWarning)
            ]
            assert len(experimental_warnings) == 0

    def test_can_suppress_preview(self):
        @preview()
        class MyClass:
            pass

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            warnings.filterwarnings("ignore", category=PreviewAPIWarning)
            MyClass()
            preview_warnings = [
                x for x in w if issubclass(x.category, PreviewAPIWarning)
            ]
            assert len(preview_warnings) == 0


# ---------------------------------------------------------------------------
# Introspection
# ---------------------------------------------------------------------------


class TestIntrospection:
    def test_get_stability_level_returns_none_for_unmarked(self):
        class PlainClass:
            pass

        assert get_stability_level(PlainClass) is None

    def test_get_stability_level_on_function(self):
        @experimental()
        def my_func():
            pass

        assert get_stability_level(my_func) == StabilityLevel.EXPERIMENTAL


# ---------------------------------------------------------------------------
# Real framework classes
# ---------------------------------------------------------------------------


class TestFrameworkClasses:
    def test_environment_lifecycle_is_experimental(self):
        from scoped.environments.lifecycle import EnvironmentLifecycle

        assert get_stability_level(EnvironmentLifecycle) == StabilityLevel.EXPERIMENTAL

    def test_connector_manager_is_preview(self):
        from scoped.connector.bridge import ConnectorManager

        assert get_stability_level(ConnectorManager) == StabilityLevel.PREVIEW

    def test_event_bus_is_experimental(self):
        from scoped.events.bus import EventBus

        assert get_stability_level(EventBus) == StabilityLevel.EXPERIMENTAL
