# Edital.py

import os
import subprocess
import sys
import time
import datetime
import utils

# Configurações
PROJETO_DIR = os.path.dirname(os.path.abspath(__file__))
IGNORAR = {
    "IA.py",
    "utils.py",
    "Edital.py"
}

# Identifica scripts de scraping disponíveis
SCRIPTS = [f for f in os.listdir(PROJETO_DIR) if f.endswith(".py") and f not in IGNORAR]
TOTAL_SCRIPTS = len(SCRIPTS)


def executar_ciclo(nome_ciclo="Manual"):
    """
    Executa todos os scripts de scraping e registra a auditoria.
    """
    # Evita drift de relógio: Pega a data OFICIAL do banco antes de começar
    data_inicio = utils.obter_data_sql()
    
    utils.print_section(f"INICIANDO CICLO DE EDITAIS: {nome_ciclo} ({data_inicio.strftime('%H:%M:%S')})")

    # 1. Executar Scrapers
    for i, arquivo in enumerate(SCRIPTS, start=1):
        utils.print_section(f"SCRIPT {i}/{TOTAL_SCRIPTS}: {arquivo}")
        
        try:
            caminho_script = os.path.join(PROJETO_DIR, arquivo)
            # Executa o script como um módulo independente
            subprocess.run(
                [sys.executable, "-m", f"sites.{arquivo[:-3]}"],
                cwd=os.path.dirname(PROJETO_DIR),
                check=False
            )
        except Exception as e:
            utils.print_error(f"Falha ao executar {arquivo}: {e}")

    utils.print_section("SCRAPING FINALIZADO. PROCESSANDO AUDITORIA...")

    # 2. Processar Auditoria (Novos Editais inseridos neste ciclo)
    # A função obter_resumo_captacao conta quantos editais têm created_at >= data_inicio
    try:
        resumo_novos = utils.obter_resumo_captacao(data_inicio)
        
        if not resumo_novos:
            # Log de ciclo vazio (para garantir rastreabilidade que rodou)
            utils.registrar_log_auditoria(nome_ciclo, "Geral", 0, 
                {"msg": "Nenhum edital novo captado neste ciclo", "total_scripts": TOTAL_SCRIPTS}
            )
        else:
            # Log detalhado por fonte
            for fonte, qtd in resumo_novos:
                fonte_real = fonte if fonte else "Desconhecida"
                utils.registrar_log_auditoria(
                    nome_ciclo, 
                    fonte_real, 
                    qtd, 
                    {
                        "tempo_inicio": str(data_inicio),
                        "total_scripts": TOTAL_SCRIPTS
                    }
                )
                
    except Exception as e:
        utils.print_error(f"Erro ao registrar auditoria: {e}")

    utils.print_success(f"CICLO {nome_ciclo} CONCLUÍDO.")


def agendador():
    """
    Mantém o script rodando e verifica os horários de execução (06h, 10h, 18h).
    """
    utils.print_section("ENTRANDO EM MODO DE AGENDAMENTO AUTOMÁTICO")
    print("Horários programados: 06:00, 10:00, 18:00 (Fuso Horário Local)")
    print("Pressione Ctrl+C para encerrar.")

    horarios_alvo = [6, 10, 18]
    ultimo_horario_executado = -1

    while True:
        agora = datetime.datetime.now()
        
        # Verifica se estamos na hora cheia de um dos alvos e se ainda não rodamos nesta hora
        if agora.hour in horarios_alvo and agora.minute == 0 and agora.hour != ultimo_horario_executado:
            ciclo_nome = f"{agora.hour:02d}h"
            executar_ciclo(ciclo_nome)
            ultimo_horario_executado = agora.hour
            time.sleep(65) # Dorme pouco mais de 1 min para não repetir no mesmo minuto
        else:
            # Reseta o bloqueio se mudou de hora
            if agora.hour != ultimo_horario_executado:
                 ultimo_horario_executado = -1
            
            # Verifica a cada 30 segundos
            time.sleep(30)


if __name__ == "__main__":
    # 1. Execução Imediata (Manual/Start)
    executar_ciclo("Inicial/Manual")
    
    # 2. Entra no Loop de Agendamento
    try:
        agendador()
    except KeyboardInterrupt:
        print("\nEncerrando agendador...")