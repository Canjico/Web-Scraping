# ARAUCARA.py

import os
import shutil
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from playwright.sync_api import sync_playwright

from . import utils
from .IA import ia_client

import warnings
from urllib3.exceptions import InsecureRequestWarning
warnings.simplefilter("ignore", InsecureRequestWarning)

# ==============================================
# CONFIGURAÇÃO
# ==============================================
EXTENSOES_VALIDAS = (".pdf", ".docx", ".doc", ".zip", ".rar", ".xlsx", ".xls")
PROJETO_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = os.path.join(PROJETO_DIR, "temp_downloads_fappr")

URL_ALVO = "https://www.fappr.pr.gov.br/Programas-Abertos"
BASE_DOMAIN = "https://www.fappr.pr.gov.br"
HEADERS = {"User-Agent": "Mozilla/5.0"}

INSTITUICAO = "FUNDAÇÃO ARAUCÁRIA"
ICONE_PADRAO = "/icons/fundacao-araucaria.png"


class FapprProcessor:

    def __init__(self):
        self.session = utils.get_http_session()
        self._preparar_diretorio_temporario()

    def _preparar_diretorio_temporario(self):
        if os.path.exists(TEMP_DIR):
            shutil.rmtree(TEMP_DIR)
        os.makedirs(TEMP_DIR, exist_ok=True)

    # ==================================================
    # DOWNLOAD
    # ==================================================
    def _baixar_arquivo(self, url: str) -> str | None:
        ext = os.path.splitext(urlparse(url).path)[1] or ".bin"
        nome = f"temp_{utils.gerar_hash_id(url)}{ext}"
        caminho = os.path.join(TEMP_DIR, nome)

        try:
            resp = self.session.get(url, timeout=60, verify=False)
            if resp.status_code != 200:
                return None

            with open(caminho, "wb") as f:
                f.write(resp.content)

            return caminho

        except Exception as e:
            utils.logger.warning(f"Falha download {url}: {e}")
            return None

    # ==================================================
    # EXECUÇÃO
    # ==================================================
    def executar(self):

        utils.print_section("INICIANDO SCRAPING: FUNDAÇÃO ARAUCÁRIA")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(extra_http_headers=HEADERS)

            try:
                utils.page_goto_humano(page, URL_ALVO, timeout=60000)
                page.wait_for_selector("h3", timeout=15000)

                soup = BeautifulSoup(page.content(), "html.parser")
                blocos = soup.find_all("h3")

                utils.logger.info(f"{len(blocos)} blocos encontrados.")

                for h3 in blocos:
                    titulo_bloco = h3.get_text(strip=True)
                    ul = h3.find_next("ul")
                    if not ul:
                        continue

                    links = [
                        urljoin(BASE_DOMAIN, a["href"])
                        for a in ul.find_all("a", href=True)
                        if a["href"].lower().endswith(EXTENSOES_VALIDAS)
                    ]

                    if not links:
                        continue

                    utils.logger.info(f"\n>>> Bloco: {titulo_bloco} ({len(links)} arquivos)")

                    arquivos_temp = []
                    textos_extraidos = []

                    # ======================================
                    # BAIXA + EXTRAÇÃO + OCR
                    # ======================================
                    for url_arquivo in links:

                        # 🔒 BLOQUEIO: já existe no banco?
                        if utils.verificar_se_arquivo_existe_no_banco("", url_arquivo):
                            utils.logger.info(f"Arquivo já existe no banco, ignorado: {url_arquivo}")
                            continue

                        caminho = self._baixar_arquivo(url_arquivo)
                        if not caminho:
                            continue

                        arquivos_temp.append({"path": caminho, "url": url_arquivo})

                        texto = utils.extrair_texto_generico(caminho)

                        # 🔹 OCR fallback (igual FINEP)
                        if not texto or len(texto) < 300:
                            utils.logger.warning(
                                f"OCR ativado para {os.path.basename(caminho)}"
                            )
                            texto = utils.extrair_texto_ocr(caminho)

                        if texto and len(texto) > 300:
                            textos_extraidos.append(
                                f"Arquivo: {os.path.basename(caminho)}\n{texto}"
                            )

                    if not textos_extraidos:
                        utils.logger.warning("Nenhum texto válido extraído. Pulando bloco.")
                        self._limpar_temporarios(arquivos_temp)
                        continue

                    # ======================================
                    # IA
                    # ======================================
                    utils.logger.info("Enviando para análise da IA...")
                    resultado_ia = ia_client.analisar_edital_completo(
                        textos_extraidos,
                        contexto=titulo_bloco
                    )

                    if not resultado_ia.get("aprovado"):
                        utils.logger.warning(
                            f"⛔ REPROVADO: {resultado_ia.get('motivo_aprovacao')}"
                        )
                        self._limpar_temporarios(arquivos_temp)
                        continue

                    # ======================================
                    # SQL
                    # ======================================
                    utils.print_success(f"✅ APROVADO: {resultado_ia.get('titulo')}")
                    resultado_ia["icone_instituicao"] = ICONE_PADRAO

                    titulo_final = resultado_ia.get("titulo") or titulo_bloco
                    edital_id = utils.obter_ou_criar_edital(titulo_final, INSTITUICAO, url_edital=f"{URL_ALVO}|{titulo_bloco}")

                    utils.atualizar_edital_ia(edital_id, resultado_ia)

                    arquivos_json = []

                    for arq in arquivos_temp:
                        utils.salvar_arquivo_no_edital(
                            edital_id=edital_id,
                            url_arquivo=arq["url"],
                            caminho_local=arq["path"],
                            tipo="Edital/Anexo"
                        )

                        arquivos_json.append({
                            "tipo": "Documento",
                            "url": arq["url"],
                            "nome": os.path.basename(arq["path"])
                        })

                        if os.path.exists(arq["path"]):
                            os.remove(arq["path"])

                    utils.atualizar_edital_arquivos_json(edital_id, arquivos_json)

            except Exception as e:
                utils.logger.error(f"Erro fatal no scraping Araucária: {e}")

            finally:
                browser.close()

        self._preparar_diretorio_temporario()
        utils.print_success("Scraping da Fundação Araucária finalizado.")

    # ==================================================
    # LIMPEZA
    # ==================================================
    def _limpar_temporarios(self, arquivos):
        for arq in arquivos:
            try:
                if os.path.exists(arq["path"]):
                    os.remove(arq["path"])
            except:
                pass


if __name__ == "__main__":
    FapprProcessor().executar()
