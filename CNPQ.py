# CNPQ.py

import os
import shutil
from playwright.sync_api import sync_playwright
from urllib.parse import urljoin, urlparse

from . import utils
from .IA import ia_client


# ==============================================
# CONFIGURAÇÃO
# ==============================================
PROJETO_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = os.path.join(PROJETO_DIR, "temp_downloads_cnpq")

URL_FONTE = "http://memoria2.cnpq.br/web/guest/chamadas-publicas#void"
BASE_DOMAIN = "http://memoria2.cnpq.br"

INSTITUICAO = "CNPq"
ICONE_PADRAO = "/icons/cnpq.png"


class CnpqProcessor:

    def __init__(self):
        self.session = utils.get_http_session()
        self._preparar_diretorio_temporario()

    # ==================================================
    # SETUP
    # ==================================================
    def _preparar_diretorio_temporario(self):
        if os.path.exists(TEMP_DIR):
            shutil.rmtree(TEMP_DIR)
        os.makedirs(TEMP_DIR, exist_ok=True)

    # ==================================================
    # EXECUÇÃO PRINCIPAL
    # ==================================================
    def executar(self):

        utils.print_section("INICIANDO SCRAPING: CNPq")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            page = browser.new_page(accept_downloads=True)

            try:
                utils.page_goto_humano(page, URL_FONTE, timeout=180000)
                page.wait_for_selector("div.links-normas a.btn", timeout=20000)

                botoes = page.query_selector_all("div.links-normas a.btn")
                utils.logger.info(f"{len(botoes)} chamadas encontradas.")

                for btn in botoes:
                    href = btn.get_attribute("href")
                    titulo_btn = btn.inner_text().strip()

                    if not href:
                        continue

                    url_completa = urljoin(BASE_DOMAIN, href)

                    # 🔒 BLINDAGEM: já existe no banco?
                    if utils.verificar_se_arquivo_existe_no_banco(
                        caminho_arquivo="",
                        url_arquivo=url_completa
                    ):
                        utils.logger.info("Já existe no banco. Pulando.")
                        continue

                    utils.logger.info(f"\n>>> Processando chamada: {titulo_btn}")

                    try:
                        # 1️⃣ Download
                        with page.expect_download(timeout=60000) as dl_info:
                            btn.click()

                        download = dl_info.value
                        caminho_pdf = os.path.join(TEMP_DIR, download.suggested_filename)
                        download.save_as(caminho_pdf)

                        # 2️⃣ Extração de texto
                        textos_extraidos = []

                        texto = utils.extrair_texto_generico(caminho_pdf)

                        if not texto or len(texto) < 300:
                            utils.logger.warning("Texto fraco. Ativando OCR...")
                            texto = utils.extrair_texto_ocr(caminho_pdf)

                        if texto and len(texto) > 300:
                            textos_extraidos.append(
                                f"Arquivo: {os.path.basename(caminho_pdf)}\n{texto}"
                            )

                        if not textos_extraidos:
                            utils.logger.warning("Nenhum texto válido após OCR.")
                            continue

                        # 3️⃣ IA
                        utils.logger.info("Enviando para análise da IA...")
                        resultado_ia = ia_client.analisar_edital_completo(
                            textos=textos_extraidos,
                            contexto="CNPq - Chamada Pública"
                        )

                        if not resultado_ia.get("aprovado"):
                            utils.logger.warning(
                                f"⛔ REPROVADO pela IA: {resultado_ia.get('motivo_aprovacao')}"
                            )
                            continue

                        # 4️⃣ APROVADO
                        utils.print_success(f"✅ APROVADO: {resultado_ia.get('titulo')}")
                        utils.logger.info(f"Motivo: {resultado_ia.get('motivo_aprovacao')}")

                        resultado_ia["icone_instituicao"] = ICONE_PADRAO

                        # 5️⃣ Edital
                        titulo_final = resultado_ia.get("titulo") or titulo_btn
                        edital_id = utils.obter_ou_criar_edital(
                    titulo_final,
                    INSTITUICAO,
                    url_edital=url_completa
                )

                        # 6️⃣ Atualiza IA
                        utils.atualizar_edital_ia(edital_id, resultado_ia)

                        # 7️⃣ Arquivo
                        utils.salvar_arquivo_no_edital(
                            edital_id=edital_id,
                            url_arquivo=url_completa,
                            caminho_local=caminho_pdf,
                            tipo="Edital"
                        )

                        # 8️⃣ JSON consolidado
                        utils.atualizar_edital_arquivos_json(
                            edital_id,
                            arquivos_json=[
                                {
                                    "tipo": "Edital",
                                    "url": url_completa,
                                    "nome": os.path.basename(caminho_pdf)
                                }
                            ]
                        )

                    except Exception as e:
                        utils.logger.error(f"Erro ao processar chamada: {e}")

                    finally:
                        if os.path.exists(caminho_pdf):
                            os.remove(caminho_pdf)

            except Exception as e:
                utils.logger.error(f"Erro fatal no scraping do CNPq: {e}")

            finally:
                browser.close()

        self._preparar_diretorio_temporario()
        utils.print_success("Scraping do CNPq finalizado.")


if __name__ == "__main__":
    CnpqProcessor().executar()
