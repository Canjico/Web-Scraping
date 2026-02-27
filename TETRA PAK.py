# TETRAPAK.py

import os
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

from . import utils
from .IA import ia_client

# ==============================================
# CONFIGURAÇÃO
# ==============================================
URL_ALVO = "https://www.tetrapak.com/pt-br/startup-tetra-pak"

PROJETO_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = os.path.join(PROJETO_DIR, "temp_downloads_tetrapak")

INSTITUICAO = "Tetra Pak"
ICONE_PADRAO = "/icons/tetrapak.png"


class TetraPakProcessor:

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

        utils.print_section("INICIANDO SCRAPING: TETRA PAK")

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
                utils.page_goto_humano(page, URL_ALVO, wait_until="networkidle", timeout=90000)
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
                    contexto="Tetra Pak – Programas para Startups"
                )

                # ==================================================
                # REGRA DE NEGÓCIO LOCAL — TETRA PAK
                # ==================================================
                # Força a aprovação e fluxo contínuo, independente da inferência da IA
                resultado_ia["fluxo_continuo"] = True
                resultado_ia["data_final_submissao"] = "Fluxo Contínuo"
                resultado_ia["aprovado"] = True
                resultado_ia["motivo_aprovacao"] = "Programa institucional com submissão em fluxo contínuo."

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
                    INSTITUICAO
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
                utils.logger.error(f"Erro ao processar Tetra Pak: {e}")

            finally:
                browser.close()

        self._reset_temp_dir()
        utils.print_success("Scraping Tetra Pak finalizado com sucesso.")


if __name__ == "__main__":
    TetraPakProcessor().executar()
