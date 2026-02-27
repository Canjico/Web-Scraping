# COCA_COLA.py

import os
import shutil
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

from . import utils
from .IA import ia_client

# ==============================================
# CONFIGURAÇÃO
# ==============================================
PROJETO_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = os.path.join(PROJETO_DIR, "temp_downloads_cocacola")

URL_ALVO = "https://www.ideiasparaummundomelhor25.com.br/"
INSTITUICAO = "Coca-Cola Brasil"
ICONE_PADRAO = "/icons/coca-cola.png"


class CocaColaProcessor:

    def __init__(self):
        self.session = utils.get_http_session()
        self._preparar_diretorio_temporario()

    def _preparar_diretorio_temporario(self):
        if os.path.exists(TEMP_DIR):
            shutil.rmtree(TEMP_DIR)
        os.makedirs(TEMP_DIR, exist_ok=True)

    # ==================================================
    # EXECUÇÃO PRINCIPAL
    # ==================================================
    def executar(self):

        utils.print_section("INICIANDO SCRAPING: COCA-COLA")

        texto_pagina = ""

        # 1️⃣ Coleta do conteúdo da página
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=False)
                page = browser.new_page()
                utils.page_goto_humano(page, URL_ALVO, timeout=60000)
                texto_pagina = page.inner_text("body")
                browser.close()

        except Exception as e:
            utils.logger.error(f"Erro ao acessar página Coca-Cola: {e}")
            return

        if not texto_pagina or len(texto_pagina) < 500:
            utils.logger.warning("Texto insuficiente para análise. Abortando.")
            return

        # 2️⃣ Envio para IA (APENAS ANÁLISE, SEM VALIDAÇÃO DE PRAZO)
        utils.logger.info("Enviando conteúdo para análise da IA...")
        resultado_ia = ia_client.analisar_edital_completo(
            textos=[texto_pagina],
            contexto="Coca-Cola – Ideias para um Mundo Melhor"
        )

        # 3️⃣ Cria ou recupera edital (não há reprovação)
        titulo_final = resultado_ia.get("titulo") or "Coca-Cola – Ideias para um Mundo Melhor"
        edital_id = utils.obter_ou_criar_edital(titulo_final, INSTITUICAO, url_edital=URL_ALVO)

        # 4️⃣ Atualiza dados do edital
        resultado_ia["icone_instituicao"] = ICONE_PADRAO
        utils.atualizar_edital_ia(edital_id, resultado_ia)

        # 5️⃣ Vincula URL principal como arquivo lógico
        utils.salvar_arquivo_no_edital(
            edital_id=edital_id,
            url_arquivo=URL_ALVO,
            tipo="Página Web"
        )

        # 6️⃣ Atualiza JSON de arquivos
        utils.atualizar_edital_arquivos_json(
            edital_id,
            arquivos_json=[{
                "tipo": "Página Web",
                "url": URL_ALVO,
                "nome": urlparse(URL_ALVO).netloc
            }]
        )

        utils.print_success("Processamento Coca-Cola finalizado com sucesso.")
        self._preparar_diretorio_temporario()


if __name__ == "__main__":
    CocaColaProcessor().executar()
