from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import sys
from types import ModuleType


class LegacyPycCompatibilityError(RuntimeError):
    """Erro explícito quando existe .pyc legado, mas incompatível com o Python atual."""


class LegacyPycNotFoundError(FileNotFoundError):
    """Erro explícito quando o wrapper legado não encontra bytecode para carregar."""


def _candidate_pyc_paths(module_file: str) -> list[str]:
    base_name = os.path.splitext(os.path.basename(module_file))[0]
    tag = f"cpython-{sys.version_info.major}{sys.version_info.minor}"
    root_dir = os.path.dirname(module_file)
    candidates = [
        os.path.join(root_dir, "_pyc_original", f"{base_name}.{tag}.pyc"),
        os.path.join(root_dir, "__pycache__", f"{base_name}.{tag}.pyc"),
    ]
    # Fallback auditável: lista bytecodes de outras versões para logar a causa real.
    for folder in (os.path.join(root_dir, "_pyc_original"), os.path.join(root_dir, "__pycache__")):
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        for name in names:
            if name.startswith(base_name + ".") and name.endswith(".pyc"):
                path = os.path.join(folder, name)
                if path not in candidates:
                    candidates.append(path)
    return candidates


def _pyc_magic_is_compatible(path: str) -> bool:
    try:
        with open(path, "rb") as fp:
            return fp.read(4) == importlib.util.MAGIC_NUMBER
    except OSError:
        return False


def pyc_compatibility_report(module_file: str) -> dict:
    candidates = _candidate_pyc_paths(module_file)
    existing = [p for p in candidates if os.path.exists(p)]
    compatible = [p for p in existing if _pyc_magic_is_compatible(p)]
    return {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "magic_number": importlib.util.MAGIC_NUMBER.hex(),
        "candidates": candidates,
        "existing": existing,
        "compatible": compatible,
        "ok": bool(compatible),
    }


def _pyc_path_for(module_file: str) -> str:
    report = pyc_compatibility_report(module_file)
    compatible = report["compatible"]
    if compatible:
        return compatible[0]
    existing = report["existing"]
    if existing:
        raise LegacyPycCompatibilityError(
            "Existe .pyc legado, mas ele nao e compativel com este Python. "
            f"relatorio={json.dumps(report, ensure_ascii=False)}"
        )
    raise LegacyPycNotFoundError(
        "Arquivo .pyc nao encontrado. "
        f"relatorio={json.dumps(report, ensure_ascii=False)}"
    )


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


def _env_flag(name: str, default: str = "1") -> bool:
    return str(os.getenv(name, default)).strip().lower() not in {"0", "false", "nao", "não", "no", "off"}


def run_smart_fallback_for_wrapper(module_file: str, original_error: BaseException) -> int:
    """Usado quando alguém executa `FONTE.py` direto e o .pyc legado é incompatível.

    O `Edital.py` normalmente já pula o legado incompatível antes de chamar o subprocesso.
    Esta função protege o uso manual dos wrappers para não quebrar seco.
    """
    script = os.path.basename(module_file)
    fonte = os.path.splitext(script)[0]
    persist = _env_flag("SMART_WRAPPER_PERSIST", os.getenv("SMART_PERSIST_ON_WRAPPER", "1"))
    print(
        f"[LEGADO] {script} nao carregou bytecode legado: {original_error}",
        file=sys.stderr,
    )
    print("[SMART-WRAPPER-FALLBACK] acionando fallback inteligente", file=sys.stderr)
    try:
        try:
            from .smart_fallback import run  # type: ignore
        except Exception:
            from smart_fallback import run  # type: ignore
        summary = run(script=script, fonte=fonte, persist=persist)
        return 0 if summary.get("ok") else 2
    except Exception as fallback_error:
        print(f"[SMART-WRAPPER-FALLBACK-ERRO] {fallback_error}", file=sys.stderr)
        return 2
