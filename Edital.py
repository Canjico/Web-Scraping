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

import os
import subprocess
import sys
import time
import json
import datetime
import argparse

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

# Suporta execução como módulo (python -m sites.Edital) e como script (python Edital.py)
try:
    from . import utils  # type: ignore
except Exception:  # pragma: no cover
    import utils  # type: ignore


# =========================================================
# Configurações
# =========================================================
PROJETO_DIR = os.path.dirname(os.path.abspath(__file__))

IGNORAR = {
    "__init__.py",
    "IA.py",
    "utils.py",
    "Edital.py",
}

# Identifica scripts de scraping disponíveis
SCRIPTS = sorted(
    f
    for f in os.listdir(PROJETO_DIR)
    if f.endswith(".py") and f not in IGNORAR and not f.startswith("_")
)
TOTAL_SCRIPTS = len(SCRIPTS)

# Timezone do agendador (pra rodar às 00:00/06:00/18:00 *no fuso certo*)
TZ_NAME = os.getenv("SCRAPER_TZ", "America/Sao_Paulo")
_TZ = None
if ZoneInfo:
    try:
        _TZ = ZoneInfo(TZ_NAME)
    except Exception:
        _TZ = None


def _now() -> datetime.datetime:
    return datetime.datetime.now(_TZ) if _TZ else datetime.datetime.now()


# =========================================================
# Execução do ciclo
# =========================================================

def executar_ciclo(nome_ciclo: str = "Manual") -> None:
    """Executa todos os scrapers e registra auditoria com detalhes + incertezas."""

    if TOTAL_SCRIPTS == 0:
        utils.print_error("Nenhum scraper encontrado para executar (SCRIPTS vazio).")
        return

    # Evita drift de relógio: data oficial do SQL antes de começar
    data_inicio = utils.obter_data_sql()

    utils.print_section(
        f"INICIANDO CICLO DE EDITAIS: {nome_ciclo} ({data_inicio.strftime('%H:%M:%S')})"
    )

    # 1) Executar Scrapers
    for i, arquivo in enumerate(SCRIPTS, start=1):
        utils.print_section(f"SCRIPT {i}/{TOTAL_SCRIPTS}: {arquivo}")

        try:
            # Executa o script como módulo (mais estável/imports corretos)
            modulo = f"sites.{arquivo[:-3]}"
            subprocess.run(
                [sys.executable, "-m", modulo],
                cwd=os.path.dirname(PROJETO_DIR),
                check=False,
            )
        except Exception as e:
            utils.print_error(f"Falha ao executar {arquivo}: {e}")

    utils.print_section("SCRAPING FINALIZADO. PROCESSANDO AUDITORIA...")

    # 2) Montar auditoria detalhada (editais criados neste ciclo)
    try:
        editais_novos = utils.obter_editais_criados_desde(data_inicio)

        if not editais_novos:
            utils.registrar_log_auditoria(
                nome_ciclo,
                "Geral",
                0,
                {
                    "msg": "Nenhum edital novo captado neste ciclo",
                    "tempo_inicio": str(data_inicio),
                    "total_scripts": TOTAL_SCRIPTS,
                },
            )
        else:
            # Agrupa por fonte
            from collections import defaultdict

            por_fonte = defaultdict(list)
            for e in editais_novos:
                por_fonte[(e.get("fonte") or "Desconhecida")].append(e)

            for fonte, lista in por_fonte.items():
                detalhes = {
                    "tempo_inicio": str(data_inicio),
                    "total_scripts": TOTAL_SCRIPTS,
                    "editais": [],
                }

                for e in lista:
                    jd = {}
                    try:
                        jd = json.loads(e.get("json_data") or "{}")
                    except Exception:
                        jd = {}

                    incertezas = jd.get("incertezas") or jd.get("campos_incertos")
                    if not isinstance(incertezas, list):
                        incertezas = utils.detectar_incertezas(jd, fonte=fonte)

                    detalhes["editais"].append(
                        {
                            "id": e.get("id"),
                            "titulo": e.get("titulo"),
                            "link": f"{utils.FRONTEND_APP_URL}?id={e.get('id')}",
                            "incertezas": incertezas,
                        }
                    )

                utils.registrar_log_auditoria(
                    nome_ciclo,
                    fonte,
                    len(lista),
                    detalhes,
                )

    except Exception as e:
        utils.print_error(f"Erro ao registrar auditoria: {e}")

    utils.print_success(f"CICLO {nome_ciclo} CONCLUÍDO.")


# =========================================================
# Agendador
# =========================================================

def _proximo_agendamento(
    agora: datetime.datetime,
    horas_execucao: list[int],
) -> tuple[datetime.datetime, str]:
    """Retorna (datetime da próxima execução, label do ciclo)."""
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
            label = f"{h:02d}h"
            candidatos.append((dt, label))

    futuros = [c for c in candidatos if c[0] > agora]
    futuros.sort(key=lambda x: x[0])
    return futuros[0]


def agendador_diario(horas_execucao: list[int] | None = None) -> None:
    """Roda automaticamente TODO DIA nos horários configurados (padrão: 00:00, 06:00, 18:00)."""

    horas_execucao = horas_execucao or [0, 6, 18]

    utils.print_section("ENTRANDO EM MODO DE AGENDAMENTO AUTOMÁTICO")
    print(
        "Agendado: "
        + ", ".join([f"{h:02d}:00" for h in sorted(horas_execucao)])
        + f" (TZ={TZ_NAME if _TZ else 'local'})"
    )
    print("Pressione Ctrl+C para encerrar.")

    # Proteção: garante no máximo 1 execução por slot (dia+hora)
    ultima_execucao_slot: tuple[datetime.date, int] | None = None

    while True:
        agora = _now()
        prox, ciclo_label = _proximo_agendamento(agora, horas_execucao)
        segundos = max(1, int((prox - agora).total_seconds()))

        print(
            f"[AGENDADOR] Agora: {agora.strftime('%Y-%m-%d %H:%M:%S')} | "
            f"Próxima execução: {prox.strftime('%Y-%m-%d %H:%M:%S')} ({ciclo_label}) | "
            f"Dormindo {segundos}s"
        )

        time.sleep(segundos)

        agora2 = _now()
        slot = (agora2.date(), agora2.hour)

        if ultima_execucao_slot == slot:
            # Já rodou esse slot (proteção extra para drift/loop)
            time.sleep(5)
            continue

        executar_ciclo(ciclo_label)
        ultima_execucao_slot = slot

        # Pequena pausa para evitar loop em caso de clock skew
        time.sleep(10)


# =========================================================
# CLI
# =========================================================

def _parse_horarios(csv: str) -> list[int]:
    try:
        horas = [int(x.strip()) for x in (csv or "").split(",") if x.strip() != ""]
    except Exception:
        horas = [0, 6, 18]

    # Sanitize: mantém apenas 0-23 e remove duplicados
    horas = sorted({h for h in horas if 0 <= h <= 23})
    return horas or [0, 6, 18]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Agendador de captação de editais")
    parser.add_argument(
        "--run-now",
        action="store_true",
        help="Executa um ciclo imediatamente (teste manual) e depois entra no agendador",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Executa uma vez e encerra (teste/CI)",
    )
    parser.add_argument(
        "--ciclo",
        default="Manual",
        help="Nome do ciclo quando usar --run-now/--once",
    )
    parser.add_argument(
        "--horarios",
        default=os.getenv("SCRAPER_HORARIOS", "0,6,18"),
        help="Horários do scraping (horas cheias) no formato CSV. Ex: 0,6,18",
    )
    args = parser.parse_args(argv)

    # Para evitar 'execução aleatória' em reinícios, NÃO rodamos nada por padrão.
    # Se quiser rodar ao iniciar, use --run-now, --once ou defina RUN_ON_STARTUP=1
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
        print("\nEncerrando agendador...")
