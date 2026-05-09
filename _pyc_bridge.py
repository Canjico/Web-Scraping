import importlib.machinery
import importlib.util
import os
import sys
from types import ModuleType


def _pyc_path_for(module_file: str) -> str:
    base_name = os.path.splitext(os.path.basename(module_file))[0]
    tag = f"cpython-{sys.version_info.major}{sys.version_info.minor}"
    root_dir = os.path.dirname(module_file)
    preferred = os.path.join(root_dir, "_pyc_original", f"{base_name}.{tag}.pyc")
    if os.path.exists(preferred):
        return preferred
    return os.path.join(root_dir, "__pycache__", f"{base_name}.{tag}.pyc")


def load_module_from_pyc(module_file: str, module_name: str, package: str | None) -> ModuleType:
    pyc_path = _pyc_path_for(module_file)
    if not os.path.exists(pyc_path):
        raise FileNotFoundError(f"Arquivo .pyc nao encontrado: {pyc_path}")

    loader = importlib.machinery.SourcelessFileLoader(module_name, pyc_path)
    spec = importlib.util.spec_from_loader(module_name, loader)
    if spec is None:
        raise ImportError(f"Nao foi possivel criar spec para: {pyc_path}")

    module = importlib.util.module_from_spec(spec)

    if package:
        module.__package__ = package
    elif module_name == "__main__":
        package_root = os.path.dirname(os.path.dirname(module_file))
        if package_root and package_root not in sys.path:
            sys.path.insert(0, package_root)
        module.__package__ = "sites"

    loader.exec_module(module)
    return module


def export_module_namespace(target_globals: dict, module: ModuleType) -> None:
    protected = {
        "__name__",
        "__file__",
        "__package__",
        "__loader__",
        "__spec__",
        "__builtins__",
        "__cached__",
        "__doc__",
    }

    for key, value in module.__dict__.items():
        if key in protected:
            continue
        target_globals[key] = value
