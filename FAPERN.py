# FAPERN.py (refatorado para salvar no SQL e processar em partes grandes)

import os
import shutil
from urllib.parse import urljoin, urlparse
from playwright.sync_api import sync_playwright

from . import utils
from .IA import ia_client

# ==============================================
# CONFIGURAÇÃO
# ==============================================
PROJETO_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = os.path.join(PROJETO_DIR, "temp_downloads_fapern")

BASE_URL = "http://www.fapern.rn.gov.br/Conteudo.asp?TRAN=ITEM&TARG=15517&ACT="
INSTITUICAO = "FAPERN"
ICONE_PADRAO = "/icons/fapern.png"

EXTENSOES_VALIDAS = (".pdf", ".doc", ".docx")

# ==================================================
# Função para enviar texto em blocos grandes para a IA
# ==================================================
def analisar_texto_em_partes(texto_completo, contexto):
    # Delegado para o modo Smart (50k por trecho)
    return ia_client.analisar_smart(texto_completo, contexto=contexto, tamanho_chunk=50000)

# ==================================================
# Classe principal
# ==================================================
class FapernProcessor:

    def __init__(self):
        self.session = utils.get_http_session()
        self._preparar_diretorio_temporario()

    def _preparar_diretorio_temporario(self):
        if os.path.exists(TEMP_DIR):
            shutil.rmtree(TEMP_DIR)
        os.makedirs(TEMP_DIR, exist_ok=True)

    def _baixar_arquivo(self, url: str) -> str | None:
        nome = os.path.basename(urlparse(url).path) or "documento.bin"
        caminho = os.path.join(TEMP_DIR, nome)

        try:
            resp = self.session.get(url, timeout=60, verify=False)
            if resp.status_code != 200:
                return None

            with open(caminho, "wb") as f:
                f.write(resp.content)
            return caminho

        except Exception as e:
            utils.logger.error(f"Erro ao baixar arquivo {nome}: {e}")
            return None

    # ==================================================
    # EXECUÇÃO PRINCIPAL
    # ==================================================
    def executar(self):

        utils.print_section("INICIANDO SCRAPING: FAPERN")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            try:
                utils.page_goto_humano(page, BASE_URL, timeout=60000)
                page.wait_for_selector("div.section a[href]", timeout=30000)

                links = page.locator("div.section a[href]").all()
                utils.logger.info(f"{len(links)} links encontrados.")

                for link in links:
                    href = link.get_attribute("href")
                    if not href:
                        continue

                    url = urljoin(BASE_URL, href)
                    if not url.lower().endswith(EXTENSOES_VALIDAS):
                        continue

                    utils.logger.info(f"\n>>> Processando arquivo: {url}")

                    caminho = self._baixar_arquivo(url)
                    if not caminho:
                        continue

                    texto = utils.extrair_texto_generico(caminho)
                    if not texto or len(texto) < 500:
                        utils.logger.warning("Texto insuficiente. Pulando.")
                        os.remove(caminho)
                        continue

                    # IA decide em partes grandes
                    resultado_ia = analisar_texto_em_partes(
                        texto_completo=texto,
                        contexto="Editais FAPERN"
                    )

                    if not resultado_ia.get("aprovado"):
                        utils.logger.warning(
                            f"⛔ REPROVADO pela IA: {resultado_ia.get('motivo_aprovacao')}"
                        )
                        os.remove(caminho)
                        continue

                    # APROVADO
                    utils.print_success(f"✅ APROVADO: {resultado_ia.get('titulo')}")
                    utils.logger.info(f"Motivo: {resultado_ia.get('motivo_aprovacao')}")

                    resultado_ia["icone_instituicao"] = ICONE_PADRAO
                    titulo_final = resultado_ia.get("titulo") or "Edital FAPERN"

                    # ==================================================
                    # Aqui garantimos que se o edital já existir no SQL, só atualizamos
                    # ==================================================
                    edital_id = utils.obter_ou_criar_edital(titulo_final, INSTITUICAO, url_edital=url)

                    # Atualiza dados da IA
                    utils.atualizar_edital_ia(edital_id, resultado_ia)

                    # Vincula arquivos (link principal ou anexos)
                    arquivos_para_vincular = [{"url": url, "path": caminho}]

                    for arq in arquivos_para_vincular:
                        utils.salvar_arquivo_no_edital(
                            edital_id=edital_id,
                            url_arquivo=arq["url"],
                            caminho_local=arq["path"],
                            tipo="Edital/Anexo",
                            evitar_duplicado=True
                        )

                    # Limpa arquivos temporários
                    if os.path.exists(caminho):
                        os.remove(caminho)

            except Exception as e:
                utils.logger.error(f"Erro fatal no scraping FAPERN: {e}")

            finally:
                browser.close()

        self._preparar_diretorio_temporario()
        utils.print_success("Scraping FAPERN finalizado.")


if __name__ == "__main__":
    FapernProcessor().executar()
