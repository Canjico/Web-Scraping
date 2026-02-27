# FUNCAP.py

import os
import shutil
import time
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

from . import utils
from .IA import ia_client


# ==============================================
# CONFIGURAÇÃO
# ==============================================
IFRAME_URL = "https://montenegro.funcap.ce.gov.br/sugba/editais-site-wordpress"
BASE_DOMAIN = "https://montenegro.funcap.ce.gov.br/sugba"

INSTITUICAO = "FUNCAP"
ICONE_PADRAO = "/icons/funcap.png"

EXTENSOES_VALIDAS = (".pdf", ".doc", ".docx", ".zip")

PROJETO_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = os.path.join(PROJETO_DIR, "temp_downloads_funcap")


class FuncapProcessor:

    def __init__(self):
        self.session = utils.get_http_session()
        self._preparar_temp()

    def _preparar_temp(self):
        if os.path.exists(TEMP_DIR):
            shutil.rmtree(TEMP_DIR)
        os.makedirs(TEMP_DIR, exist_ok=True)

    def executar(self):

        utils.print_section("INICIANDO SCRAPING: FUNCAP")

        # 1️⃣ Carrega HTML via Playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            page = browser.new_page()
            utils.page_goto_humano(page, IFRAME_URL, timeout=120000)
            html = page.content()
            browser.close()

        soup = BeautifulSoup(html, "html.parser")
        tabela = soup.find("table")

        if not tabela:
            utils.logger.warning("Tabela de editais não encontrada.")
            return

        linhas = tabela.find_all("tr", style=True)
        utils.logger.info(f"{len(linhas)} linhas encontradas.")

        for tr in linhas:
            try:
                link_tag = tr.find("a", href=True)
                if not link_tag:
                    continue

                href = link_tag["href"].strip()

                if not href.lower().endswith(EXTENSOES_VALIDAS):
                    continue

                # 🔥 CORREÇÃO CRÍTICA DE URL
                # Remove ../ e normaliza para dentro do /sugba mantendo subdiretórios (ex: resultados/)
                href_limpo = href.replace("../", "").lstrip("/")
                url_arquivo = f"{BASE_DOMAIN}/{href_limpo}"
                
                # Nome do arquivo para salvar localmente
                nome_arquivo = os.path.basename(href_limpo)

                utils.logger.info(f"\n>>> Processando arquivo: {url_arquivo}")

                caminho_local = os.path.join(TEMP_DIR, nome_arquivo)

                # 2️⃣ Download do arquivo
                resp = self.session.get(url_arquivo, timeout=60, verify=False)
                if resp.status_code != 200:
                    utils.logger.warning("Falha ao baixar arquivo.")
                    continue

                with open(caminho_local, "wb") as f:
                    f.write(resp.content)

                # 3️⃣ Extração de texto
                texto = utils.extrair_texto_generico(caminho_local)

                if not texto or len(texto) < 300:
                    utils.logger.warning("Texto fraco. Ativando OCR...")
                    texto = utils.extrair_texto_ocr(caminho_local)

                if not texto or len(texto) < 300:
                    utils.logger.warning("Texto insuficiente após OCR. Pulando.")
                    os.remove(caminho_local)
                    continue

                # 4️⃣ IA decide (COM RETRY PARA ERRO 429)
                # O IA.py captura exceções e retorna um dict com erro. 
                # Verificamos o erro no retorno para aplicar o retry.
                resultado_ia = {}
                for tentativa in range(2):
                    try:
                        resultado_ia = ia_client.analisar_edital_completo(
                            textos=[texto],
                            contexto="FUNCAP - Editais"
                        )
                        
                        # Verifica se o retorno contém erro de Rate Limit (429)
                        motivo = str(resultado_ia.get("motivo_aprovacao", ""))
                        if "429" in motivo and tentativa == 0:
                            utils.logger.warning("Rate limit (429) detectado. Aguardando 40s e tentando novamente...")
                            time.sleep(40)
                            continue
                        
                        # Se chegou aqui, ou deu certo ou é outro tipo de erro/reprovação
                        break
                        
                    except Exception as e:
                        # Fallback caso IA.py mude e passe a lançar exceções
                        if "429" in str(e) and tentativa == 0:
                            utils.logger.warning("Rate limit (Exception 429) detectado. Aguardando 40s...")
                            time.sleep(40)
                        else:
                            raise e

                if not resultado_ia.get("aprovado"):
                    utils.logger.warning(
                        f"⛔ REPROVADO pela IA: {resultado_ia.get('motivo_aprovacao')}"
                    )
                    os.remove(caminho_local)
                    continue

                utils.print_success(f"✅ APROVADO: {resultado_ia.get('titulo')}")

                resultado_ia["icone_instituicao"] = ICONE_PADRAO

                # 5️⃣ Banco
                titulo = resultado_ia.get("titulo") or "Edital FUNCAP"
                edital_id = utils.obter_ou_criar_edital(titulo, INSTITUICAO)

                utils.atualizar_edital_ia(edital_id, resultado_ia)

                utils.salvar_arquivo_no_edital(
                    edital_id=edital_id,
                    url_arquivo=url_arquivo,
                    caminho_local=caminho_local,
                    tipo="Edital/Anexo"
                )

                utils.atualizar_edital_arquivos_json(
                    edital_id,
                    [{
                        "tipo": "Documento",
                        "url": url_arquivo,
                        "nome": nome_arquivo
                    }]
                )

                os.remove(caminho_local)

            except Exception as e:
                utils.logger.error(f"Erro ao processar edital FUNCAP: {e}")

        self._preparar_temp()
        utils.print_success("Scraping FUNCAP finalizado.")


if __name__ == "__main__":
    FuncapProcessor().executar()
