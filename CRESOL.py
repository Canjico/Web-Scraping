# CRESOL.py

import os
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

from . import utils
from .IA import ia_client

# ==============================================
# CONFIGURAÇÃO
# ==============================================
URL_ALVO = "https://cresol.com.br/instituto/"

PROJETO_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = os.path.join(PROJETO_DIR, "temp_downloads_cresol")

INSTITUICAO = "Cresol"
ICONE_PADRAO = "/icons/cresol.png"


class CresolProcessor:

    def __init__(self):
        self._reset_temp_dir()

    # ==================================================
    # SETUP
    # ==================================================
    def _reset_temp_dir(self):
        if os.path.exists(TEMP_DIR):
            utils.safe_rmtree(TEMP_DIR)
        os.makedirs(TEMP_DIR, exist_ok=True)

    # ==================================================
    # EXECUÇÃO PRINCIPAL
    # ==================================================
    def executar(self):

        utils.print_section("INICIANDO SCRAPING: CRESOL")

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=False,
                slow_mo=50
            )

            context = browser.new_context(
                locale="pt-BR",
                timezone_id="America/Sao_Paulo",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/121.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800}
            )

            page = context.new_page()

            try:
                # ALTERAÇÃO: Mudança de networkidle para domcontentloaded para evitar travamentos em trackers
                utils.page_goto_humano(page, URL_ALVO, wait_until="domcontentloaded", timeout=90000)
                
                # ALTERAÇÃO: Fecha cookies proativamente
                utils.fechar_cookies(page)
                page.wait_for_timeout(3000)

                html_content = page.content()
                if "403 Forbidden" in html_content or "Access Denied" in html_content:
                    utils.logger.error("Site bloqueou o acesso (403/Access Denied). Abortando.")
                    return

                titulo_pagina = page.title()
                texto_pagina = page.inner_text("body")

                if not texto_pagina or len(texto_pagina) < 500:
                    utils.logger.warning("Texto insuficiente para análise. Abortando.")
                    return

                textos_analise = [texto_pagina]

                # =========================
                # IA — CHAMADA ÚNICA
                # =========================
                utils.logger.info("Enviando conteúdo para análise da IA...")
                resultado_ia = ia_client.analisar_edital_completo(
                    textos=textos_analise,
                    contexto="Cresol – Instituto"
                )

                # ==================================================
                # REGRA DE NEGÓCIO LOCAL — CRESOL
                # ==================================================
                # Espelhando exatamente o comportamento do Tetra Pak
                resultado_ia["fluxo_continuo"] = True
                resultado_ia["data_final_submissao"] = "Fluxo Contínuo"
                resultado_ia["aprovado"] = True
                resultado_ia["motivo_aprovacao"] = (
                    "Programa institucional com submissão em fluxo contínuo."
                )

                if not resultado_ia.get("aprovado"):
                    utils.logger.warning(
                        f"⛔ REPROVADO pela IA: {resultado_ia.get('motivo_aprovacao')}"
                    )
                    return

                # =========================
                # APROVADO
                # =========================
                utils.print_success(f"✅ APROVADO: {resultado_ia.get('titulo')}")
                utils.logger.info(f"Motivo: {resultado_ia.get('motivo_aprovacao')}")

                resultado_ia["icone_instituicao"] = ICONE_PADRAO

                titulo_final = resultado_ia.get("titulo") or titulo_pagina
                edital_id = utils.obter_ou_criar_edital(
                    titulo_final,
                    INSTITUICAO,
                    url_edital=URL_ALVO
                )

                utils.atualizar_edital_ia(edital_id, resultado_ia)

                # =========================
                # VINCULA PÁGINA WEB
                # =========================
                utils.salvar_arquivo_no_edital(
                    edital_id=edital_id,
                    url_arquivo=URL_ALVO,
                    tipo="Página Web"
                )

                arquivos_json = [{
                    "tipo": "Página Web",
                    "url": URL_ALVO,
                    "nome": urlparse(URL_ALVO).netloc
                }]

                utils.atualizar_edital_arquivos_json(edital_id, arquivos_json)

            except Exception as e:
                utils.logger.error(f"Erro ao processar CRESOL: {e}")

            finally:
                browser.close()

        self._reset_temp_dir()
        utils.print_success("Scraping CRESOL finalizado com sucesso.")


if __name__ == "__main__":
    CresolProcessor().executar()