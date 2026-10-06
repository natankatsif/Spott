"""Every module of spott imports, and the annotations of what it defines resolve. Python 3.12 and 3.13 evaluate
annotations when a function is defined, 3.14 only when they are read: a method named like a module that a later
annotation in its class uses (PgJobStore.schedule did) breaks the import on 3.12, and on 3.14 only a read of that
annotation. CI runs 3.12, the oldest requires-python allows, and 3.14; this test fails on both."""

import importlib
import inspect
import pkgutil

import spott


def defined_in(module):
    """The functions and classes the module defines, and the methods of those classes."""
    for obj in vars(module).values():
        if (inspect.isfunction(obj) or inspect.isclass(obj)) and obj.__module__ == module.__name__:
            yield obj
            if inspect.isclass(obj):
                yield from (v for v in vars(obj).values() if inspect.isfunction(v))


def test_every_module_imports_and_its_annotations_resolve():
    broken = []
    # A package that fails to import is reported by the loop; walk_packages would raise or skip it.
    for info in pkgutil.walk_packages(spott.__path__, "spott.", onerror=lambda name: None):
        try:
            module = importlib.import_module(info.name)
        except Exception as e:  # every broken module is listed, not only the first
            broken.append(f"{info.name}: {e!r}")
            continue
        for obj in defined_in(module):
            try:
                inspect.get_annotations(obj)
            except Exception as e:
                broken.append(f"{info.name}.{obj.__qualname__}: {e!r}")
    assert not broken, "\n".join(broken)
