"""Checklist de sanidade do pacote de scraping.

Uso:
  python health_check.py
  python health_check.py --json

Ele não acessa banco, não chama IA e não faz scraping. É só raio-x local para evitar
quebras bestas antes de subir o robô.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
IGNORE_FILES = {
    "__init__.py",
    "IA.py",
    "utils.py",
    "Edital.py",
    "_pyc_bridge.py",
    "web_check.py",
    "source_registry.py",
    "smart_crawler.py",
    "smart_fallback.py",
    "run_tests.py",
    "health_check.py",
}


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _load_sources() -> dict:
    path = PROJECT_DIR / "smart_sources.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _script_files() -> list[str]:
    return sorted(
        p.name for p in PROJECT_DIR.glob("*.py")
        if p.name not in IGNORE_FILES and not p.name.startswith("_")
    )


def run_checks() -> dict:
    warnings: list[str] = []
    errors: list[str] = []
    scripts = _script_files()
    sources = _load_sources()

    if "run_tests.py" in scripts:
        errors.append("run_tests.py seria executado como scraper. Ele deve ficar no IGNORAR do Edital.py.")
    if "health_check.py" in scripts:
        errors.append("health_check.py seria executado como scraper. Ele deve ficar no IGNORAR do Edital.py.")

    wrappers: list[str] = []
    wrappers_without_smart_fallback: list[str] = []
    for script in scripts:
        text = _read(PROJECT_DIR / script)
        if "load_module_from_pyc" in text:
            wrappers.append(script)
            if "run_smart_fallback_for_wrapper" not in text:
                wrappers_without_smart_fallback.append(script)

    if wrappers_without_smart_fallback:
        errors.append("Wrappers sem fallback inteligente: " + ", ".join(wrappers_without_smart_fallback))

    sources_missing = [script for script in scripts if script not in sources]
    if sources_missing:
        warnings.append("Scripts sem entrada no smart_sources.json: " + ", ".join(sources_missing))

    pyc_report = {}
    try:
        import _pyc_bridge
        for script in wrappers:
            report = _pyc_bridge.pyc_compatibility_report(str(PROJECT_DIR / script))
            pyc_report[script] = {
                "ok": report.get("ok"),
                "python": report.get("python"),
                "existing_count": len(report.get("existing") or []),
                "compatible_count": len(report.get("compatible") or []),
            }
    except Exception as exc:
        warnings.append(f"Nao foi possivel checar compatibilidade .pyc: {exc}")

    incompatible = [name for name, report in pyc_report.items() if not report.get("ok")]
    if incompatible:
        warnings.append(
            "Wrappers com .pyc ausente/incompativel neste Python; o fallback inteligente cobrirá a execução: "
            + ", ".join(incompatible)
        )

    env_example = _read(PROJECT_DIR / ".env.example")
    required_envs = [
        "SQL_CONNECTION_STRING",
        "FRONTEND_APP_URL",
        "AZURE_OPENAI_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_DEPLOYMENT",
        "AZURE_OPENAI_API_VERSION",
        "AZURE_OPENAI_WEBCHECK_ENDPOINT",
        "AZURE_OPENAI_WEBCHECK_DEPLOYMENT",
        "AZURE_OPENAI_WEBCHECK_API_VERSION",
        "AZURE_OPENAI_WEBSEARCH_ENABLED",
        "ENABLE_WEB_CHECK",
        "ENABLE_SMART_FALLBACK",
        "SMART_FALLBACK_ON_SUCCESS",
        "SCRAPER_SUBPROCESS_TIMEOUT_SECONDS",
        "SMART_FALLBACK_TIMEOUT_SECONDS",
        "SKIP_INCOMPATIBLE_LEGACY_PYC",
    ]
    missing_envs = [key for key in required_envs if key not in env_example]
    if missing_envs:
        warnings.append(".env.example sem variaveis recomendadas: " + ", ".join(missing_envs))

    return {
        "ok": not errors,
        "python": sys.version.split()[0],
        "project_dir": str(PROJECT_DIR),
        "scripts_detectados": len(scripts),
        "wrappers_legados": len(wrappers),
        "smart_sources": len(sources),
        "errors": errors,
        "warnings": warnings,
        "pyc_report": pyc_report,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sanidade local do scraping")
    parser.add_argument("--json", action="store_true", help="Imprime apenas JSON")
    args = parser.parse_args(argv)
    result = run_checks()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("HEALTH CHECK DO SCRAPING")
        print("=" * 60)
        print(f"status: {'OK' if result['ok'] else 'ERRO'}")
        print(f"python: {result['python']}")
        print(f"scripts_detectados: {result['scripts_detectados']}")
        print(f"wrappers_legados: {result['wrappers_legados']}")
        print(f"smart_sources: {result['smart_sources']}")
        for item in result["errors"]:
            print(f"[ERRO] {item}")
        for item in result["warnings"]:
            print(f"[AVISO] {item}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
