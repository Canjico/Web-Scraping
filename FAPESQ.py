# FAPESQ.py

from urllib.parse import urljoin
from playwright.sync_api import sync_playwright

from . import utils
from .IA import ia_client


# ==============================================
# CONFIGURAÇÃO
# ==============================================
BASE_URL_LISTA = "https://fapesq.rpp.br/editais/editais-abertos"
BASE_DOMAIN = "https://fapesq.rpp.br"

INSTITUICAO = "FAPESQ"
ICONE_PADRAO = "/icons/fapesq.png"


class FapesqProcessor:

    def executar(self):

        utils.print_section("INICIANDO SCRAPING: FAPESQ")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            page = browser.new_page()

            try:
                utils.page_goto_humano(page, BASE_URL_LISTA, timeout=120000)
                page.wait_for_selector("a.summary.url", timeout=30000)

                links = []
                for a in page.locator("a.summary.url").all():
                    href = a.get_attribute("href")
                    if href:
                        links.append(urljoin(BASE_DOMAIN, href))

                utils.logger.info(f"{len(links)} editais encontrados.")

                for link in links:
                    utils.logger.info(f"\n>>> Processando edital: {link}")

                    try:
                        utils.page_goto_humano(page, link, timeout=60000)

                        texto_pagina = page.inner_text("body")

                        # Coleta anexos
                        anexos_urls = []
                        for a in page.locator("div#content a[href]").all():
                            href = a.get_attribute("href")
                            if href and "download" in href.lower():
                                anexos_urls.append(
                                    urljoin(BASE_DOMAIN, href)
                                )

                        textos = [texto_pagina]

                        for url_anexo in anexos_urls:
                            try:
                                texto_anexo = utils.extrair_texto_por_url(
                                    url_anexo
                                )
                                if texto_anexo:
                                    textos.append(texto_anexo)
                            except Exception as e:
                                utils.logger.warning(
                                    f"Erro ao extrair texto do anexo: {e}"
                                )
                        if not texto_pagina or len(texto_pagina) < 300:
                            utils.logger.warning(
                                 "Texto insuficiente na página. Pulando."
                             )
                            continue

                        resultado_ia = ia_client.analisar_edital_completo(
                            textos=textos,
                            contexto="Editais FAPESQ"
                         )

                        if not resultado_ia.get("aprovado"):
                            utils.logger.warning(
                                f"⛔ REPROVADO pela IA: {resultado_ia.get('motivo_aprovacao')}"
                            )
                            continue

                        utils.print_success(
                            f"✅ APROVADO: {resultado_ia.get('titulo')}"
                        )

                        resultado_ia["icone_instituicao"] = ICONE_PADRAO

                        titulo = resultado_ia.get("titulo") or "Edital FAPESQ"
                        edital_id = utils.obter_ou_criar_edital(
                    titulo,
                    INSTITUICAO,
                    url_edital=link
                )

                        utils.atualizar_edital_ia(
                            edital_id,
                            resultado_ia
                        )

                        # Página principal
                        utils.salvar_arquivo_no_edital(
                            edital_id,
                            url_arquivo=link,
                            tipo="Página Web"
                        )

                        # Anexos (salvos direto no SQL)
                        for url_anexo in anexos_urls:
                            utils.salvar_arquivo_no_edital(
                                edital_id,
                                url_arquivo=url_anexo,
                                tipo="Documento"
                            )

                    except Exception as e:
                        utils.logger.error(
                            f"Erro ao processar edital FAPESQ: {e}"
                        )

            finally:
                browser.close()

        utils.print_success("Scraping FAPESQ finalizado.")


if __name__ == "__main__":
    FapesqProcessor().executar()
