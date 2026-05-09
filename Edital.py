import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import time

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
#   python -m sites.Edital                 -> entra no agendador (padrão 00, 06, 18)
#   python -m sites.Edital --run-now       -> roda agora e depois agenda
#   python -m sites.Edital --once          -> roda uma vez e encerra (teste/CI)
#   python -m sites.Edital --horarios 0,6,18
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
    module_name = arquivo[:-3]
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", module_name):
        return [sys.executable, "-m", f"sites.{module_name}"]
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
        result = subprocess.run(
            cmd,
            cwd=PROJETO_DIR,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env_exec,
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

        amostra.append(
            {
                "id": item.get("id"),
                "titulo": str(item.get("titulo") or "").strip(),
                "ia": status_ia,
                "motivo_ia": str(data.get("motivo_aprovacao") or "").strip(),
                "status_final": str(web_check.get("status_final") or "").strip(),
                "fonte_status_final": str(web_check.get("fonte_decisao_final") or "").strip(),
                "divergencia_web_check": bool(web_check.get("divergencia_status")),
            }
        )

    return {
        "aprovados": aprovados,
        "reprovados": reprovados,
        "incertos": incertos,
        "divergencias_web_check": divergencias_web_check,
        "amostra": amostra,
    }


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
                    f"titulo={edital.get('titulo')}"
                ),
            )
            if edital.get("motivo_ia"):
                _append_log_txt(caminho_log, f"  motivo_ia: {edital.get('motivo_ia')}")

    if stdout_tail:
        _append_log_txt(caminho_log, "\nstdout_tail:")
        _append_log_txt(caminho_log, stdout_tail[-1200:])
    if stderr_tail:
        _append_log_txt(caminho_log, "\nstderr_tail:")
        _append_log_txt(caminho_log, stderr_tail[-1200:])


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

            run_result = subprocess.run(
                cmd,
                cwd=os.path.dirname(PROJETO_DIR),
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env_exec,
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
            smart_error = str((smart_summary or {}).get("error") or "").strip() if isinstance(smart_summary, dict) else ""

            if motivo_legacy and smart_persisted > 0:
                status_execucao = "sucesso_com_fallback"
                motivo_falha = None
                motivo_observacao = f"Legado falhou, mas SMART persistiu {smart_persisted} candidato(s). Falha legado: {motivo_legacy}"
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
                "smart_fallback": smart_summary,
                "smart_return_code": smart_result.get("returncode"),
                "tempo_inicio": str(inicio_script),
                "tempo_fim": str(fim_script),
                "qtd_editais_novos": qtd_novos,
                "editais_novos": [
                    {
                        "id": e.get("id"),
                        "titulo": e.get("titulo"),
                        "fonte": e.get("fonte"),
                        "link": f"{utils.FRONTEND_APP_URL}?id={e.get('id')}",
                    }
                    for e in novos_script[:30]
                ],
                "stdout_tail": stdout_completo[-1200:],
                "stderr_tail": stderr_completo[-1200:],
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
