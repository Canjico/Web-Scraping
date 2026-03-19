import argparse
import datetime
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

IGNORAR = {
    "__init__.py",
    "IA.py",
    "utils.py",
    "Edital.py",
    "_pyc_bridge.py",
}

SCRIPTS = sorted(
    f
    for f in os.listdir(PROJETO_DIR)
    if f.endswith(".py") and f not in IGNORAR and not f.startswith("_")
)
TOTAL_SCRIPTS = len(SCRIPTS)
FONTES_TOTAL_DIARIAS = max(
    TOTAL_SCRIPTS,
    int(os.getenv("FONTES_TOTAL_DIARIAS", os.getenv("SCRAPER_FONTES_TOTAL", "42"))),
)

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
        return f"Processo encerrou com codigo {returncode}"
    return ""


def executar_ciclo(nome_ciclo: str = "Manual") -> None:
    if TOTAL_SCRIPTS == 0:
        utils.print_error("Nenhum scraper encontrado para executar (SCRIPTS vazio).")
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

            fim_script = utils.obter_data_sql()
            novos_script = utils.obter_editais_criados_desde(inicio_script)
            qtd_novos = len(novos_script)

            motivo_falha = _extract_error_reason(
                run_result.stdout or "",
                run_result.stderr or "",
                run_result.returncode,
            )
            status_execucao = "erro" if motivo_falha else "sucesso"

            if status_execucao == "sucesso":
                fontes_ok += 1
                utils.print_success(f"Fonte processada com sucesso: {fonte_label} | novos={qtd_novos}")
            else:
                fontes_erro += 1
                utils.print_error(f"Fonte com erro: {fonte_label} | motivo={motivo_falha}")

            detalhes = {
                "script": arquivo,
                "fonte_processada": fonte_label,
                "status_execucao": status_execucao,
                "motivo_falha": motivo_falha or None,
                "return_code": run_result.returncode,
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
                "stdout_tail": (run_result.stdout or "")[-1200:],
                "stderr_tail": (run_result.stderr or "")[-1200:],
                "fonte_index": i,
                "fontes_total_implementadas": TOTAL_SCRIPTS,
                "fontes_total_config": FONTES_TOTAL_DIARIAS,
                "fontes_ok_execucao": fontes_ok,
                "fontes_erro_execucao": fontes_erro,
            }

            # Erro de auditoria nao deve invalidar scraping bem-sucedido.
            utils.registrar_log_auditoria(
                nome_ciclo,
                fonte_label,
                qtd_novos if status_execucao == "sucesso" else 0,
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
            utils.print_error(motivo_falha)

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
                    "tempo_fim": str(utils.obter_data_sql()),
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

    try:
        editais_novos = utils.obter_editais_criados_desde(data_inicio)
        total_novos = len(editais_novos)
        status_geral = "erro" if fontes_erro > 0 else "sucesso"
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

