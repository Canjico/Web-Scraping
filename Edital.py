import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import time
import threading
from types import SimpleNamespace

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

try:
    from . import utils  # type: ignore
except Exception:  # pragma: no cover
    import utils  # type: ignore

# Edital.py
# Agendador + executor de scrapers de editais
#
# Uso (recomendado):
#   python Edital.py                         -> entra no agendador (padrão 00, 06, 18)
#   python Edital.py --run-now               -> roda agora e depois agenda
#   python Edital.py --once                  -> roda uma vez e encerra (teste/CI)
#   python Edital.py --horarios 0,6,18
#
# Observação: pode funcionar também como script direto:
#   python Edital.py --once

PROJETO_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(PROJETO_DIR)
LOG_DIR = os.path.join(ROOT_DIR, "Log")

IGNORAR = {
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

SCRIPTS = sorted(
    f
    for f in os.listdir(PROJETO_DIR)
    if f.endswith(".py") and f not in IGNORAR and not f.startswith("_")
)
TOTAL_SCRIPTS = len(SCRIPTS)
try:
    _FONTES_TOTAL_CONFIG = int(os.getenv("FONTES_TOTAL_DIARIAS", os.getenv("SCRAPER_FONTES_TOTAL", str(TOTAL_SCRIPTS))))
except Exception:
    _FONTES_TOTAL_CONFIG = TOTAL_SCRIPTS
FONTES_TOTAL_DIARIAS = max(TOTAL_SCRIPTS, _FONTES_TOTAL_CONFIG)

TZ_NAME = os.getenv("SCRAPER_TZ", "America/Sao_Paulo")
_TZ = None
if ZoneInfo:
    try:
        _TZ = ZoneInfo(TZ_NAME)
    except Exception:
        _TZ = None


def _now() -> datetime.datetime:
    return datetime.datetime.now(_TZ) if _TZ else datetime.datetime.now()


def _script_fonte_label(arquivo: str) -> str:
    base = os.path.splitext(arquivo or "")[0]
    return re.sub(r"\s+", " ", base.replace("_", " ")).strip() or "Fonte desconhecida"


def _build_exec_command(arquivo: str) -> list[str]:
    """Monta comando sem assumir que a pasta do projeto se chama `sites`.

    No servidor o pacote pode estar instalado como `sites`, mas no ZIP/local a pasta pode se chamar
    `Web-Scraping-main`. Assumir `python -m sites.FONTE` fazia o legado quebrar antes mesmo do
    fallback entrar. O modo direto preserva os stubs atuais e funciona em qualquer nome de pasta.
    """
    prefer_module = os.getenv("SCRAPER_EXEC_AS_MODULE", "0") == "1"
    module_name = arquivo[:-3]
    package_name = os.getenv("SCRAPER_PACKAGE_NAME", os.path.basename(PROJETO_DIR))
    if prefer_module and re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", module_name):
        return [sys.executable, "-m", f"{package_name}.{module_name}"]
    return [sys.executable, os.path.join(PROJETO_DIR, arquivo)]


def _extract_error_reason(stdout: str, stderr: str, returncode: int) -> str:
    if returncode != 0:
        detalhe = ""
        for chunk in (stderr or "", stdout or ""):
            for line in reversed(chunk.splitlines()):
                clean = (line or "").strip()
                if clean:
                    detalhe = clean
                    break
            if detalhe:
                break
        if detalhe:
            return f"Processo encerrou com codigo {returncode} | detalhe: {detalhe[:220]}"
        return f"Processo encerrou com codigo {returncode}"
    return ""


def _env_flag(name: str, default: str = "1") -> bool:
    return str(os.getenv(name, default)).strip().lower() not in {"0", "false", "nao", "não", "no", "off"}






def _safe_json_loads(value):
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _first_nonempty(*values):
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            clean = value.strip()
            if clean:
                return clean
        elif value:
            return value
    return ""


def _looks_like_http_url(value: str) -> bool:
    return isinstance(value, str) and value.strip().lower().startswith(("http://", "https://"))


def _extract_original_link(edital: dict) -> str:
    """Retorna o link oficial capturado no site fonte, sem confundir com o link do painel.

    Muitos portais usam rotas tokenizadas, por exemplo:
    https://site.gov.br/editais/share/edital/<token>. Esse valor deve ser preservado como
    link_original/link_fonte. O FRONTEND_APP_URL e o ?id=... servem apenas para abrir a
    tela interna do painel.
    """
    payload = _safe_json_loads(edital.get("json_data"))
    smart_meta = payload.get("smart_crawler") if isinstance(payload.get("smart_crawler"), dict) else {}

    candidates = [
        edital.get("link_original"),
        edital.get("url_edital"),
        edital.get("url"),
        edital.get("link_fonte"),
        payload.get("link_original"),
        payload.get("url_edital"),
        payload.get("url"),
        payload.get("link"),
        smart_meta.get("url"),
        edital.get("link_origem"),
    ]
    for candidate in candidates:
        clean = str(candidate or "").strip()
        if _looks_like_http_url(clean):
            return clean
    return ""


def _build_frontend_link(edital_id) -> str:
    if edital_id in (None, ""):
        return ""
    base = str(getattr(utils, "FRONTEND_APP_URL", "") or "").strip()
    if not base:
        return ""
    separator = "&" if "?" in base else "?"
    return f"{base}{separator}id={edital_id}"


def _build_new_edital_summary(edital: dict) -> dict:
    edital_id = edital.get("id")
    link_painel = _build_frontend_link(edital_id)
    link_original = _extract_original_link(edital)
    return {
        "id": edital_id,
        "titulo": edital.get("titulo"),
        "fonte": edital.get("fonte"),
        "link": link_painel,  # compatibilidade com consumidores antigos do log/notificacao
        "link_painel": link_painel,
        "link_original": link_original,
    }

def _env_int(name: str, default: int, minimum: int = 1, maximum: int | None = None) -> int:
    try:
        value = int(str(os.getenv(name, str(default))).strip())
    except Exception:
        value = default
    value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


SCRAPER_SUBPROCESS_TIMEOUT_SECONDS = _env_int("SCRAPER_SUBPROCESS_TIMEOUT_SECONDS", 900, minimum=30)
SMART_FALLBACK_TIMEOUT_SECONDS = _env_int("SMART_FALLBACK_TIMEOUT_SECONDS", 900, minimum=30)
SKIP_INCOMPATIBLE_LEGACY_PYC = _env_flag("SKIP_INCOMPATIBLE_LEGACY_PYC", "1") if "_env_flag" in globals() else True

# Logs de execucao em tempo real.
# - SCRAPER_LOG_TO_CONSOLE=1: imprime resumo humano no terminal.
# - SCRAPER_LOG_VERBOSE=1: imprime detalhes por edital/candidato.
# - SCRAPER_STREAM_SUBPROCESS=1: espelha stdout/stderr dos subprocessos enquanto eles rodam.
SCRAPER_LOG_TO_CONSOLE = _env_flag("SCRAPER_LOG_TO_CONSOLE", "1")
SCRAPER_LOG_VERBOSE = _env_flag("SCRAPER_LOG_VERBOSE", "0")
SCRAPER_STREAM_SUBPROCESS = _env_flag("SCRAPER_STREAM_SUBPROCESS", "0")
SCRAPER_CONSOLE_SHOW_STDOUT_TAIL = _env_flag("SCRAPER_CONSOLE_SHOW_STDOUT_TAIL", "0")
SCRAPER_CONSOLE_OUTPUT_CHARS = _env_int("SCRAPER_CONSOLE_OUTPUT_CHARS", 8000, minimum=500, maximum=200000)
SCRAPER_FILE_STDOUT_CHARS = _env_int("SCRAPER_FILE_STDOUT_CHARS", 50000, minimum=1200, maximum=500000)
SCRAPER_FILE_STDERR_CHARS = _env_int("SCRAPER_FILE_STDERR_CHARS", 50000, minimum=1200, maximum=500000)
SCRAPER_AUDIT_STDOUT_CHARS = _env_int("SCRAPER_AUDIT_STDOUT_CHARS", 12000, minimum=1200, maximum=200000)
SCRAPER_AUDIT_STDERR_CHARS = _env_int("SCRAPER_AUDIT_STDERR_CHARS", 12000, minimum=1200, maximum=200000)
SCRAPER_CONSOLE_MAX_EDITAIS = _env_int("SCRAPER_CONSOLE_MAX_EDITAIS", 50, minimum=1, maximum=500)


def _decode_subprocess_output(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _run_subprocess(cmd: list[str], *, cwd: str, env: dict, timeout_seconds: int) -> SimpleNamespace:
    """Executa subprocesso com timeout e, opcionalmente, streaming em tempo real.

    O modo streaming evita a sensacao de "terminal congelado" durante fontes lentas e
    ainda preserva stdout/stderr para auditoria e log em arquivo.
    """
    if not SCRAPER_STREAM_SUBPROCESS:
        try:
            result = subprocess.run(
                cmd,
                cwd=cwd,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=timeout_seconds,
            )
            return SimpleNamespace(
                stdout=result.stdout or "",
                stderr=result.stderr or "",
                returncode=result.returncode,
                timed_out=False,
                skipped_legacy=False,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = _decode_subprocess_output(exc.stdout)
            stderr = _decode_subprocess_output(exc.stderr)
            msg = f"[TIMEOUT] Processo encerrado apos {timeout_seconds}s: {' '.join(cmd)}"
            if stderr:
                stderr += "\n"
            stderr += msg
            return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=124, timed_out=True, skipped_legacy=False)

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    proc = None

    def _reader(stream, collector: list[str], is_err: bool) -> None:
        try:
            for line in iter(stream.readline, ""):
                if not line:
                    break
                collector.append(line)
                # Mantem a linha original, mas com prefixo para ficar rastreavel no terminal.
                prefix = "[SUBPROCESS-ERR]" if is_err else "[SUBPROCESS]"
                if is_err:
                    utils.print_error(f"{prefix} {line.rstrip()}")
                else:
                    utils.print_info(f"{prefix} {line.rstrip()}")
        except Exception as exc:
            collector.append(f"[STREAM-ERROR] {exc}\n")

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        threads = [
            threading.Thread(target=_reader, args=(proc.stdout, stdout_lines, False), daemon=True),
            threading.Thread(target=_reader, args=(proc.stderr, stderr_lines, True), daemon=True),
        ]
        for t in threads:
            t.start()
        try:
            return_code = proc.wait(timeout=timeout_seconds)
            timed_out = False
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()
            return_code = 124
            msg = f"[TIMEOUT] Processo encerrado apos {timeout_seconds}s: {' '.join(cmd)}\n"
            stderr_lines.append(msg)
            utils.print_error(msg.rstrip())
        for t in threads:
            t.join(timeout=2)
        return SimpleNamespace(
            stdout="".join(stdout_lines),
            stderr="".join(stderr_lines),
            returncode=return_code,
            timed_out=timed_out,
            skipped_legacy=False,
        )
    except Exception as exc:
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
        return SimpleNamespace(stdout="".join(stdout_lines), stderr=str(exc), returncode=125, timed_out=False, skipped_legacy=False)

def _is_pyc_wrapper(arquivo: str) -> bool:
    path = os.path.join(PROJETO_DIR, arquivo)
    try:
        with open(path, "r", encoding="utf-8") as fp:
            head = fp.read(1200)
        return "load_module_from_pyc" in head and "export_module_namespace" in head
    except Exception:
        return False


def _legacy_pyc_status(arquivo: str) -> dict:
    path = os.path.join(PROJETO_DIR, arquivo)
    if not _is_pyc_wrapper(arquivo):
        return {"wrapper": False, "compatible": True, "existing": [], "compatible_paths": []}
    try:
        try:
            from . import _pyc_bridge as bridge  # type: ignore
        except Exception:
            import _pyc_bridge as bridge  # type: ignore
        candidates = bridge._candidate_pyc_paths(path)  # noqa: SLF001 - uso intencional para diagnostico local
        existing = [p for p in candidates if os.path.exists(p)]
        compatible_paths = [p for p in existing if bridge._pyc_magic_is_compatible(p)]  # noqa: SLF001
        return {
            "wrapper": True,
            "compatible": bool(compatible_paths),
            "existing": existing,
            "compatible_paths": compatible_paths,
        }
    except Exception as exc:
        return {"wrapper": True, "compatible": False, "existing": [], "compatible_paths": [], "error": str(exc)}


def _skipped_legacy_result(arquivo: str, status: dict) -> SimpleNamespace:
    existing = ", ".join(status.get("existing") or []) or "nenhum .pyc encontrado"
    stderr = (
        f"[LEGADO-SKIP] {arquivo}: wrapper .pyc sem bytecode compativel com Python "
        f"{sys.version_info.major}.{sys.version_info.minor}. Arquivos: {existing}. "
        "Fallback inteligente sera usado no lugar do legado."
    )
    return SimpleNamespace(stdout="", stderr=stderr, returncode=126, timed_out=False, skipped_legacy=True)


def _smart_fallback_enabled() -> bool:
    return _env_flag("ENABLE_SMART_FALLBACK", "1")


def _smart_fallback_on_success() -> bool:
    return _env_flag("SMART_FALLBACK_ON_SUCCESS", "1")


def _extract_smart_summary(stdout: str) -> dict:
    for line in reversed((stdout or "").splitlines()):
        marker = "[SMART-JSON]"
        if marker in line:
            raw = line.split(marker, 1)[1].strip()
            try:
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {"ok": False, "error": "Falha ao parsear resumo SMART"}
    return {}


def _executar_smart_fallback(arquivo: str, fonte_label: str) -> dict:
    if not _smart_fallback_enabled():
        return {"enabled": False, "summary": {}, "stdout": "", "stderr": "", "returncode": None}

    smart_path = os.path.join(PROJETO_DIR, "smart_fallback.py")
    if not os.path.exists(smart_path):
        return {"enabled": False, "summary": {}, "stdout": "", "stderr": "", "returncode": None}

    cmd = [sys.executable, smart_path, "--script", arquivo, "--fonte", fonte_label, "--persist"]
    try:
        env_exec = os.environ.copy()
        env_exec.setdefault("PYTHONUTF8", "1")
        env_exec.setdefault("PYTHONIOENCODING", "utf-8")
        result = _run_subprocess(
            cmd,
            cwd=PROJETO_DIR,
            env=env_exec,
            timeout_seconds=SMART_FALLBACK_TIMEOUT_SECONDS,
        )
        summary = _extract_smart_summary(result.stdout or "")
        if not summary:
            summary = {"ok": result.returncode == 0, "persisted": 0, "candidates": 0}
        if result.returncode != 0:
            summary.setdefault("ok", False)
            summary.setdefault("error", _extract_error_reason(result.stdout or "", result.stderr or "", result.returncode))
        return {
            "enabled": True,
            "summary": summary,
            "stdout": result.stdout or "",
            "stderr": result.stderr or "",
            "returncode": result.returncode,
        }
    except Exception as exc:
        return {
            "enabled": True,
            "summary": {"ok": False, "error": str(exc), "persisted": 0, "candidates": 0},
            "stdout": "",
            "stderr": str(exc),
            "returncode": None,
        }


def _sanitize_filename_part(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "").strip())
    clean = clean.strip("_")
    return clean or "ciclo"


def _tipo_disparo(nome_ciclo: str) -> str:
    ciclo = str(nome_ciclo or "").strip().lower()
    return "agendado" if re.fullmatch(r"\d{2}h", ciclo) else "manual"


def _criar_log_inicio_ciclo(nome_ciclo: str, instante_inicio: datetime.datetime | None = None) -> str | None:
    inicio = instante_inicio or _now()
    ciclo_key = _sanitize_filename_part(nome_ciclo)
    timestamp = inicio.strftime("%Y%m%d_%H%M%S")
    tipo_disparo = _tipo_disparo(nome_ciclo)

    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        base_name = f"{timestamp}_{ciclo_key}"
        filename = f"{base_name}.txt"
        path = os.path.join(LOG_DIR, filename)

        seq = 1
        while os.path.exists(path):
            filename = f"{base_name}_{seq:02d}.txt"
            path = os.path.join(LOG_DIR, filename)
            seq += 1

        with open(path, "w", encoding="utf-8") as fp:
            fp.write("LOG DE INICIO DO CICLO\n")
            fp.write("=" * 60 + "\n")
            fp.write(f"ciclo: {nome_ciclo}\n")
            fp.write(f"tipo_disparo: {tipo_disparo}\n")
            fp.write(f"iniciado_em: {inicio.strftime('%Y-%m-%d %H:%M:%S')}\n")
            fp.write(f"timezone: {TZ_NAME if _TZ else 'local'}\n")
            fp.write(f"scripts_detectados: {TOTAL_SCRIPTS}\n")
            fp.write(f"fontes_total_config: {FONTES_TOTAL_DIARIAS}\n")
            fp.write(f"timeout_scraper_segundos: {SCRAPER_SUBPROCESS_TIMEOUT_SECONDS}\n")
            fp.write(f"timeout_smart_fallback_segundos: {SMART_FALLBACK_TIMEOUT_SECONDS}\n")
        return path
    except Exception as exc:
        utils.print_error(f"Falha ao criar log de inicio do ciclo: {exc}")
        return None


def _append_log_txt(caminho_log: str | None, texto: str) -> None:
    if not caminho_log:
        return
    try:
        with open(caminho_log, "a", encoding="utf-8") as fp:
            fp.write(texto)
            if not texto.endswith("\n"):
                fp.write("\n")
    except Exception as exc:
        utils.print_error(f"Falha ao escrever no log de ciclo: {exc}")


def _to_bool_or_none(value) -> bool | None:
    if isinstance(value, bool):
        return value
    token = str(value or "").strip().lower()
    if token in {"true", "1", "sim", "yes", "aprovado"}:
        return True
    if token in {"false", "0", "nao", "não", "no", "reprovado"}:
        return False
    return None


def _parse_json_data(raw_json) -> dict:
    if isinstance(raw_json, dict):
        return raw_json
    if isinstance(raw_json, str) and raw_json.strip():
        try:
            parsed = json.loads(raw_json)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
    return {}


def _resumo_aprovacao_editais(editais: list[dict]) -> dict:
    aprovados = 0
    reprovados = 0
    incertos = 0
    divergencias_web_check = 0
    amostra: list[dict] = []

    for item in editais or []:
        data = _parse_json_data(item.get("json_data"))
        aprovado_flag = _to_bool_or_none(data.get("aprovado"))
        if aprovado_flag is True:
            aprovados += 1
            status_ia = "aprovado"
        elif aprovado_flag is False:
            reprovados += 1
            status_ia = "reprovado"
        else:
            incertos += 1
            status_ia = "incerto"

        web_check = data.get("web_check") if isinstance(data.get("web_check"), dict) else {}
        if web_check.get("divergencia_status") is True:
            divergencias_web_check += 1

        smart_meta = data.get("smart_crawler") if isinstance(data.get("smart_crawler"), dict) else {}
        amostra.append(
            {
                "id": item.get("id"),
                "titulo": str(item.get("titulo") or data.get("titulo") or "").strip(),
                "ia": status_ia,
                "motivo_ia": str(data.get("motivo_aprovacao") or "").strip(),
                "status_final": str(web_check.get("status_final") or data.get("status_detectado") or "").strip(),
                "fonte_status_final": str(web_check.get("fonte_decisao_final") or "").strip(),
                "divergencia_web_check": bool(web_check.get("divergencia_status")),
                "prazo": str(data.get("data_final_submissao") or data.get("prazo_final") or data.get("data_fim") or "").strip(),
                "confianca": data.get("confianca_captura") if data.get("confianca_captura") is not None else smart_meta.get("confidence"),
                "score": smart_meta.get("score"),
                "documentos_detectados": smart_meta.get("documentos_detectados"),
                "link_original": _extract_original_link(item),
                "link_painel": _build_frontend_link(item.get("id")),
                "descricao": str(data.get("descricao") or "").strip(),
            }
        )

    return {
        "aprovados": aprovados,
        "reprovados": reprovados,
        "incertos": incertos,
        "divergencias_web_check": divergencias_web_check,
        "amostra": amostra,
    }


def _clip_console(value, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if limit and len(text) > limit:
        return text[: max(0, limit - 3)] + "..."
    return text


def _format_metric(value) -> str:
    if value in (None, ""):
        return "-"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _console_log_smart_summary(fonte_label: str, smart_summary: dict) -> None:
    if not SCRAPER_LOG_TO_CONSOLE or not isinstance(smart_summary, dict) or not smart_summary:
        return
    utils.print_info(
        "[SMART-RESUMO] "
        f"fonte={fonte_label} | ok={smart_summary.get('ok')} | "
        f"paginas={smart_summary.get('pages_fetched', 0)} | falhas={smart_summary.get('pages_failed', 0)} | "
        f"links={smart_summary.get('links_seen', 0)} | docs={smart_summary.get('documents_seen', 0)} | "
        f"candidatos={smart_summary.get('candidates', 0)} | persistidos={smart_summary.get('persisted', 0)} | "
        f"pulados={smart_summary.get('skipped', 0)}"
    )
    if smart_summary.get("warnings") and SCRAPER_LOG_VERBOSE:
        for warning in smart_summary.get("warnings", [])[:8]:
            utils.print_info(f"[SMART-WARNING] {fonte_label}: {_clip_console(warning, 500)}")
    if smart_summary.get("errors"):
        for error in smart_summary.get("errors", [])[:8]:
            utils.print_error(f"[SMART-ERRO] {fonte_label}: {_clip_console(error, 600)}")


def _escrever_console_script(
    *,
    indice: int,
    arquivo: str,
    fonte_label: str,
    status_execucao: str,
    motivo_falha: str | None,
    return_code: int | None,
    novos_script: list[dict],
    smart_summary: dict,
    stdout_tail: str,
    stderr_tail: str,
) -> None:
    if not SCRAPER_LOG_TO_CONSOLE:
        return

    resumo = _resumo_aprovacao_editais(novos_script or [])
    utils.print_section(f"RESUMO DA FONTE {indice}/{TOTAL_SCRIPTS}: {fonte_label}")
    if status_execucao in {"sucesso", "sucesso_com_fallback"}:
        utils.print_success(
            f"{arquivo} | status={status_execucao} | return_code={return_code} | "
            f"novos={len(novos_script or [])} | aprovados_ia={resumo['aprovados']} | "
            f"reprovados_ia={resumo['reprovados']} | incertos_ia={resumo['incertos']} | "
            f"divergencias_web_check={resumo['divergencias_web_check']}"
        )
    else:
        utils.print_error(
            f"{arquivo} | status={status_execucao} | return_code={return_code} | motivo={motivo_falha or '-'}"
        )

    _console_log_smart_summary(fonte_label, smart_summary if isinstance(smart_summary, dict) else {})

    if resumo["amostra"]:
        utils.print_info(f"[EDITAIS-COLETADOS] Mostrando ate {min(SCRAPER_CONSOLE_MAX_EDITAIS, len(resumo['amostra']))}/{len(resumo['amostra'])}")
        for edital in resumo["amostra"][:SCRAPER_CONSOLE_MAX_EDITAIS]:
            utils.print_info(
                "[EDITAL] "
                f"id={edital.get('id')} | ia={edital.get('ia')} | "
                f"status_final={edital.get('status_final') or '-'} | "
                f"prazo={edital.get('prazo') or '-'} | "
                f"confianca={_format_metric(edital.get('confianca'))} | "
                f"docs={_format_metric(edital.get('documentos_detectados'))} | "
                f"titulo={_clip_console(edital.get('titulo'), 220)}"
            )
            if edital.get("link_original"):
                utils.print_info(f"  link_original: {edital.get('link_original')}")
            if edital.get("link_painel"):
                utils.print_info(f"  link_painel: {edital.get('link_painel')}")
            if edital.get("motivo_ia"):
                utils.print_info(f"  motivo_ia: {_clip_console(edital.get('motivo_ia'), 500)}")
            if SCRAPER_LOG_VERBOSE and edital.get("descricao"):
                utils.print_info(f"  descricao: {_clip_console(edital.get('descricao'), 700)}")
    else:
        utils.print_info("[EDITAIS-COLETADOS] Nenhum edital novo salvo nessa fonte.")

    if SCRAPER_CONSOLE_SHOW_STDOUT_TAIL and stdout_tail:
        utils.print_info("[STDOUT-TAIL]\n" + stdout_tail[-SCRAPER_CONSOLE_OUTPUT_CHARS:])
    if stderr_tail and (SCRAPER_LOG_VERBOSE or status_execucao == "erro"):
        utils.print_error("[STDERR-TAIL]\n" + stderr_tail[-SCRAPER_CONSOLE_OUTPUT_CHARS:])


def _escrever_log_script(
    caminho_log: str | None,
    *,
    indice: int,
    arquivo: str,
    fonte_label: str,
    inicio_script,
    fim_script,
    status_execucao: str,
    motivo_falha: str | None,
    return_code: int | None,
    novos_script: list[dict],
    stdout_tail: str,
    stderr_tail: str,
) -> None:
    resumo = _resumo_aprovacao_editais(novos_script or [])
    _append_log_txt(caminho_log, "\n" + "-" * 80)
    _append_log_txt(caminho_log, f"SCRIPT {indice}/{TOTAL_SCRIPTS}: {arquivo}")
    _append_log_txt(caminho_log, f"fonte: {fonte_label}")
    _append_log_txt(caminho_log, f"inicio: {inicio_script}")
    _append_log_txt(caminho_log, f"fim: {fim_script}")
    _append_log_txt(caminho_log, f"status_execucao: {status_execucao}")
    _append_log_txt(caminho_log, f"return_code: {return_code}")
    _append_log_txt(caminho_log, f"qtd_editais_novos: {len(novos_script or [])}")
    _append_log_txt(caminho_log, f"aprovados_ia: {resumo['aprovados']}")
    _append_log_txt(caminho_log, f"reprovados_ia: {resumo['reprovados']}")
    _append_log_txt(caminho_log, f"incertos_ia: {resumo['incertos']}")
    _append_log_txt(caminho_log, f"divergencias_web_check: {resumo['divergencias_web_check']}")
    _append_log_txt(caminho_log, f"causa_erro: {motivo_falha or '-'}")

    if "[SMART_FALLBACK_STDOUT]" in (stdout_tail or ""):
        smart_block = (stdout_tail or "").split("[SMART_FALLBACK_STDOUT]", 1)[-1]
        for line in smart_block.splitlines():
            if "[SMART-JSON]" in line:
                _append_log_txt(caminho_log, f"smart_fallback: {line.strip()}")
                break

    if resumo["amostra"]:
        _append_log_txt(caminho_log, "editais_novos:")
        for edital in resumo["amostra"][:50]:
            _append_log_txt(
                caminho_log,
                (
                    f"- id={edital.get('id')} | ia={edital.get('ia')} | "
                    f"divergencia_web_check={edital.get('divergencia_web_check')} | "
                    f"status_final={edital.get('status_final') or '-'} | "
                    f"fonte_final={edital.get('fonte_status_final') or '-'} | "
                    f"prazo={edital.get('prazo') or '-'} | "
                    f"confianca={_format_metric(edital.get('confianca'))} | "
                    f"docs={_format_metric(edital.get('documentos_detectados'))} | "
                    f"titulo={edital.get('titulo')}"
                ),
            )
            if edital.get("link_original"):
                _append_log_txt(caminho_log, f"  link_original: {edital.get('link_original')}")
            if edital.get("link_painel"):
                _append_log_txt(caminho_log, f"  link_painel: {edital.get('link_painel')}")
            if edital.get("motivo_ia"):
                _append_log_txt(caminho_log, f"  motivo_ia: {edital.get('motivo_ia')}")
            if edital.get("descricao"):
                _append_log_txt(caminho_log, f"  descricao: {_clip_console(edital.get('descricao'), 700)}")

    if stdout_tail:
        _append_log_txt(caminho_log, "\nstdout_tail:")
        _append_log_txt(caminho_log, stdout_tail[-SCRAPER_FILE_STDOUT_CHARS:])
    if stderr_tail:
        _append_log_txt(caminho_log, "\nstderr_tail:")
        _append_log_txt(caminho_log, stderr_tail[-SCRAPER_FILE_STDERR_CHARS:])


def _escrever_log_fim_ciclo(
    caminho_log: str | None,
    *,
    nome_ciclo: str,
    fontes_ok: int,
    fontes_erro: int,
    total_novos: int,
    status_geral: str,
) -> None:
    fim = _now().strftime("%Y-%m-%d %H:%M:%S")
    _append_log_txt(caminho_log, "\n" + "=" * 60)
    _append_log_txt(caminho_log, "RESUMO FINAL DO CICLO")
    _append_log_txt(caminho_log, "=" * 60)
    _append_log_txt(caminho_log, f"ciclo: {nome_ciclo}")
    _append_log_txt(caminho_log, f"finalizado_em: {fim}")
    _append_log_txt(caminho_log, f"status_geral: {status_geral}")
    _append_log_txt(caminho_log, f"fontes_ok: {fontes_ok}")
    _append_log_txt(caminho_log, f"fontes_erro: {fontes_erro}")
    _append_log_txt(caminho_log, f"fontes_total_implementadas: {TOTAL_SCRIPTS}")
    _append_log_txt(caminho_log, f"fontes_total_config: {FONTES_TOTAL_DIARIAS}")
    _append_log_txt(caminho_log, f"total_editais_novos_ciclo: {total_novos}")


def executar_ciclo(nome_ciclo: str = "Manual") -> None:
    inicio_local = _now()
    caminho_log = _criar_log_inicio_ciclo(nome_ciclo=nome_ciclo, instante_inicio=inicio_local)
    if caminho_log:
        utils.print_info(f"[LOG] Arquivo de inicio criado: {caminho_log}")
        _append_log_txt(caminho_log, "\nEXECUCAO INICIADA")
        _append_log_txt(caminho_log, f"inicio_local: {inicio_local.strftime('%Y-%m-%d %H:%M:%S')}")

    if TOTAL_SCRIPTS == 0:
        utils.print_error("Nenhum scraper encontrado para executar (SCRIPTS vazio).")
        _append_log_txt(caminho_log, "ERRO: Nenhum scraper encontrado para executar (SCRIPTS vazio).")
        return

    data_inicio = utils.obter_data_sql()
    utils.print_section(
        f"INICIANDO CICLO DE EDITAIS: {nome_ciclo} ({data_inicio.strftime('%H:%M:%S')})"
    )
    utils.print_info(
        f"Progresso monitoramento: 0/{FONTES_TOTAL_DIARIAS} fontes sem erro (implementadas no ciclo: {TOTAL_SCRIPTS})"
    )

    fontes_ok = 0
    fontes_erro = 0
    resultados_monitoramento = []

    for i, arquivo in enumerate(SCRIPTS, start=1):
        fonte_label = _script_fonte_label(arquivo)
        inicio_script = utils.obter_data_sql()
        cmd = _build_exec_command(arquivo)

        utils.print_section(f"SCRIPT {i}/{TOTAL_SCRIPTS}: {arquivo}")

        try:
            env_exec = os.environ.copy()
            env_exec.setdefault("PYTHONUTF8", "1")
            env_exec.setdefault("PYTHONIOENCODING", "utf-8")

            pyc_status = _legacy_pyc_status(arquivo)
            if SKIP_INCOMPATIBLE_LEGACY_PYC and pyc_status.get("wrapper") and not pyc_status.get("compatible"):
                run_result = _skipped_legacy_result(arquivo, pyc_status)
            else:
                run_result = _run_subprocess(
                    cmd,
                    cwd=os.path.dirname(PROJETO_DIR),
                    env=env_exec,
                    timeout_seconds=SCRAPER_SUBPROCESS_TIMEOUT_SECONDS,
                )

            motivo_legacy = _extract_error_reason(
                run_result.stdout or "",
                run_result.stderr or "",
                run_result.returncode,
            )

            smart_result = {"enabled": False, "summary": {}, "stdout": "", "stderr": "", "returncode": None}
            if _smart_fallback_enabled() and (_smart_fallback_on_success() or motivo_legacy):
                utils.print_info(f"[SMART] Rodando fallback inteligente para {fonte_label}...")
                smart_result = _executar_smart_fallback(arquivo, fonte_label)

            fim_script = utils.obter_data_sql()
            novos_script = utils.obter_editais_criados_desde(inicio_script)
            qtd_novos = len(novos_script)

            smart_summary = smart_result.get("summary") if isinstance(smart_result, dict) else {}
            smart_persisted = int((smart_summary or {}).get("persisted") or 0) if isinstance(smart_summary, dict) else 0
            smart_ok = bool((smart_summary or {}).get("ok")) if isinstance(smart_summary, dict) else False
            smart_error = str((smart_summary or {}).get("error") or "").strip() if isinstance(smart_summary, dict) else ""

            if motivo_legacy and smart_ok:
                status_execucao = "sucesso_com_fallback"
                motivo_falha = None
                motivo_observacao = (
                    f"Legado falhou/pulado, mas SMART processou a fonte "
                    f"(persistidos={smart_persisted}, candidatos={(smart_summary or {}).get('candidates', 0)}, "
                    f"paginas={(smart_summary or {}).get('pages_fetched', 0)}). Falha legado: {motivo_legacy}"
                )
            elif motivo_legacy:
                status_execucao = "erro"
                motivo_falha = motivo_legacy
                motivo_observacao = motivo_legacy
            else:
                status_execucao = "sucesso"
                motivo_falha = None
                motivo_observacao = smart_error if smart_error else None

            if status_execucao in {"sucesso", "sucesso_com_fallback"}:
                fontes_ok += 1
                utils.print_success(f"Fonte processada: {fonte_label} | status={status_execucao} | novos={qtd_novos} | smart_persistidos={smart_persisted}")
                if smart_error and not motivo_legacy:
                    utils.print_info(f"[SMART] Aviso controlado em {fonte_label}: {smart_error}")
            else:
                fontes_erro += 1
                utils.print_error(f"Fonte com erro: {fonte_label} | motivo={motivo_falha}")

            stdout_completo = (run_result.stdout or "")
            stderr_completo = (run_result.stderr or "")
            if smart_result.get("enabled"):
                stdout_completo += "\n\n[SMART_FALLBACK_STDOUT]\n" + (smart_result.get("stdout") or "")
                stderr_completo += "\n\n[SMART_FALLBACK_STDERR]\n" + (smart_result.get("stderr") or "")

            detalhes = {
                "script": arquivo,
                "fonte_processada": fonte_label,
                "status_execucao": status_execucao,
                "motivo_falha": motivo_falha or None,
                "observacao": motivo_observacao,
                "return_code": run_result.returncode,
                "timed_out": bool(getattr(run_result, "timed_out", False)),
                "skipped_legacy": bool(getattr(run_result, "skipped_legacy", False)),
                "smart_fallback": smart_summary,
                "smart_return_code": smart_result.get("returncode"),
                "tempo_inicio": str(inicio_script),
                "tempo_fim": str(fim_script),
                "qtd_editais_novos": qtd_novos,
                "editais_novos": [
                    _build_new_edital_summary(e)
                    for e in novos_script[:30]
                ],
                "stdout_tail": stdout_completo[-SCRAPER_AUDIT_STDOUT_CHARS:],
                "stderr_tail": stderr_completo[-SCRAPER_AUDIT_STDERR_CHARS:],
                "fonte_index": i,
                "fontes_total_implementadas": TOTAL_SCRIPTS,
                "fontes_total_config": FONTES_TOTAL_DIARIAS,
                "fontes_ok_execucao": fontes_ok,
                "fontes_erro_execucao": fontes_erro,
            }

            _escrever_log_script(
                caminho_log,
                indice=i,
                arquivo=arquivo,
                fonte_label=fonte_label,
                inicio_script=inicio_script,
                fim_script=fim_script,
                status_execucao=status_execucao,
                motivo_falha=motivo_falha or None,
                return_code=run_result.returncode,
                novos_script=novos_script,
                stdout_tail=stdout_completo,
                stderr_tail=stderr_completo,
            )
            _escrever_console_script(
                indice=i,
                arquivo=arquivo,
                fonte_label=fonte_label,
                status_execucao=status_execucao,
                motivo_falha=motivo_falha or None,
                return_code=run_result.returncode,
                novos_script=novos_script,
                smart_summary=smart_summary if isinstance(smart_summary, dict) else {},
                stdout_tail=stdout_completo,
                stderr_tail=stderr_completo,
            )

            # Erro de auditoria nao deve invalidar scraping bem-sucedido.
            utils.registrar_log_auditoria(
                nome_ciclo,
                fonte_label,
                qtd_novos if status_execucao in {"sucesso", "sucesso_com_fallback"} else 0,
                detalhes,
                status=status_execucao,
            )

            resultados_monitoramento.append(
                {
                    "fonte": fonte_label,
                    "status": status_execucao,
                    "motivo_falha": motivo_falha,
                    "qtd_novos": qtd_novos,
                }
            )

            utils.print_info(
                f"Progresso monitoramento: {fontes_ok}/{FONTES_TOTAL_DIARIAS} fontes sem erro (executadas: {i}/{TOTAL_SCRIPTS}, com erro: {fontes_erro})"
            )

        except Exception as e:
            fontes_erro += 1
            motivo_falha = f"Falha ao executar {arquivo}: {e}"
            fim_script = utils.obter_data_sql()
            utils.print_error(motivo_falha)

            _escrever_log_script(
                caminho_log,
                indice=i,
                arquivo=arquivo,
                fonte_label=fonte_label,
                inicio_script=inicio_script,
                fim_script=fim_script,
                status_execucao="erro",
                motivo_falha=motivo_falha,
                return_code=None,
                novos_script=[],
                stdout_tail="",
                stderr_tail="",
            )
            _escrever_console_script(
                indice=i,
                arquivo=arquivo,
                fonte_label=fonte_label,
                status_execucao="erro",
                motivo_falha=motivo_falha,
                return_code=None,
                novos_script=[],
                smart_summary={},
                stdout_tail="",
                stderr_tail="",
            )

            utils.registrar_log_auditoria(
                nome_ciclo,
                fonte_label,
                0,
                {
                    "script": arquivo,
                    "fonte_processada": fonte_label,
                    "status_execucao": "erro",
                    "motivo_falha": motivo_falha,
                    "tempo_inicio": str(inicio_script),
                    "tempo_fim": str(fim_script),
                    "fontes_total_implementadas": TOTAL_SCRIPTS,
                    "fontes_total_config": FONTES_TOTAL_DIARIAS,
                    "fontes_ok_execucao": fontes_ok,
                    "fontes_erro_execucao": fontes_erro,
                },
                status="erro",
            )

            resultados_monitoramento.append(
                {"fonte": fonte_label, "status": "erro", "motivo_falha": motivo_falha, "qtd_novos": 0}
            )
            utils.print_info(
                f"Progresso monitoramento: {fontes_ok}/{FONTES_TOTAL_DIARIAS} fontes sem erro (executadas: {i}/{TOTAL_SCRIPTS}, com erro: {fontes_erro})"
            )

    utils.print_section("SCRAPING FINALIZADO. PROCESSANDO AUDITORIA...")

    total_novos = 0
    status_geral = "erro" if fontes_erro > 0 else "sucesso"
    try:
        editais_novos = utils.obter_editais_criados_desde(data_inicio)
        total_novos = len(editais_novos)
        msg = "Ciclo concluido com fontes em erro." if fontes_erro > 0 else "Ciclo concluido sem erro de fontes."

        utils.registrar_log_auditoria(
            nome_ciclo,
            "Geral",
            total_novos,
            {
                "msg": msg,
                "tempo_inicio": str(data_inicio),
                "tempo_fim": str(utils.obter_data_sql()),
                "total_scripts": TOTAL_SCRIPTS,
                "fontes_ok": fontes_ok,
                "fontes_erro": fontes_erro,
                "fontes_total_config": FONTES_TOTAL_DIARIAS,
                "total_editais_novos_ciclo": total_novos,
                "resultado_fontes": resultados_monitoramento,
            },
            status=status_geral,
        )
    except Exception as e:
        utils.print_error(f"Erro ao registrar auditoria: {e}")
        _append_log_txt(caminho_log, f"ERRO_AUDITORIA: {e}")

    _escrever_log_fim_ciclo(
        caminho_log,
        nome_ciclo=nome_ciclo,
        fontes_ok=fontes_ok,
        fontes_erro=fontes_erro,
        total_novos=total_novos,
        status_geral=status_geral,
    )

    utils.print_success(
        f"CICLO {nome_ciclo} CONCLUIDO | fontes sem erro: {fontes_ok}/{FONTES_TOTAL_DIARIAS} | fontes com erro: {fontes_erro}"
    )


def _proximo_agendamento(agora: datetime.datetime, horas_execucao: list[int]) -> tuple[datetime.datetime, str]:
    horas_execucao = sorted({int(h) for h in horas_execucao})
    hoje = agora.date()
    amanha = hoje + datetime.timedelta(days=1)

    candidatos: list[tuple[datetime.datetime, str]] = []
    for dia in (hoje, amanha):
        for h in horas_execucao:
            if _TZ:
                dt = datetime.datetime(dia.year, dia.month, dia.day, h, 0, 0, tzinfo=_TZ)
            else:
                dt = datetime.datetime(dia.year, dia.month, dia.day, h, 0, 0)
            candidatos.append((dt, f"{h:02d}h"))

    futuros = [c for c in candidatos if c[0] > agora]
    futuros.sort(key=lambda x: x[0])
    return futuros[0]


def agendador_diario(horas_execucao: list[int] | None = None) -> None:
    horas_execucao = horas_execucao or [0, 6, 18]

    utils.print_section("ENTRANDO EM MODO DE AGENDAMENTO AUTOMATICO")
    utils.print_info(
        "Agendado: "
        + ", ".join([f"{h:02d}:00" for h in sorted(horas_execucao)])
        + f" (TZ={TZ_NAME if _TZ else 'local'})"
    )
    utils.print_info("Pressione Ctrl+C para encerrar.")

    ultima_execucao_slot: tuple[datetime.date, int] | None = None

    while True:
        agora = _now()
        prox, ciclo_label = _proximo_agendamento(agora, horas_execucao)
        segundos = max(1, int((prox - agora).total_seconds()))
        utils.print_info(
            f"[AGENDADOR] Agora: {agora.strftime('%Y-%m-%d %H:%M:%S')} | Proxima execucao: {prox.strftime('%Y-%m-%d %H:%M:%S')} ({ciclo_label}) | Dormindo {segundos}s"
        )
        time.sleep(segundos)

        agora2 = _now()
        slot = (agora2.date(), agora2.hour)
        if ultima_execucao_slot == slot:
            time.sleep(5)
            continue

        executar_ciclo(ciclo_label)
        ultima_execucao_slot = slot
        time.sleep(10)


def _parse_horarios(csv: str) -> list[int]:
    try:
        horas = [int(x.strip()) for x in (csv or "").split(",") if x.strip()]
    except Exception:
        horas = [0, 6, 18]
    horas = sorted({h for h in horas if 0 <= h <= 23})
    return horas or [0, 6, 18]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Agendador de captacao de editais")
    parser.add_argument("--run-now", action="store_true", help="Executa um ciclo imediatamente e depois agenda")
    parser.add_argument("--once", action="store_true", help="Executa uma vez e encerra")
    parser.add_argument("--ciclo", default="Manual", help="Nome do ciclo quando usar --run-now/--once")
    parser.add_argument(
        "--horarios",
        default=os.getenv("SCRAPER_HORARIOS", "0,6,18"),
        help="Horarios no formato CSV. Ex: 0,6,18",
    )
    args = parser.parse_args(argv)

    run_on_startup = os.getenv("RUN_ON_STARTUP", "0") == "1"
    horas_execucao = _parse_horarios(args.horarios)

    if args.run_now or args.once or run_on_startup:
        executar_ciclo(args.ciclo)
    if args.once:
        return 0

    agendador_diario(horas_execucao=horas_execucao)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        utils.print_info("Encerrando agendador...")
