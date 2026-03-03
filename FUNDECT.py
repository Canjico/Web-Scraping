# FUNDECT.py

import os
import shutil
from urllib.parse import urljoin, urlparse
from playwright.sync_api import sync_playwright

from . import utils
from .IA import ia_client

# ==============================================
# CONFIGURAÇÃO
# ==============================================
BASE_URL = "https://www.fundect.ms.gov.br/category/chamadas-abertas/"
BASE_DOMAIN = "https://www.fundect.ms.gov.br"

PROJETO_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = os.path.join(PROJETO_DIR, "temp_downloads_fundect")

INSTITUICAO = "FUNDECT"
ICONE_PADRAO = "/icons/fundect.png"


class FundectProcessor:

    def __init__(self):
        self.session = utils.get_http_session()
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

        utils.print_section("INICIANDO SCRAPING: FUNDECT")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            page = browser.new_page()

            try:
                utils.page_goto_humano(page, BASE_URL, timeout=60000)
                utils.fechar_cookies(page)

                urls_editais = list({
                    urljoin(BASE_DOMAIN, a.get_attribute("href"))
                    for a in page.locator(".card a, .post a").all()
                    if a.get_attribute("href")
                })

                utils.logger.info(f"{len(urls_editais)} editais encontrados.")

                for url in urls_editais:
                    utils.logger.info(f"\n>>> Processando edital: {url}")

                    try:
                        utils.page_goto_humano(page, url, timeout=60000)

                        texto_pagina = page.inner_text("body")
                        textos_analise = [texto_pagina] if texto_pagina else []
                        arquivos_temp = []

                        # =========================
                        # PDFs
                        # =========================
                        links_pdf = [
                            urljoin(url, a.get_attribute("href"))
                            for a in page.locator("article a[href$='.pdf'], .entry-content a[href$='.pdf']").all()
                            if a.get_attribute("href")
                        ]

                        for link_pdf in links_pdf:
                            nome_pdf = os.path.basename(urlparse(link_pdf).path) or "documento.pdf"
                            caminho_pdf = os.path.join(TEMP_DIR, nome_pdf)

                            try:
                                resp = self.session.get(link_pdf, timeout=60, verify=False)
                                if resp.status_code != 200:
                                    continue

                                with open(caminho_pdf, "wb") as f:
                                    f.write(resp.content)

                                texto_pdf = utils.extrair_texto_pdf(caminho_pdf)
                                if texto_pdf and len(texto_pdf) > 200:
                                    textos_analise.append(
                                        f"Arquivo: {nome_pdf}\n{texto_pdf}"
                                    )

                                arquivos_temp.append({
                                    "path": caminho_pdf,
                                    "url": link_pdf
                                })

                            except Exception as e:
                                utils.logger.warning(f"Erro ao baixar PDF: {e}")

                        if not textos_analise:
                            utils.logger.warning("Nenhum texto válido para análise. Pulando.")
                            continue

                        # =========================
                        # IA – CHAMADA ÚNICA
                        # =========================
                        utils.logger.info("Enviando edital para análise da IA...")
                        resultado_ia = ia_client.analisar_edital_completo(
                            textos=textos_analise,
                            contexto="FUNDECT – Chamadas Abertas"
                        )

                        if not resultado_ia.get("aprovado"):
                            utils.logger.warning(
                                f"⛔ REPROVADO pela IA: {resultado_ia.get('motivo_aprovacao')}"
                            )
                            continue

                        # =========================
                        # APROVADO
                        # =========================
                        utils.print_success(f"✅ APROVADO: {resultado_ia.get('titulo')}")
                        utils.logger.info(f"Motivo: {resultado_ia.get('motivo_aprovacao')}")

                        resultado_ia["icone_instituicao"] = ICONE_PADRAO

                        titulo_final = resultado_ia.get("titulo") or "FUNDECT – Chamada"
                        edital_id = utils.obter_ou_criar_edital(titulo_final, INSTITUICAO)

                        utils.atualizar_edital_ia(edital_id, resultado_ia)

                        # =========================
                        # VINCULAR ARQUIVOS (Refatorado)
                        # =========================
                        
                        # Página principal
                        utils.salvar_arquivo_no_edital(
                            edital_id=edital_id,
                            url_arquivo=url,
                            tipo="Página Web"
                        )

                        arquivos_json = []

                        # PDFs
                        for item in arquivos_temp:
                            utils.salvar_arquivo_no_edital(
                                edital_id=edital_id,
                                url_arquivo=item["url"],
                                caminho_local=item["path"],
                                tipo="Edital/Anexo"
                            )

                            arquivos_json.append({
                                "tipo": "Documento",
                                "url": item["url"],
                                "nome": os.path.basename(item["path"])
                            })

                            if os.path.exists(item["path"]):
                                os.remove(item["path"])

                        # mantém JSON consolidado igual FAPEG (opcional, mas compatível)
                        utils.atualizar_edital_arquivos_json(edital_id, arquivos_json)

                    except Exception as e:
                        utils.logger.error(f"Erro ao processar edital FUNDECT: {e}")

            except Exception as e:
                utils.logger.error(f"Erro fatal FUNDECT: {e}")

            finally:
                browser.close()

        self._reset_temp_dir()
        utils.print_success("Scraping FUNDECT finalizado com sucesso.")


if __name__ == "__main__":
    FundectProcessor().executar()
