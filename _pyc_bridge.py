import importlib.machinery
import importlib.util
import os
import sys
from types import ModuleType


def _candidate_pyc_paths(module_file: str) -> list[str]:
    base_name = os.path.splitext(os.path.basename(module_file))[0]
    tag = f"cpython-{sys.version_info.major}{sys.version_info.minor}"
    root_dir = os.path.dirname(module_file)
    candidates = [
        os.path.join(root_dir, "_pyc_original", f"{base_name}.{tag}.pyc"),
        os.path.join(root_dir, "__pycache__", f"{base_name}.{tag}.pyc"),
    ]
    # Fallback auditavel: mostra pyc de outras versoes em vez de falhar dizendo que nao existe.
    for folder in (os.path.join(root_dir, "_pyc_original"), os.path.join(root_dir, "__pycache__")):
        try:
            for name in sorted(os.listdir(folder)):
                if name.startswith(base_name + ".") and name.endswith(".pyc"):
                    path = os.path.join(folder, name)
                    if path not in candidates:
                        candidates.append(path)
        except Exception:
            pass
    return candidates


def _pyc_path_for(module_file: str) -> str:
    candidates = _candidate_pyc_paths(module_file)
    for path in candidates:
        if os.path.exists(path) and _pyc_magic_is_compatible(path):
            return path
    existing = [p for p in candidates if os.path.exists(p)]
    if existing:
        raise RuntimeError(
            "Existe .pyc legado, mas ele nao e compativel com este Python. "
            f"Python atual={sys.version_info.major}.{sys.version_info.minor}; arquivos encontrados={existing}"
        )
    raise FileNotFoundError(f"Arquivo .pyc nao encontrado. Procurado em: {candidates}")


def _pyc_magic_is_compatible(path: str) -> bool:
    try:
        with open(path, "rb") as fp:
            return fp.read(4) == importlib.util.MAGIC_NUMBER
    except Exception:
        return False


def _ensure_package_alias(package_name: str, package_dir: str) -> None:
    if not package_name or package_name in sys.modules:
        return
    pkg = ModuleType(package_name)
    pkg.__path__ = [package_dir]  # type: ignore[attr-defined]
    pkg.__package__ = package_name
    pkg.__file__ = os.path.join(package_dir, "__init__.py")
    sys.modules[package_name] = pkg


def load_module_from_pyc(module_file: str, module_name: str, package: str | None) -> ModuleType:
    pyc_path = _pyc_path_for(module_file)
    root_dir = os.path.dirname(module_file)
    package_root = os.path.dirname(root_dir)
    if package_root and package_root not in sys.path:
        sys.path.insert(0, package_root)
    if root_dir and root_dir not in sys.path:
        sys.path.insert(0, root_dir)

    loader = importlib.machinery.SourcelessFileLoader(module_name, pyc_path)
    spec = importlib.util.spec_from_loader(module_name, loader)
    if spec is None:
        raise ImportError(f"Nao foi possivel criar spec para: {pyc_path}")

    module = importlib.util.module_from_spec(spec)

    package_name = package
    if not package_name and module_name == "__main__":
        package_name = os.getenv("SCRAPER_PACKAGE_NAME", "sites")
    if package_name:
        _ensure_package_alias(package_name, root_dir)
        module.__package__ = package_name

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
