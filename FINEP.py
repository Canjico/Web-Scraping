# FINEP.py

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
TEMP_DIR = os.path.join(PROJETO_DIR, "temp_downloads_finep")

BASE_URL = "http://www.finep.gov.br/chamadas-publicas?situacao=aberta"
BASE_DOMAIN = "http://www.finep.gov.br"

INSTITUICAO = "FINEP"
ICONE_PADRAO = "/icons/finep.png"

EXTENSOES_VALIDAS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar")


class FinepProcessor:

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
    # FASE 1: DESCOBERTA (CRAWLING COM PAGINAÇÃO)
    # ==================================================
    def _coletar_links_editais(self, page) -> list:
        """
        Navega por todas as páginas da FINEP e coleta os links das chamadas abertas.
        """
        utils.print_section("FASE 1: COLETA DE LINKS (PAGINAÇÃO)")
        links_encontrados = set()
        pagina_atual = 1

        while True:
            try:
                page.wait_for_load_state("networkidle")
                
                # 🔹 BLINDAGEM 1: Aguarda elementos aparecerem para evitar DOM momentaneamente vazio
                try:
                    page.wait_for_selector("a[href*='/chamadas-publicas/chamadapublica/']", state="attached", timeout=5000)
                except:
                    utils.logger.warning(f"Aviso: Nenhum link de chamada detectado imediatamente na pág {pagina_atual} (pode estar vazia ou lenta).")

                # Coleta links da página atual
                anchors = page.locator("a[href*='/chamadas-publicas/chamadapublica/']").all()
                novos_nesta_pagina = 0
                
                for a in anchors:
                    href = a.get_attribute("href")
                    if href:
                        full_url = href if href.startswith("http") else urljoin(BASE_DOMAIN, href)
                        if full_url not in links_encontrados:
                            links_encontrados.add(full_url)
                            novos_nesta_pagina += 1
                
                utils.logger.info(f"Página {pagina_atual}: {novos_nesta_pagina} novos editais encontrados. Total acumulado: {len(links_encontrados)}")

                # 🔹 BLINDAGEM 3 (SEGURANÇA EXTREMA): Se não achou nada novo, encerra para evitar loop infinito
                if novos_nesta_pagina == 0:
                    utils.logger.warning(f"Segurança: Nenhum novo link encontrado na página {pagina_atual}. Encerrando paginação.")
                    break

                # Tenta achar botão 'Próx' clicável (tag <a> com title='Próx')
                # Se virar <span> ou não existir, encerra.
                botao_prox = page.locator("a[title='Próx']")

                if botao_prox.count() > 0 and botao_prox.first.is_visible():
                    utils.logger.info("Avançando para próxima página...")
                    
                    # 🔹 BLINDAGEM 2: Garante que a URL mudou ou o reload aconteceu
                    url_anterior = page.url
                    botao_prox.first.click()
                    
                    try:
                        # Espera a URL mudar (FINEP usa query params na paginação)
                        page.wait_for_function("window.location.href !== arguments[0]", url_anterior, timeout=10000)
                    except:
                        utils.logger.warning("URL não mudou após clique (pode ser AJAX), confiando no networkidle...")
                    
                    page.wait_for_load_state("networkidle")
                    pagina_atual += 1
                else:
                    utils.logger.info("Fim da paginação detectado (botão 'Próx' não encontrado ou inativo).")
                    break
            
            except Exception as e:
                utils.logger.warning(f"Erro durante paginação na pág {pagina_atual}: {e}")
                break

        return sorted(links_encontrados)

    # ==================================================
    # FASE 2: EXECUÇÃO PRINCIPAL
    # ==================================================
    def executar(self):

        utils.print_section("INICIANDO SCRAPING: FINEP")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            try:
                # Acessa a primeira página
                utils.page_goto_humano(page, BASE_URL, timeout=120000)

                # 🔹 FASE 1: Coletar todos os links paginados
                urls_chamadas = self._coletar_links_editais(page)

                utils.logger.info(f"Total final de chamadas para processar: {len(urls_chamadas)}")

                # 🔹 FASE 2: Processar cada edital individualmente
                for i, url_chamada in enumerate(urls_chamadas, start=1):
                    utils.logger.info(f"\n>>> Processando chamada {i}/{len(urls_chamadas)}: {url_chamada}")
                    
                    try:
                        utils.page_goto_humano(page, url_chamada, timeout=120000)
                        texto_pagina = page.inner_text("body")

                        # 1️⃣ Coleta links de arquivos
                        arquivos_temp = []
                        links = page.locator("a[href]").all()

                        for a in links:
                            href = a.get_attribute("href")
                            if not href:
                                continue

                            if href.lower().endswith(EXTENSOES_VALIDAS):
                                url_arquivo = (
                                    href if href.startswith("http")
                                    else urljoin(BASE_DOMAIN, href)
                                )

                                nome = os.path.basename(urlparse(url_arquivo).path)
                                caminho = os.path.join(TEMP_DIR, nome)

                                try:
                                    resp = self.session.get(url_arquivo, timeout=60, verify=False)
                                    if resp.status_code == 200:
                                        with open(caminho, "wb") as f:
                                            f.write(resp.content)
                                        arquivos_temp.append(
                                            {"path": caminho, "url": url_arquivo}
                                        )
                                except Exception as e:
                                    utils.logger.warning(f"Falha download {nome}: {e}")

                        # 2️⃣ Extração de texto
                        textos_extraidos = []

                        if texto_pagina and len(texto_pagina) > 300:
                            textos_extraidos.append(texto_pagina)

                        for arq in arquivos_temp:
                            texto = utils.extrair_texto_generico(arq["path"])

                            # 🔹 FALLBACK OCR
                            if not texto or len(texto) < 300:
                                utils.logger.warning(
                                    f"Texto insuficiente em {os.path.basename(arq['path'])}. Ativando OCR..."
                                )
                                texto = utils.extrair_texto_ocr(arq["path"])

                            if texto and len(texto) > 300:
                                textos_extraidos.append(
                                    f"Arquivo: {os.path.basename(arq['path'])}\n{texto}"
                                )

                        if not textos_extraidos:
                            utils.logger.warning("Nenhum texto válido para análise.")
                            self._limpar_temporarios(arquivos_temp)
                            continue

                        # 3️⃣ IA decide (CHAMADA ÚNICA)
                        utils.logger.info("Enviando chamada para análise da IA...")
                        resultado_ia = ia_client.analisar_edital_completo(
                            textos=textos_extraidos,
                            contexto="FINEP - Chamada Pública"
                        )

                        if not resultado_ia.get("aprovado"):
                            utils.logger.warning(
                                f"⛔ REPROVADO pela IA: {resultado_ia.get('motivo_aprovacao')}"
                            )
                            self._limpar_temporarios(arquivos_temp)
                            continue

                        # 4️⃣ APROVADO
                        utils.print_success(f"✅ APROVADO: {resultado_ia.get('titulo')}")
                        utils.logger.info(f"Motivo: {resultado_ia.get('motivo_aprovacao')}")

                        resultado_ia["icone_instituicao"] = ICONE_PADRAO

                        # 5️⃣ Cria ou recupera edital
                        titulo_final = resultado_ia.get("titulo") or "Chamada Pública FINEP"
                        edital_id = utils.obter_ou_criar_edital(titulo_final, INSTITUICAO)

                        # 6️⃣ Atualiza dados do edital
                        utils.atualizar_edital_ia(edital_id, resultado_ia)

                        # 7️⃣ Vincula arquivos
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

                        # 8️⃣ Atualiza JSON consolidado
                        utils.atualizar_edital_arquivos_json(edital_id, arquivos_json)
                    
                    except Exception as e_item:
                        utils.logger.error(f"Erro ao processar item {url_chamada}: {e_item}")
                        # Continua para o próximo item
                        continue

            except Exception as e:
                utils.logger.error(f"Erro fatal no scraping da FINEP: {e}")

            finally:
                browser.close()

        self._preparar_diretorio_temporario()
        utils.print_success("Scraping da FINEP finalizado.")

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
    FinepProcessor().executar()