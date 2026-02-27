# CONFAP.py

import os
import shutil
from urllib.parse import urljoin, urlparse
from playwright.sync_api import sync_playwright

from . import utils
from .IA import ia_client

# ==============================================
# CONFIGURAÇÃO
# ==============================================
BASE_URL = "https://confap.org.br/pt/editais/status=em-andamento"
BASE_DOMAIN = "https://confap.org.br"

PROJETO_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = os.path.join(PROJETO_DIR, "temp_downloads_confap")

INSTITUICAO = "CONFAP"
ICONE_PADRAO = "/icons/confap.png"


class ConfapProcessor:

    def __init__(self):
        self.session = utils.get_http_session()
        self._reset_temp_dir()

    # ==================================================
    # SETUP
    # ==================================================
    def _reset_temp_dir(self):
        if os.path.exists(TEMP_DIR):
            shutil.rmtree(TEMP_DIR)
        os.makedirs(TEMP_DIR, exist_ok=True)

    # ==================================================
    # EXECUÇÃO PRINCIPAL
    # ==================================================
    def executar(self):

        utils.print_section("INICIANDO SCRAPING: CONFAP")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            page = browser.new_page()

            try:
                utils.page_goto_humano(page, BASE_URL, timeout=60000)
                page.wait_for_selector("a.btn.btn-grayish", timeout=20000)

                urls_editais = [
                    urljoin(BASE_DOMAIN, el.get_attribute("href"))
                    for el in page.locator("a.btn.btn-grayish").all()
                    if el.get_attribute("href")
                ]

                utils.logger.info(f"{len(urls_editais)} editais encontrados.")

                for url in urls_editais:
                    self._processar_edital(page, url)

            except Exception as e:
                utils.logger.error(f"Erro fatal CONFAP: {e}")

            finally:
                browser.close()

        self._reset_temp_dir()
        utils.print_success("Scraping do CONFAP finalizado.")

    # ==================================================
    # PROCESSAMENTO DE UM EDITAL
    # ==================================================
    def _processar_edital(self, page, url):

        utils.logger.info(f">>> Processando edital: {url}")

        try:
            utils.page_goto_humano(page, url, timeout=60000)

            # =========================
            # EXTRAÇÃO ROBUSTA DE TÍTULO
            # =========================
            titulo_pagina = None

            # 1. Tenta o seletor específico (sem crashar se não existir)
            try:
                if page.locator("h2.page-title").count() > 0:
                    titulo_pagina = page.locator("h2.page-title").first.inner_text(timeout=2000).strip()
            except Exception:
                pass

            # 2. Fallback: Title da página HTML
            if not titulo_pagina:
                try:
                    titulo_pagina = page.title().strip()
                except Exception:
                    pass

            # 3. Fallback: URL (Último recurso)
            if not titulo_pagina:
                path_parts = urlparse(url).path.strip("/").split("/")
                # Pega o último segmento válido
                slug = path_parts[-1] if path_parts else "edital-confap"
                titulo_pagina = slug.replace("-", " ").replace("_", " ").upper()

            # Extração do conteúdo do corpo
            texto_pagina = ""
            try:
                texto_pagina = page.inner_text("body")
            except Exception:
                utils.logger.warning("Não foi possível extrair o texto do body HTML.")

            textos_analise = []
            arquivos_temp = []

            if texto_pagina and len(texto_pagina) > 500:
                textos_analise.append(texto_pagina)

            # =========================
            # COLETA DE PDFs
            # =========================
            links_pdf = [
                urljoin(BASE_DOMAIN, el.get_attribute("href"))
                for el in page.locator("a[href$='.pdf']").all()
                if el.get_attribute("href")
            ]

            for link_pdf in links_pdf:
                self._baixar_pdf(link_pdf, textos_analise, arquivos_temp)

            if not textos_analise:
                utils.logger.warning("Nenhum conteúdo válido para análise. Pulando edital.")
                return

            # =========================
            # IA – CHAMADA ÚNICA
            # =========================
            utils.logger.info("Enviando edital para análise da IA...")
            resultado_ia = ia_client.analisar_edital_completo(
                textos=textos_analise,
                contexto=titulo_pagina
            )

            if not resultado_ia.get("aprovado"):
                utils.logger.warning(
                    f"⛔ REPROVADO pela IA: {resultado_ia.get('motivo_aprovacao')}"
                )
                return

            # =========================
            # APROVADO
            # =========================
            utils.print_success(f"✅ APROVADO: {resultado_ia.get('titulo')}")

            resultado_ia["icone_instituicao"] = ICONE_PADRAO

            # Prioriza título da IA, fallback para o extraído
            titulo_final = resultado_ia.get("titulo") or titulo_pagina
            edital_id = utils.obter_ou_criar_edital(titulo_final, INSTITUICAO, url_edital=url)

            utils.atualizar_edital_ia(edital_id, resultado_ia)

            arquivos_json = []

            # Página principal
            utils.salvar_arquivo_no_edital(
                edital_id=edital_id,
                url_arquivo=url,
                tipo="Página Web"
            )

            arquivos_json.append({
                "tipo": "Página Web",
                "url": url,
                "nome": urlparse(url).netloc
            })

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

            utils.atualizar_edital_arquivos_json(edital_id, arquivos_json)

        except Exception as e:
            utils.logger.error(f"Erro ao processar edital CONFAP: {e}")

    # ==================================================
    # DOWNLOAD E EXTRAÇÃO PDF
    # ==================================================
    def _baixar_pdf(self, url_pdf, textos_analise, arquivos_temp):

        try:
            nome_pdf = os.path.basename(urlparse(url_pdf).path) or "documento.pdf"
            caminho_pdf = os.path.join(TEMP_DIR, nome_pdf)

            resp = self.session.get(url_pdf, timeout=60)
            if resp.status_code != 200:
                return

            with open(caminho_pdf, "wb") as f:
                f.write(resp.content)

            texto_pdf = utils.extrair_texto_pdf(caminho_pdf)
            if not texto_pdf or len(texto_pdf) < 200:
                # OCR fallback para PDF escaneado
                try:
                    utils.logger.warning(f"OCR ativado para {nome_pdf}")
                    texto_pdf = utils.extrair_texto_ocr(caminho_pdf)
                except Exception as _e_ocr:
                    utils.logger.warning(f"OCR falhou ({nome_pdf}): {_e_ocr}")

            if texto_pdf and len(texto_pdf) > 200:
                textos_analise.append(f"Arquivo: {nome_pdf}\n{texto_pdf}")

            arquivos_temp.append({
                "path": caminho_pdf,
                "url": url_pdf
            })

        except Exception as e:
            utils.logger.warning(f"Falha ao baixar PDF: {e}")


if __name__ == "__main__":
    ConfapProcessor().executar()
