# FACEPE.py

import os
import shutil
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from . import utils
from .IA import ia_client


# ==============================================
# CONFIGURAÇÃO
# ==============================================
PROJETO_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = os.path.join(PROJETO_DIR, "temp_downloads_facepe")

BASE_URL = "https://www.facepe.br/editais/todos/?c=aberto"
BASE_DOMAIN = "https://www.facepe.br"

INSTITUICAO = "FACEPE"
ICONE_PADRAO = "/icons/facepe.png"

EXTENSOES_VALIDAS = (".pdf",)


class FacepeProcessor:

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
    # DOWNLOAD
    # ==================================================
    def _baixar_arquivo(self, url: str) -> str | None:
        nome = os.path.basename(urlparse(url).path)
        caminho = os.path.join(TEMP_DIR, nome)

        try:
            resp = self.session.get(url, timeout=60, verify=False)
            if resp.status_code != 200:
                return None

            with open(caminho, "wb") as f:
                f.write(resp.content)

            return caminho
        except Exception as e:
            utils.logger.error(f"Erro ao baixar arquivo: {e}")
            return None

    # ==================================================
    # EXECUÇÃO PRINCIPAL
    # ==================================================
    def executar(self):

        utils.print_section("INICIANDO SCRAPING: FACEPE")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            page = browser.new_page()

            try:
                utils.page_goto_humano(page, BASE_URL, timeout=60000)
                page.wait_for_selector("a.avia-button", timeout=15000)

                soup = BeautifulSoup(page.content(), "html.parser")

                links = [
                    urljoin(BASE_DOMAIN, a["href"])
                    for a in soup.find_all("a", class_="avia-button", href=True)
                    if a["href"].lower().endswith(EXTENSOES_VALIDAS)
                ]

                utils.logger.info(f"{len(links)} editais encontrados.")

                for url in links:
                    utils.logger.info(f"\n>>> Processando edital: {url}")

                    # 🔹 BLOQUEIO: já existe no banco?
                    if utils.verificar_se_arquivo_existe_no_banco("", url):
                        utils.logger.info("Arquivo já existente no banco. Pulando.")
                        continue

                    caminho = self._baixar_arquivo(url)
                    if not caminho:
                        continue

                    textos_extraidos = []

                    texto = utils.extrair_texto_generico(caminho)

                    # 🔹 FALLBACK OCR (igual FINEP)
                    if not texto or len(texto) < 300:
                        utils.logger.warning("Texto insuficiente. Ativando OCR...")
                        texto = utils.extrair_texto_ocr(caminho)

                    if texto and len(texto) > 300:
                        textos_extraidos.append(texto)
                    else:
                        utils.logger.warning("Nenhum texto válido após OCR.")
                        os.remove(caminho)
                        continue

                    # 1️⃣ IA decide (CHAMADA ÚNICA)
                    utils.logger.info("Enviando edital para análise da IA...")
                    resultado_ia = ia_client.analisar_edital_completo(
                        textos=textos_extraidos,
                        contexto="FACEPE - Editais Abertos"
                    )

                    if not resultado_ia.get("aprovado"):
                        utils.logger.warning(
                            f"⛔ REPROVADO pela IA: {resultado_ia.get('motivo_aprovacao')}"
                        )
                        os.remove(caminho)
                        continue

                    # 2️⃣ APROVADO
                    utils.print_success(f"✅ APROVADO: {resultado_ia.get('titulo')}")
                    utils.logger.info(f"Motivo: {resultado_ia.get('motivo_aprovacao')}")

                    resultado_ia["icone_instituicao"] = ICONE_PADRAO

                    # 3️⃣ Cria ou recupera edital
                    titulo_final = resultado_ia.get("titulo") or "FACEPE"
                    edital_id = utils.obter_ou_criar_edital(titulo_final, INSTITUICAO, url_edital=url)

                    # 4️⃣ Atualiza edital com dados da IA
                    utils.atualizar_edital_ia(edital_id, resultado_ia)

                    # 5️⃣ Vincula arquivo
                    utils.salvar_arquivo_no_edital(
                        edital_id=edital_id,
                        url_arquivo=url,
                        caminho_local=caminho,
                        tipo="Edital"
                    )

                    # 6️⃣ Atualiza JSON de arquivos
                    utils.atualizar_edital_arquivos_json(
                        edital_id,
                        arquivos_json=[
                            {
                                "tipo": "Edital",
                                "url": url,
                                "nome": os.path.basename(caminho)
                            }
                        ]
                    )

                    os.remove(caminho)

            except Exception as e:
                utils.logger.error(f"Erro fatal no scraping FACEPE: {e}")

            finally:
                browser.close()

        self._preparar_diretorio_temporario()
        utils.print_success("Scraping da FACEPE finalizado.")


if __name__ == "__main__":
    FacepeProcessor().executar()
