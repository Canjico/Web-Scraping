try:
    from ._pyc_bridge import load_module_from_pyc, export_module_namespace
except Exception:
    from _pyc_bridge import load_module_from_pyc, export_module_namespace

_loaded_module = load_module_from_pyc(__file__, __name__, __package__)
export_module_namespace(globals(), _loaded_module)