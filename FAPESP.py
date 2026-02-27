# FAPESP.py

import os
from playwright.sync_api import sync_playwright

from . import utils
from .IA import ia_client


# ==============================================
# CONFIGURAÇÃO
# ==============================================
PROJETO_DIR = os.path.dirname(os.path.abspath(__file__))

BASE_URL = "https://fapesp.br/chamadas/"
INSTITUICAO = "FAPESP"
ICONE_PADRAO = "/icons/fapesp.png"


class FapespProcessor:

    def __init__(self):
        self.session = utils.get_http_session()

    # ==================================================
    # FASE 1 — DESCOBERTA (COLETA LINKS)
    # ==================================================
    def _coletar_links_editais(self, page):

        utils.print_section("FASE 1: COLETA DE LINKS FAPESP")

        page.goto(BASE_URL, wait_until="networkidle", timeout=120000)
        
        # Blindagem: esperar 'attached' em vez de 'visible' evita erros quando o elemento 
        # já está no DOM mas fora da viewport ou oculto por CSS.
        try:
            page.wait_for_selector("h3 a", state="attached", timeout=15000)
        except Exception:
            utils.logger.warning("Links (h3 a) não confirmados como 'attached' imediatamente. Continuando coleta...")

        links_encontrados = set()

        anchors = page.locator("h3 a").all()

        for a in anchors:
            href = a.get_attribute("href")
            if not href:
                continue

            if "acordos-dfg" in href:
                continue

            links_encontrados.add(href)

        utils.logger.info(
            f"Total de editais encontrados: {len(links_encontrados)}"
        )

        return sorted(links_encontrados)

    # ==================================================
    # FASE 2 — EXECUÇÃO PRINCIPAL
    # ==================================================
    def executar(self):

        utils.print_section("INICIANDO SCRAPING: FAPESP")

        with sync_playwright() as p:

            # 🔹 abertura estilo TetraPak (anti-bloqueio)
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
                # ============================
                # FASE 1 — DESCOBERTA
                # ============================
                urls_editais = self._coletar_links_editais(page)

                # ============================
                # FASE 2 — PROCESSAMENTO
                # ============================
                for i, url_edital in enumerate(urls_editais, start=1):

                    utils.logger.info(
                        f"\n>>> Processando edital {i}/{len(urls_editais)}: {url_edital}"
                    )

                    try:
                        page.goto(url_edital, wait_until="networkidle", timeout=120000)
                        page.wait_for_timeout(2000)

                        texto_pagina = page.inner_text("body")

                        if not texto_pagina or len(texto_pagina) < 500:
                            utils.logger.warning(
                                "Texto insuficiente para análise."
                            )
                            continue

                        # =========================
                        # IA — CHAMADA ÚNICA
                        # =========================
                        utils.logger.info("Enviando página para análise da IA...")

                        resultado_ia = ia_client.analisar_edital_completo(
                            textos=[texto_pagina],
                            contexto="FAPESP - Chamadas Públicas"
                        )

                        if not resultado_ia.get("aprovado"):
                            utils.logger.warning(
                                f"⛔ REPROVADO pela IA: {resultado_ia.get('motivo_aprovacao')}"
                            )
                            continue

                        # =========================
                        # APROVADO
                        # =========================
                        utils.print_success(
                            f"✅ APROVADO: {resultado_ia.get('titulo')}"
                        )

                        resultado_ia["icone_instituicao"] = ICONE_PADRAO

                        titulo_final = (
                            resultado_ia.get("titulo")
                            or "Edital FAPESP"
                        )

                        edital_id = utils.obter_ou_criar_edital(
                    titulo_final,
                    INSTITUICAO,
                    url_edital=url_edital
                )

                        utils.atualizar_edital_ia(edital_id, resultado_ia)

                        # =========================
                        # SALVA LINK ORIGINAL
                        # =========================
                        utils.salvar_arquivo_no_edital(
                            edital_id=edital_id,
                            url_arquivo=url_edital,
                            tipo="Página Web"
                        )

                        arquivos_json = [{
                            "tipo": "Página Web",
                            "url": url_edital,
                            "nome": url_edital.split("/")[-1] or "fapesp"
                        }]

                        utils.atualizar_edital_arquivos_json(
                            edital_id,
                            arquivos_json
                        )

                    except Exception as e_item:
                        utils.logger.error(
                            f"Erro ao processar edital {url_edital}: {e_item}"
                        )
                        continue

            finally:
                browser.close()

        utils.print_success("Scraping FAPESP finalizado.")
        

if __name__ == "__main__":
    FapespProcessor().executar()