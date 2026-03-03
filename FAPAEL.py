# FAPEAL.py

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
TEMP_DIR = os.path.join(PROJETO_DIR, "temp_downloads_fapeal")

BASE_URL = "https://www.fapeal.br/category/editais/chamadas-abertas/"
BASE_DOMAIN = "https://www.fapeal.br"

INSTITUICAO = "FAPEAL"
ICONE_PADRAO = "/icons/fapeal.png"

EXTENSOES_VALIDAS = (".pdf", ".doc", ".docx", ".zip", ".xls", ".xlsx")


class FapealProcessor:

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

    def _baixar_arquivo(self, url: str) -> str | None:
        # 🔹 Evita baixar se já existe no banco
        if utils.verificar_se_arquivo_existe_no_banco("", url):
            utils.logger.info(f"Arquivo já existe no banco, pulando download: {url}")
            return None

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
            utils.logger.error(f"Erro ao baixar {url}: {e}")
            return None

    # ==================================================
    # EXECUÇÃO PRINCIPAL
    # ==================================================
    def executar(self):

        utils.print_section("INICIANDO SCRAPING: FAPEAL")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            page = browser.new_page()

            try:
                utils.page_goto_humano(page, BASE_URL, timeout=60000)
                page.wait_for_selector("h2.entry-title a", timeout=20000)

                posts = page.locator("h2.entry-title a").evaluate_all(
                    "els => els.map(el => el.href)"
                )

                utils.logger.info(f"{len(posts)} posts encontrados.")

                for post_url in posts:
                    utils.logger.info(f"\n>>> Processando post: {post_url}")
                    utils.page_goto_humano(page, post_url, timeout=60000)

                    texto_pagina = page.inner_text("body")

                    links = page.locator("a").evaluate_all("""
                        els => els
                            .map(el => el.href)
                            .filter(h => h && %s.some(ext => h.toLowerCase().endsWith(ext)))
                    """ % list(EXTENSOES_VALIDAS))

                    if not links:
                        utils.logger.warning("Nenhum arquivo encontrado no post.")
                        continue

                    arquivos_temp = []
                    textos_extraidos = []

                    # 1️⃣ Download + extração + OCR
                    for link in links:
                        url_completa = urljoin(BASE_DOMAIN, link)
                        caminho = self._baixar_arquivo(url_completa)

                        if not caminho:
                            continue

                        arquivos_temp.append({"path": caminho, "url": url_completa})

                        texto = utils.extrair_texto_generico(caminho)

                        # 🔹 OCR fallback (igual FINEP)
                        if not texto or len(texto) < 300:
                            utils.logger.warning(
                                f"Texto insuficiente em {os.path.basename(caminho)}. Ativando OCR..."
                            )
                            texto = utils.extrair_texto_ocr(caminho)

                        if texto and len(texto) > 300:
                            textos_extraidos.append(
                                f"Arquivo: {os.path.basename(caminho)}\n{texto}"
                            )

                    if texto_pagina and len(texto_pagina) > 300:
                        textos_extraidos.insert(0, f"Página Web:\n{texto_pagina}")

                    if not textos_extraidos:
                        utils.logger.warning("Nenhum texto válido para análise.")
                        self._limpar_temporarios(arquivos_temp)
                        continue

                    # 2️⃣ IA decide
                    utils.logger.info("Enviando bloco para análise da IA...")
                    resultado_ia = ia_client.analisar_edital_completo(
                        textos=textos_extraidos,
                        contexto="FAPEAL - Chamadas Abertas"
                    )

                    if not resultado_ia.get("aprovado"):
                        utils.logger.warning(
                            f"⛔ REPROVADO pela IA: {resultado_ia.get('motivo_aprovacao')}"
                        )
                        self._limpar_temporarios(arquivos_temp)
                        continue

                    # 3️⃣ APROVADO
                    utils.print_success(f"✅ APROVADO: {resultado_ia.get('titulo')}")
                    utils.logger.info(f"Motivo: {resultado_ia.get('motivo_aprovacao')}")

                    resultado_ia["icone_instituicao"] = ICONE_PADRAO

                    titulo_final = resultado_ia.get("titulo") or "Edital FAPEAL"
                    edital_id = utils.obter_ou_criar_edital(titulo_final, INSTITUICAO, url_edital=post_url)

                    # 4️⃣ Atualiza edital
                    utils.atualizar_edital_ia(edital_id, resultado_ia)

                    arquivos_json = []

                    # 5️⃣ Vincula arquivos
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

                        try:
                            os.remove(item["path"])
                        except:
                            pass

                    utils.atualizar_edital_arquivos_json(edital_id, arquivos_json)

            except Exception as e:
                utils.logger.error(f"Erro fatal no scraping FAPEAL: {e}")

            finally:
                browser.close()

        self._preparar_diretorio_temporario()
        utils.print_success("Scraping da FAPEAL finalizado.")

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
    FapealProcessor().executar()
