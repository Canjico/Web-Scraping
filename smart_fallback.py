from __future__ import annotations

import argparse
import json
import os
import sys

try:
    from .source_registry import get_source_by_script, SourceConfig  # type: ignore
    from .smart_crawler import SmartCrawler, persist_report, report_to_dict, utils  # type: ignore
except Exception:  # pragma: no cover
    from source_registry import get_source_by_script, SourceConfig  # type: ignore
    from smart_crawler import SmartCrawler, persist_report, report_to_dict, utils  # type: ignore


def _build_inline_source(name: str, url: str) -> SourceConfig:
    from urllib.parse import urlparse
    host = urlparse(url).netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    base = f"{urlparse(url).scheme}://{urlparse(url).netloc}" if host else url
    return SourceConfig(script=f"{name}.py", name=name or host or "Fonte manual", base_url=base, start_urls=(url,), allowed_domains=(host,) if host else tuple())


def run(script: str = "", fonte: str = "", url: str = "", persist: bool = False) -> dict:
    source = get_source_by_script(script) if script else None
    if source is None and url:
        source = _build_inline_source(fonte or script or "Fonte manual", url)
    if source is None:
        return {"ok": False, "error": f"Fonte nao encontrada no smart_sources.json para script='{script}'", "persisted": 0}

    utils.print_info(f"[SMART] Iniciando fallback inteligente para {source.name} ({', '.join(source.start_urls)})")
    crawler = SmartCrawler(source)
    report = crawler.crawl()
    payload = report_to_dict(report)
    summary = {
        "ok": True,
        "source": source.name,
        "pages_fetched": report.pages_fetched,
        "pages_failed": report.pages_failed,
        "links_seen": report.links_seen,
        "documents_seen": report.documents_seen,
        "candidates": len(report.candidates),
        "persisted": 0,
        "warnings": report.warnings[:20],
    }

    if persist:
        persisted = persist_report(report, use_ia=os.getenv("SMART_USE_IA", "1") == "1")
        summary.update({
            "persisted": persisted.get("persisted", 0),
            "skipped": persisted.get("skipped", 0),
            "ids": persisted.get("ids", []),
            "errors": persisted.get("errors", []),
        })

    # Arquivo de auditoria local para investigar por que uma fonte capturou ou nao capturou algo.
    try:
        log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Log", "smart_crawler")
        os.makedirs(log_dir, exist_ok=True)
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in source.name)[:80] or "fonte"
        with open(os.path.join(log_dir, f"{safe_name}.json"), "w", encoding="utf-8") as fp:
            json.dump({"summary": summary, "report": payload}, fp, ensure_ascii=False, indent=2)
    except Exception as exc:
        summary.setdefault("warnings", []).append(f"Falha ao gravar auditoria smart: {exc}")

    utils.print_info("[SMART-JSON] " + json.dumps(summary, ensure_ascii=False))
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fallback inteligente de descoberta de editais/documentos")
    parser.add_argument("--script", default="", help="Arquivo do scraper legado. Ex: FINEP.py")
    parser.add_argument("--fonte", default="", help="Nome amigavel da fonte")
    parser.add_argument("--url", default="", help="URL manual quando nao existir no registry")
    parser.add_argument("--persist", action="store_true", help="Persiste candidatos confiaveis no banco usando utils.py")
    parser.add_argument("--json", action="store_true", help="Imprime somente JSON final")
    args = parser.parse_args(argv)
    summary = run(script=args.script, fonte=args.fonte, url=args.url, persist=args.persist)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
