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


def _env_flag(name: str, default: str = "0") -> bool:
    return str(os.getenv(name, default)).strip().lower() not in {"0", "false", "nao", "não", "no", "off", ""}


def _env_int(name: str, default: int, minimum: int = 1, maximum: int | None = None) -> int:
    try:
        value = int(str(os.getenv(name, str(default))).strip())
    except Exception:
        value = default
    value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _clip(value: object, limit: int = 260) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: max(0, limit - 3)] + "..."


def _verbose_enabled() -> bool:
    return _env_flag("SMART_VERBOSE_LOG", "0")


def _verbose(msg: str) -> None:
    if _verbose_enabled():
        utils.print_info(f"[SMART-DETALHE] {msg}")


def _emit_report_details(report) -> None:
    if not _verbose_enabled():
        return
    max_candidates = _env_int("SMART_VERBOSE_MAX_CANDIDATES", 20, minimum=1, maximum=200)
    max_docs = _env_int("SMART_VERBOSE_MAX_DOCS_PER_CANDIDATE", 8, minimum=1, maximum=100)
    utils.print_info(
        "[SMART-DETALHE] "
        f"fonte={report.source} | paginas_lidas={report.pages_fetched} | "
        f"paginas_falhas={report.pages_failed} | links={report.links_seen} | "
        f"documentos={report.documents_seen} | candidatos={len(report.candidates)}"
    )
    for warning in (report.warnings or [])[:20]:
        utils.print_info(f"[SMART-DETALHE] warning={_clip(warning, 700)}")
    for idx, cand in enumerate(report.candidates[:max_candidates], start=1):
        utils.print_info(
            "[SMART-CANDIDATO] "
            f"#{idx} | conf={cand.confidence:.3f} | score={cand.score:.3f} | "
            f"kind={cand.kind} | status_hint={cand.status_hint} | docs={len(cand.documents)} | "
            f"titulo={_clip(cand.title, 220)}"
        )
        utils.print_info(f"[SMART-CANDIDATO] #{idx} url={cand.url}")
        if cand.reasons:
            utils.print_info(f"[SMART-CANDIDATO] #{idx} motivos={_clip('; '.join(cand.reasons), 700)}")
        for doc_i, doc in enumerate(cand.documents[:max_docs], start=1):
            utils.print_info(
                "[SMART-DOCUMENTO] "
                f"cand=#{idx} doc={doc_i}/{len(cand.documents)} | conf={doc.confidence:.3f} | "
                f"tipo={doc.content_type or '-'} | titulo={_clip(doc.title or doc.text or doc.url, 180)} | url={doc.url}"
            )


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
    _emit_report_details(report)
    payload = report_to_dict(report)
    ok = report.pages_fetched > 0 or len(report.candidates) > 0 or report.documents_seen > 0
    status = "ok" if ok else "fonte_nao_capturada"
    summary = {
        "ok": ok,
        "status": status,
        "degraded": not ok or bool(report.pages_failed and report.pages_fetched == 0),
        "source": source.name,
        "pages_fetched": report.pages_fetched,
        "pages_failed": report.pages_failed,
        "links_seen": report.links_seen,
        "documents_seen": report.documents_seen,
        "candidates": len(report.candidates),
        "persisted": 0,
        "warnings": report.warnings[:20],
    }
    if not ok:
        summary["error"] = "Nenhuma pagina/documento/candidato foi capturado pela fonte. Isso nao deve ser tratado como sucesso silencioso."

    if persist:
        persisted = persist_report(report, use_ia=os.getenv("SMART_USE_IA", "1") == "1")
        summary.update({
            "persisted": persisted.get("persisted", 0),
            "skipped": persisted.get("skipped", 0),
            "ids": persisted.get("ids", []),
            "errors": persisted.get("errors", []),
            "persisted_details": persisted.get("persisted_details", []),
            "skipped_details": persisted.get("skipped_details", []),
        })
        if _verbose_enabled():
            utils.print_info(
                f"[SMART-PERSISTENCIA] persistidos={summary.get('persisted', 0)} | "
                f"pulados={summary.get('skipped', 0)} | erros={len(summary.get('errors') or [])}"
            )
            for item in summary.get("persisted_details", [])[:20]:
                utils.print_info(
                    f"[SMART-PERSISTIDO] id={item.get('id')} | conf={item.get('confidence')} | "
                    f"docs={item.get('documents')} | titulo={_clip(item.get('title'), 220)} | url={item.get('url')}"
                )
            for item in summary.get("skipped_details", [])[:20]:
                utils.print_info(
                    f"[SMART-PULADO] motivo={item.get('reason')} | conf={item.get('confidence')} | "
                    f"status={item.get('status_hint')} | titulo={_clip(item.get('title'), 220)} | url={item.get('url')}"
                )

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
