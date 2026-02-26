# Edital.py

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

import utils

# =========================================================
# Configurações
# =========================================================
PROJETO_DIR = os.path.dirname(os.path.abspath(__file__))

IGNORAR = {
    "IA.py",
    "utils.py",
    "Edital.py",
}

# Identifica scripts de scraping disponíveis
SCRIPTS = [f for f in os.listdir(PROJETO_DIR) if f.endswith(".py") and f not in IGNORAR]
TOTAL_SCRIPTS = len(SCRIPTS)

# Timezone do agendador (pra rodar à meia-noite *de verdade*)
TZ_NAME = os.getenv("SCRAPER_TZ", "America/Sao_Paulo")
_TZ = None
if ZoneInfo:
    try:
        _TZ = ZoneInfo(TZ_NAME)
    except Exception:
        _TZ = None


def _now() -> datetime.datetime:
    return datetime.datetime.now(_TZ) if _TZ else datetime.datetime.now()


def _proxima_meia_noite(agora: datetime.datetime) -> datetime.datetime:
    # Próxima 00:00 no fuso configurado
    amanha = agora.date() + datetime.timedelta(days=1)
    if _TZ:
        return datetime.datetime.combine(amanha, datetime.time(0, 0, 0), tzinfo=_TZ)
    return datetime.datetime.combine(amanha, datetime.time(0, 0, 0))


# =========================================================
# Execução do ciclo
# =========================================================

def executar_ciclo(nome_ciclo: str = "Manual"):
    """Executa todos os scrapers e registra auditoria com detalhes + incertezas."""

    # Evita drift de relógio: data oficial do SQL antes de começar
    data_inicio = utils.obter_data_sql()

    utils.print_section(
        f"INICIANDO CICLO DE EDITAIS: {nome_ciclo} ({data_inicio.strftime('%H:%M:%S')})"
    )

    # 1) Executar Scrapers
    for i, arquivo in enumerate(SCRIPTS, start=1):
        utils.print_section(f"SCRIPT {i}/{TOTAL_SCRIPTS}: {arquivo}")

        try:
            caminho_script = os.path.join(PROJETO_DIR, arquivo)
            # Executa o script como um módulo independente
            subprocess.run(
                [sys.executable, "-m", f"sites.{arquivo[:-3]}"],
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

def agendador_diario():
    """Roda automaticamente TODO DIA à meia-noite (00:00) no fuso configurado."""

    utils.print_section("ENTRANDO EM MODO DE AGENDAMENTO AUTOMÁTICO")
    print(f"Agendado: 00:00 diário (TZ={TZ_NAME if _TZ else 'local'})")
    print("Pressione Ctrl+C para encerrar.")

    # Proteção simples: garante no máximo 1 execução por dia
    ultima_execucao_dia = None

    while True:
        agora = _now()
        prox = _proxima_meia_noite(agora)
        segundos = max(1, int((prox - agora).total_seconds()))

        # Log (só pra debug humano)
        print(f"[AGENDADOR] Agora: {agora.strftime('%Y-%m-%d %H:%M:%S')} | Próxima execução: {prox.strftime('%Y-%m-%d %H:%M:%S')} | Dormindo {segundos}s")
        time.sleep(segundos)

        # Ao acordar, roda e marca o dia
        agora2 = _now()
        dia = agora2.date()
        if ultima_execucao_dia == dia:
            # já rodou hoje (proteção extra)
            time.sleep(5)
            continue

        executar_ciclo("00h")
        ultima_execucao_dia = dia

        # Pequena pausa para evitar loop em caso de clock skew
        time.sleep(10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agendador de captação de editais")
    parser.add_argument("--run-now", action="store_true", help="Executa um ciclo imediatamente antes de agendar")
    parser.add_argument("--once", action="store_true", help="Executa uma vez e encerra (útil para teste)")
    parser.add_argument("--ciclo", default="Manual", help="Nome do ciclo quando usar --run-now/--once")
    args = parser.parse_args()

    # Para evitar 'execução aleatória' em reinícios, NÃO rodamos nada por padrão.
    # Se quiser rodar ao iniciar, use --run-now, --once ou defina RUN_ON_STARTUP=1
    run_on_startup = os.getenv("RUN_ON_STARTUP", "0") == "1"

    if args.run_now or args.once or run_on_startup:
        executar_ciclo(args.ciclo)

    if args.once:
        raise SystemExit(0)

    try:
        agendador_diario()
    except KeyboardInterrupt:
        print("\nEncerrando agendador...")
