# PORTAL_INDUSTRIA.py

import os
import shutil
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

from . import utils
from .IA import ia_client


# ==============================================
# CONFIGURAÇÃO
# ==============================================
BASE_URL = "https://www.portaldaindustria.com.br/canais/plataforma-inovacao-para-industria/"
INSTITUICAO = "SENAI"
ICONE_PADRAO = "/icons/portal-industria.png"

EXTENSOES_VALIDAS = (".pdf", ".doc", ".docx", ".xls", ".xlsx")

PROJETO_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = os.path.join(PROJETO_DIR, "temp_portal_industria")

# limites práticos para não explodir memória, mas permitindo o modo Smart (50k) da IA
MIN_TEXTO_PARA_IA = 300
MAX_TEXTO_POR_FONTE = 200_000  # chars


# ==============================================
class PortalIndustriaProcessor:

    def __init__(self):
        self.session = utils.get_http_session()
        self._preparar_temp()

    # ------------------------------------------
    def _preparar_temp(self):
        if os.path.exists(TEMP_DIR):
            shutil.rmtree(TEMP_DIR)
        os.makedirs(TEMP_DIR, exist_ok=True)

    # ------------------------------------------
    def _baixar_arquivo(self, url: str) -> str | None:
        try:
            nome = os.path.basename(url.split("?")[0]) or "documento.bin"
            path = os.path.join(TEMP_DIR, nome)

            resp = self.session.get(url, timeout=60, verify=False)
            if resp.status_code == 200 and len(resp.content) > 200:
                with open(path, "wb") as f:
                    f.write(resp.content)
                return path
        except Exception as e:
            utils.logger.warning(f"Erro download {url}: {e}")
        return None

    # ------------------------------------------
    def processar_item(self, page, url: str):

        utils.logger.info(f"\n>>> Processando: {url}")

        try:
            page.goto(url, timeout=60000)
            texto_pagina = page.evaluate("document.body.innerText") or ""

            links_arquivos = []
            for a in page.locator("a[href]").all():
                href = a.get_attribute("href")
                if href and href.lower().endswith(EXTENSOES_VALIDAS):
                    links_arquivos.append(urljoin(url, href))

            textos_ia = []

            # Texto da própria página (muitos editais são “o edital é a página”)
            if texto_pagina and len(texto_pagina) >= MIN_TEXTO_PARA_IA:
                textos_ia.append(texto_pagina[:MAX_TEXTO_POR_FONTE])

            arquivos_temp = []

            # Baixa anexos e extrai texto (com OCR quando necessário)
            for link in links_arquivos:
                caminho = self._baixar_arquivo(link)
                if not caminho:
                    continue

                arquivos_temp.append({"path": caminho, "url": link})

                texto = utils.extrair_texto_generico(caminho) or ""

                # OCR fallback (quando não dá pra extrair texto de verdade)
                if not texto or len(texto) < MIN_TEXTO_PARA_IA:
                    utils.logger.warning(
                        f"Texto insuficiente em {os.path.basename(caminho)}. Ativando OCR..."
                    )
                    try:
                        texto = utils.extrair_texto_ocr(caminho) or ""
                    except Exception as e:
                        utils.logger.warning(f"OCR falhou em {os.path.basename(caminho)}: {e}")
                        texto = ""

                if texto and len(texto) >= MIN_TEXTO_PARA_IA:
                    textos_ia.append(
                        f"Arquivo: {os.path.basename(link)}\n{texto[:MAX_TEXTO_POR_FONTE]}"
                    )

            if not textos_ia:
                utils.logger.warning("Nenhum texto válido para IA.")
                return

            resultado_ia = ia_client.analisar_edital_completo(
                textos=textos_ia,
                contexto="Plataforma de Inovação para a Indústria"
            )

            if not resultado_ia.get("aprovado"):
                utils.logger.warning(
                    f"⛔ REPROVADO: {resultado_ia.get('motivo_aprovacao')}"
                )
                return

            utils.print_success(f"✅ APROVADO: {resultado_ia.get('titulo')}")

            resultado_ia["icone_instituicao"] = ICONE_PADRAO

            titulo = resultado_ia.get("titulo") or "Edital Portal da Indústria"

            # ✅ Identidade forte pelo link do edital (evita duplicar no SQL)
            edital_id = utils.obter_ou_criar_edital(
                titulo,
                INSTITUICAO,
                url_edital=url
            )

            utils.atualizar_edital_ia(
                edital_id,
                resultado_ia
            )

            # ==================================================
            # SALVA ARQUIVOS DIRETO NO SQL (SEM HISTÓRICO JSON)
            # ==================================================
            arquivos_json = []

            # Se não houver anexos, o “documento” é o próprio link da página do edital
            utils.salvar_arquivo_no_edital(
                edital_id=edital_id,
                url_arquivo=url,
                tipo="Página Web"
            )
            arquivos_json.append({
                "tipo": "Página Web",
                "url": url,
                "nome": "portaldaindustria.com.br"
            })

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

            # JSON consolidado (compatibilidade / API)
            utils.atualizar_edital_arquivos_json(
                edital_id,
                arquivos_json
            )

        except Exception as e:
            utils.logger.error(f"Erro processamento {url}: {e}")

        finally:
            self._preparar_temp()

    # ------------------------------------------
    def executar(self):

        utils.print_section("INICIANDO SCRAPING: SENAI")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(ignore_https_errors=True)

            page.goto(BASE_URL, timeout=60000)

            links = set()

            for a in page.locator("a[href]").all():
                href = a.get_attribute("href")
                if href and "plataforma-inovacao" in href:
                    links.add(urljoin(BASE_URL, href).split("#")[0])

            utils.logger.info(f"{len(links)} links únicos encontrados.")

            for link in links:
                self.processar_item(page, link)

            browser.close()

        utils.print_success("Scraping Portal da Indústria finalizado.")


# ==============================================
if __name__ == "__main__":
    PortalIndustriaProcessor().executar()
