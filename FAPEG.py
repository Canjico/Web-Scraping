# FAPEG.py

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
TEMP_DIR = os.path.join(PROJETO_DIR, "temp_downloads_fapeg")

BASE_URL = "https://goias.gov.br/fapeg/editais/inscricoes-abertas/"
INSTITUICAO = "FAPEG"
ICONE_PADRAO = "/icons/fapeg.png"

EXTENSOES_VALIDAS = (".pdf",)


class FapegProcessor:

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

        utils.print_section("INICIANDO SCRAPING: FAPEG")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            try:
                page.goto(BASE_URL, timeout=60000)
                page.wait_for_selector("td a", timeout=20000)

                # 1️⃣ Coleta links dos editais
                links_editais = []
                for a in page.locator("td a").all():
                    if a.inner_text().strip().upper() == "ABRIR":
                        href = a.get_attribute("href")
                        if href:
                            links_editais.append(urljoin(BASE_URL, href))

                utils.logger.info(f"{len(links_editais)} editais encontrados.")

                # 2️⃣ Processa cada edital
                for i, link_edital in enumerate(links_editais, start=1):
                    utils.logger.info(f"\n>>> Processando edital {i}/{len(links_editais)}: {link_edital}")

                    try:
                        page.goto(link_edital, timeout=60000)
                        texto_pagina = page.inner_text("body")

                        # 3️⃣ Coleta PDFs
                        links_pdf = [
                            urljoin(link_edital, a.get_attribute("href"))
                            for a in page.locator("section.entry-content a").all()
                            if a.get_attribute("href")
                            and a.get_attribute("href").lower().endswith(EXTENSOES_VALIDAS)
                        ]

                        if not links_pdf:
                         utils.logger.warning("Nenhum PDF encontrado. Usando a própria página como documento.")
                         links_pdf = []

                        arquivos_temp = []
                        textos_extraidos = []

                        # Texto da página
                        if texto_pagina and len(texto_pagina) > 300:
                            textos_extraidos.append(texto_pagina)

                        # 4️⃣ Download + extração (com OCR)
                        for pdf_url in links_pdf:
                            nome = os.path.basename(urlparse(pdf_url).path)
                            caminho = os.path.join(TEMP_DIR, nome)

                            try:
                                resp = self.session.get(pdf_url, timeout=60, verify=False)
                                if resp.status_code != 200:
                                    continue

                                with open(caminho, "wb") as f:
                                    f.write(resp.content)

                                arquivos_temp.append({"path": caminho, "url": pdf_url})

                                texto = utils.extrair_texto_generico(caminho)

                                # 🔹 OCR fallback (igual FINEP)
                                if not texto or len(texto) < 300:
                                    utils.logger.warning(
                                        f"Texto insuficiente em {nome}. Ativando OCR..."
                                    )
                                    texto = utils.extrair_texto_ocr(caminho)

                                if texto and len(texto) > 300:
                                    textos_extraidos.append(
                                        f"Arquivo: {nome}\n{texto}"
                                    )

                            except Exception as e:
                                utils.logger.warning(f"Erro ao processar PDF {nome}: {e}")

                        if not textos_extraidos:
                            utils.logger.warning("Nenhum texto válido extraído.")
                            self._limpar_temporarios(arquivos_temp)
                            continue

                        # 5️⃣ IA decide
                        utils.logger.info("Enviando para análise da IA...")
                        resultado_ia = ia_client.analisar_edital_completo(
                            textos=textos_extraidos,
                            contexto="FAPEG - Editais"
                        )

                        if not resultado_ia.get("aprovado"):
                            utils.logger.warning(
                                f"⛔ REPROVADO pela IA: {resultado_ia.get('motivo_aprovacao')}"
                            )
                            self._limpar_temporarios(arquivos_temp)
                            continue

                        # 6️⃣ APROVADO
                        utils.print_success(f"✅ APROVADO: {resultado_ia.get('titulo')}")
                        resultado_ia["icone_instituicao"] = ICONE_PADRAO

                        titulo_final = resultado_ia.get("titulo") or "Edital FAPEG"
                        edital_id = utils.obter_ou_criar_edital(titulo_final, INSTITUICAO, url_edital=link_edital)

                        # Atualiza sempre (banco decide se muda algo)
                        utils.atualizar_edital_ia(edital_id, resultado_ia)

                        # 7️⃣ Vincula arquivos
                        arquivos_json = []

                        # Página Web sempre entra como documento (quando não há PDF, é o "edital")
                        utils.salvar_arquivo_no_edital(
                            edital_id=edital_id,
                            url_arquivo=link_edital,
                            tipo="Página Web",
                            evitar_duplicado=True
                        )

                        arquivos_json.append({
                            "tipo": "Página Web",
                            "url": link_edital,
                            "nome": urlparse(link_edital).netloc
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

                        # 8️⃣ Atualiza JSON consolidado
                        utils.atualizar_edital_arquivos_json(edital_id, arquivos_json)

                    except Exception as e_item:
                        utils.logger.error(f"Erro ao processar edital {link_edital}: {e_item}")
                        continue

            except Exception as e:
                utils.logger.error(f"Erro fatal no scraping da FAPEG: {e}")

            finally:
                browser.close()

        self._preparar_diretorio_temporario()
        utils.print_success("Scraping da FAPEG finalizado.")

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
    FapegProcessor().executar()
