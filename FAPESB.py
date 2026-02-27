# FAPESB.py (ajustado)

from playwright.sync_api import sync_playwright
from urllib.parse import urljoin

from . import utils
from .IA import ia_client

BASE_URL = "https://www.fapesb.ba.gov.br/category/edital/"
BASE_DOMAIN = "https://www.fapesb.ba.gov.br"

INSTITUICAO = "FAPESB"
ICONE_PADRAO = "/icons/fapesb.png"
EXTENSOES_VALIDAS = (".pdf", ".doc", ".docx")


class FapesbProcessor:

    def executar(self):
        utils.print_section("INICIANDO SCRAPING: FAPESB")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            try:
                utils.page_goto_humano(page, BASE_URL, timeout=120000)

                # 1️⃣ Coletar todos os anos disponíveis dinamicamente
                anos_disponiveis = [
                    b.get_attribute("value")
                    for b in page.locator("button[onclick^='filtraAno']").all()
                    if b.get_attribute("value")
                ]

                utils.logger.info(f"Anos encontrados: {anos_disponiveis}")

                # 2️⃣ Iterar sobre cada ano e coletar links de editais
                todos_links_editais = []

                for ano in anos_disponiveis:
                    utils.logger.info(f"Filtrando ano: {ano}")
                    # Clica no botão do ano
                    page.locator(f"button[value='{ano}']").click()
                    page.wait_for_timeout(2000)  # espera carregamento dinâmico (ajuste se necessário)

                    # Captura links do ano
                    links_editais = [
                        a.get_attribute("href")
                        for a in page.locator("div.edital-title > h3 > p > a").all()
                        if a.get_attribute("href")
                    ]
                    utils.logger.info(f"{len(links_editais)} editais encontrados para {ano}")

                    todos_links_editais.extend(links_editais)

                # Remove duplicatas mantendo a ordem
                todos_links_editais = list(dict.fromkeys(todos_links_editais))
                utils.logger.info(f"Total de editais para processar: {len(todos_links_editais)}")

                # 3️⃣ Processa cada edital como você já fazia
                for url_edital in todos_links_editais:
                    utils.logger.info(f"\n>>> Processando edital: {url_edital}")
                    try:
                        utils.page_goto_humano(page, url_edital, timeout=60000)
                        texto_pagina = page.inner_text("body")

                        links_anexos = []
                        for a in page.locator("div.entry-content a[href], a.link-pdf[href]").all():
                            href = a.get_attribute("href")
                            if not href:
                                continue
                            if href.lower().endswith(EXTENSOES_VALIDAS):
                                links_anexos.append(
                                    href if href.startswith("http") else urljoin(BASE_DOMAIN, href)
                                )
                        links_anexos = list(dict.fromkeys(links_anexos))

                        textos_ia = []
                        if texto_pagina and len(texto_pagina) > 500:
                            textos_ia.append(texto_pagina)

                        for link in links_anexos:
                            try:
                                texto_anexo = utils.extrair_texto_por_url(link)
                                if texto_anexo and len(texto_anexo) > 500:
                                    textos_ia.append(f"Arquivo: {link.split('/')[-1]}\n{texto_anexo}")
                            except Exception as e:
                                utils.logger.warning(f"Falha ao extrair anexo {link}: {e}")

                        if not textos_ia:
                            utils.logger.warning("Nenhum texto válido para IA. Pulando.")
                            continue

                        texto_completo = "\n\n--- FIM DE ARQUIVO ---\n\n".join(textos_ia)
                        resultado_ia = ia_client.analisar_smart(
                            texto_completo=texto_completo,
                            contexto="Editais FAPESB"
                        )

                        if not resultado_ia.get("aprovado"):
                            utils.logger.warning(f"⛔ REPROVADO pela IA: {resultado_ia.get('motivo_aprovacao')}")
                            continue

                        utils.print_success(
                            f"✅ APROVADO: {resultado_ia.get('titulo')} | Motivo: {resultado_ia.get('motivo_aprovacao')}"
                        )

                        resultado_ia["icone_instituicao"] = ICONE_PADRAO
                        titulo = resultado_ia.get("titulo") or "Edital FAPESB"

                        edital_id = utils.obter_ou_criar_edital(
                            titulo=titulo,
                            fonte=INSTITUICAO,
                            url_edital=url_edital
                        )

                        utils.atualizar_edital_ia(edital_id, resultado_ia)

                        for link in links_anexos:
                            utils.salvar_arquivo_no_edital(
                                edital_id=edital_id,
                                url_arquivo=link,
                                tipo="Documento",
                                evitar_duplicado=True
                            )

                    except Exception as e:
                        utils.logger.error(f"Erro ao processar edital FAPESB: {e}")

            finally:
                browser.close()

        utils.print_success("Scraping FAPESB finalizado.")


if __name__ == "__main__":
    FapesbProcessor().executar()
