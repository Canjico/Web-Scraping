# FAPT.py

from playwright.sync_api import sync_playwright
from urllib.parse import urljoin

from . import utils
from .IA import ia_client


# ==============================================
# CONFIGURAÇÃO
# ==============================================
URL_LISTA = "https://www.to.gov.br/fapt/01-editais-abertos/5sy36y0lf49g"
BASE_DOMAIN = "https://www.to.gov.br"

INSTITUICAO = "FAPT"
ICONE_PADRAO = "/icons/fapt.png"

EXTENSOES_VALIDAS = (".pdf", ".doc", ".docx")


class FaptProcessor:

    def executar(self):

        utils.print_section("INICIANDO SCRAPING: FAPT")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            try:
                utils.page_goto_humano(page, URL_LISTA, timeout=120000)

                links_editais = []

                for a in page.locator("ul li a[href]").all():
                    href = a.get_attribute("href")
                    if not href:
                        continue

                    if "edital" in href.lower():
                        links_editais.append(
                            href if href.startswith("http") else urljoin(BASE_DOMAIN, href)
                        )

                links_editais = list(set(links_editais))
                utils.logger.info(f"{len(links_editais)} páginas de editais encontradas.")

                for url_edital in links_editais:
                    utils.logger.info(f"\n>>> Processando edital: {url_edital}")

                    try:
                        utils.page_goto_humano(page, url_edital, timeout=60000)
                        texto_pagina = page.inner_text("body")

                        links_anexos = []

                        for a in page.locator("a[href]").all():
                            href = a.get_attribute("href")
                            if not href:
                                continue

                            if href.lower().endswith(EXTENSOES_VALIDAS) or "download" in href.lower():
                                links_anexos.append(
                                    href if href.startswith("http") else urljoin(BASE_DOMAIN, href)
                                )

                        textos_ia = []

                        if texto_pagina and len(texto_pagina) > 500:
                            textos_ia.append(texto_pagina[:8000])

                        for link in links_anexos:
                            try:
                                texto_anexo = utils.extrair_texto_por_url(link)
                                if texto_anexo and len(texto_anexo) > 500:
                                    textos_ia.append(
                                        f"Arquivo: {link.split('/')[-1]}\n{texto_anexo[:8000]}"
                                    )
                            except Exception as e:
                                utils.logger.warning(
                                    f"Falha ao extrair anexo {link}: {e}"
                                )

                        if not textos_ia:
                            utils.logger.warning("Nenhum texto válido para IA. Pulando.")
                            continue

                        resultado_ia = ia_client.analisar_edital_completo(
                            textos=textos_ia,
                            contexto="Editais FAPT"
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

                        titulo = resultado_ia.get("titulo") or "Edital FAPT"
                        edital_id = utils.obter_ou_criar_edital(
                            titulo,
                            INSTITUICAO
                        )

                        utils.atualizar_edital_ia(
                            edital_id,
                            resultado_ia
                        )

                        # ==================================================
                        # SALVA ARQUIVOS (IGUAL FAPEG - DIRETO NO SQL)
                        # ==================================================
                        arquivos_json = []

                        for link in links_anexos:
                            utils.salvar_arquivo_no_edital(
                                edital_id=edital_id,
                                url_arquivo=link,
                                tipo="Documento"
                            )

                            # mantém JSON apenas como consolidado (igual FAPEG)
                            arquivos_json.append({
                                "tipo": "Documento",
                                "url": link,
                                "nome": link.split("/")[-1]
                            })

                        # Atualiza JSON consolidado (opcional / compatibilidade)
                        if arquivos_json:
                            utils.atualizar_edital_arquivos_json(
                                edital_id,
                                arquivos_json
                            )

                    except Exception as e:
                        utils.logger.error(
                            f"Erro ao processar edital FAPT: {e}"
                        )

            finally:
                browser.close()

        utils.print_success("Scraping FAPT finalizado.")


if __name__ == "__main__":
    FaptProcessor().executar()
