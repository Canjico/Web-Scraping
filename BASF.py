try:
    from ._pyc_bridge import (
        export_module_namespace,
        load_module_from_pyc,
        run_smart_fallback_for_wrapper,
    )
except Exception:
    from _pyc_bridge import (
        export_module_namespace,
        load_module_from_pyc,
        run_smart_fallback_for_wrapper,
    )

try:
    _loaded_module = load_module_from_pyc(__file__, __name__, __package__)
    export_module_namespace(globals(), _loaded_module)
except Exception as _legacy_error:
    if __name__ == "__main__":
        raise SystemExit(run_smart_fallback_for_wrapper(__file__, _legacy_error))
    raise
