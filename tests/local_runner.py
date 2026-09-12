"""Dependency-free fallback test runner for restricted local environments."""
from __future__ import annotations

import contextlib
import importlib
import inspect
import sys
import types
import uuid
from pathlib import Path


if "pytest" not in sys.modules:
    fake = types.ModuleType("pytest")

    @contextlib.contextmanager
    def raises(expected):
        try:
            yield
        except expected:
            return
        raise AssertionError(f"Expected {expected.__name__}")

    fake.raises = raises
    sys.modules["pytest"] = fake


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    modules = [
        "tests.test_parsers", "tests.test_chunker", "tests.test_metadata",
        "tests.test_model_selector", "tests.test_rate_limiter",
        "tests.test_gemini_client",
        "tests.test_callback",
        "tests.test_checkpoint_profiles_storage", "tests.test_security",
        "tests.test_zero_cost", "tests.test_beginner_setup", "tests.test_progress",
    ]
    passed = 0
    for module_name in modules:
        module = importlib.import_module(module_name)
        for name, func in inspect.getmembers(module, inspect.isfunction):
            if not name.startswith("test_"):
                continue
            kwargs = {}
            if "tmp_path" in inspect.signature(func).parameters:
                path = root / ".test-tmp" / f"{name}-{uuid.uuid4().hex}"
                path.mkdir(parents=True)
                kwargs["tmp_path"] = path
            func(**kwargs)
            passed += 1
            print(f"PASS {module_name}.{name}")
    print(f"passed: {passed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
