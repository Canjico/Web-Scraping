# BASF.py

import os
import shutil
from playwright.sync_api import sync_playwright
from urllib.parse import urlparse

from . import utils
from .IA import ia_client


# ==============================================
# CONFIGURAÇÃO
# ==============================================
PROJETO_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = os.path.join(PROJETO_DIR, "temp_downloads_basf")

URL_ALVO = "https://agriculture.basf.com/global/en/innovations-for-agriculture/agrostart/challenges"
INSTITUICAO = "BASF AgroStart"
ICONE_PADRAO = "/icons/basf.png"


class BasfProcessor:

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

        utils.print_section("INICIANDO SCRAPING: BASF AGROSTART")

        texto_pagina = ""

        # 1️⃣ Coleta do conteúdo da página
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                utils.page_goto_humano(page, URL_ALVO, timeout=60000)
                texto_pagina = page.inner_text("body")
                browser.close()

        except Exception as e:
            utils.logger.error(f"Erro ao acessar página BASF: {e}")
            return

        if not texto_pagina or len(texto_pagina) < 500:
            utils.logger.warning("Texto insuficiente para análise. Abortando.")
            return

        # 2️⃣ Envio para IA (CHAMADA ÚNICA)
        utils.logger.info("Enviando conteúdo para análise da IA...")
        resultado_ia = ia_client.analisar_edital_completo(
            textos=[texto_pagina],
            contexto="BASF AgroStart Challenges"
        )

        if not resultado_ia.get("aprovado"):
            utils.logger.warning(
                f"⛔ REPROVADO pela IA: {resultado_ia.get('motivo_aprovacao')}"
            )
            return

        # 3️⃣ APROVADO
        utils.print_success(f"✅ APROVADO: {resultado_ia.get('titulo')}")
        utils.logger.info(f"Motivo: {resultado_ia.get('motivo_aprovacao')}")

        # Garante ícone correto
        resultado_ia["icone_instituicao"] = ICONE_PADRAO

        # 4️⃣ Cria ou recupera edital
        titulo_final = resultado_ia.get("titulo") or "BASF AgroStart"
        edital_id = utils.obter_ou_criar_edital(titulo_final, INSTITUICAO, url_edital=URL_ALVO)

        # 5️⃣ Atualiza dados ricos do edital
        utils.atualizar_edital_ia(edital_id, resultado_ia)

        # 6️⃣ Vincula URL principal como arquivo lógico
        utils.salvar_arquivo_no_edital(
            edital_id=edital_id,
            url_arquivo=URL_ALVO,
            tipo="Página Web"
        )

        # 7️⃣ Atualiza JSON de arquivos
        utils.atualizar_edital_arquivos_json(
            edital_id,
            arquivos_json=[
                {
                    "tipo": "Página Web",
                    "url": URL_ALVO,
                    "nome": urlparse(URL_ALVO).netloc
                }
            ]
        )

        utils.print_success("Processamento BASF finalizado com sucesso.")
        self._preparar_diretorio_temporario()


if __name__ == "__main__":
    BasfProcessor().executar()
