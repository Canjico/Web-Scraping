# FAPITEC.py

from playwright.sync_api import sync_playwright
from urllib.parse import urljoin

from . import utils
from .IA import ia_client


# ==============================================
# CONFIGURAÇÃO
# ==============================================
BASE_URL = "https://fapitec.se.gov.br/editais-abertos/"
BASE_DOMAIN = "https://fapitec.se.gov.br"

INSTITUICAO = "FAPITEC"
ICONE_PADRAO = "/icons/fapitec.png"

EXTENSOES_VALIDAS = (".pdf",)


class FapitecProcessor:

    def executar(self):

        utils.print_section("INICIANDO SCRAPING: FAPITEC")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            try:
                page.goto(BASE_URL, timeout=120000)

                links_editais = [
                    urljoin(BASE_DOMAIN, a.get_attribute("href"))
                    for a in page.locator("li.editais-abertos a[href]").all()
                    if a.get_attribute("href")
                ]

                utils.logger.info(f"{len(links_editais)} editais encontrados.")

                for url_edital in links_editais:
                    utils.logger.info(f"\n>>> Processando edital: {url_edital}")

                    try:
                        page.goto(url_edital, timeout=60000)

                        texto_pagina = page.inner_text("body")

                        links_anexos = [
                            urljoin(BASE_DOMAIN, a.get_attribute("href"))
                            for a in page.locator("div.entry-content a[href]").all()
                            if a.get_attribute("href").lower().endswith(EXTENSOES_VALIDAS)
                        ]

                        textos_ia = []

                        if texto_pagina and len(texto_pagina) > 500:
                            textos_ia.append(texto_pagina[:8000])

                        for link in links_anexos:
                            try:
                                texto_pdf = utils.extrair_texto_por_url(link)
                                if texto_pdf and len(texto_pdf) > 500:
                                    textos_ia.append(
                                        f"Arquivo: {link.split('/')[-1]}\n{texto_pdf[:8000]}"
                                    )
                            except Exception as e:
                                utils.logger.warning(
                                    f"Falha ao extrair PDF {link}: {e}"
                                )

                        if not textos_ia:
                            utils.logger.warning("Nenhum texto válido para IA. Pulando.")
                            continue

                        resultado_ia = ia_client.analisar_edital_completo(
                            textos=textos_ia,
                            contexto="Editais FAPITEC"
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

                        titulo = resultado_ia.get("titulo") or "Edital FAPITEC"
                        edital_id = utils.obter_ou_criar_edital(
                            titulo,
                            INSTITUICAO
                        )

                        # Atualiza dados IA no banco
                        utils.atualizar_edital_ia(
                            edital_id,
                            resultado_ia
                        )

                        # ==================================================
                        # ✅ SALVAMENTO IGUAL AO FAPEG (SQL FIRST)
                        # ==================================================
                        arquivos_json = []

                        for link in links_anexos:

                            utils.salvar_arquivo_no_edital(
                                edital_id=edital_id,
                                url_arquivo=link,
                                tipo="Edital/Anexo"
                            )

                            arquivos_json.append({
                                "tipo": "Documento",
                                "url": link,
                                "nome": link.split("/")[-1]
                            })

                        # JSON apenas consolidado (igual FAPEG)
                        if arquivos_json:
                            utils.atualizar_edital_arquivos_json(
                                edital_id,
                                arquivos_json
                            )

                    except Exception as e:
                        utils.logger.error(
                            f"Erro ao processar edital FAPITEC: {e}"
                        )

            finally:
                browser.close()

        utils.print_success("Scraping FAPITEC finalizado.")


if __name__ == "__main__":
    FapitecProcessor().executar()
