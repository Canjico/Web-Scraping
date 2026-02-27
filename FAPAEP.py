# FAPEAP.py

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
TEMP_DIR = os.path.join(PROJETO_DIR, "temp_downloads_fapeap")

BASE_URL = "https://fapeap.portal.ap.gov.br/publicacoes"
BASE_DOMAIN = "https://fapeap.portal.ap.gov.br"

INSTITUICAO = "FAPEAP"
ICONE_PADRAO = "/icons/fapeap.png"


class FapeapProcessor:

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
    # FASE 1: DESCOBERTA (TABELA + PAGINAÇÃO LIVEWIRE)
    # ==================================================
    def _coletar_blocos_paginados(self, page) -> list:
        """
        Coleta TODOS os editais da tabela navegando página por página.
        Agora baseia-se em <tr class='clickable-row'> e data-href.
        """
        utils.print_section("FASE 1: COLETA DE EDITAIS (LINHAS CLICÁVEIS)")

        blocos = []
        pagina = 1
        urls_vistas = set()

        while True:
            utils.logger.info(f"Coletando itens da página {pagina}...")

            try:
                page.wait_for_load_state("networkidle")
            except:
                pass

            # Seletor atualizado para a nova estrutura de tabela
            linhas = page.locator("tr.clickable-row").all()
            
            if not linhas:
                utils.logger.warning(f"Nenhuma linha encontrada na página {pagina}.")

            novos_nesta_pagina = 0
            for row in linhas:
                url_pdf = row.get_attribute("data-href")
                
                if not url_pdf:
                    continue
                
                # Normaliza URL se necessário
                url_final = (
                    url_pdf if url_pdf.startswith("http")
                    else urljoin(BASE_DOMAIN, url_pdf)
                )

                if url_final in urls_vistas:
                    continue

                # Tenta pegar o título dentro do span específico
                try:
                    titulo_el = row.locator("span.tw-text-black").first
                    titulo = titulo_el.inner_text().strip() if titulo_el.count() > 0 else "Edital Sem Título"
                except:
                    titulo = "Edital FAPEAP (Título não identificado)"

                item = {
                    "url": url_final,
                    "titulo": titulo
                }
                
                blocos.append(item)
                urls_vistas.add(url_final)
                novos_nesta_pagina += 1

            utils.logger.info(f"Itens coletados nesta página: {novos_nesta_pagina}")

            # 🔹 Detecta botão "Próxima" ATIVO (Lógica Livewire)
            botao_prox = page.locator("button[dusk='nextPage']")

            if botao_prox.count() > 0 and botao_prox.first.is_visible() and botao_prox.first.is_enabled():
                utils.logger.info("Avançando para próxima página...")
                try:
                    botao_prox.first.click()
                    page.wait_for_timeout(2000) # Aguarda reação do Livewire
                    pagina += 1
                except Exception as e:
                    utils.logger.warning(f"Erro ao clicar na próxima página: {e}")
                    break
            else:
                utils.logger.info("Última página detectada. Encerrando paginação.")
                break

        utils.logger.info(f"Total de editais coletados: {len(blocos)}")
        return blocos

    # ==================================================
    # FASE 2: EXECUÇÃO PRINCIPAL (SIMPLIFICADA)
    # ==================================================
    def executar(self):

        utils.print_section("INICIANDO SCRAPING: FAPEAP")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            try:
                page.goto(BASE_URL, timeout=120000)

                # 🔹 FASE 1: Coleta via tabela
                blocos = self._coletar_blocos_paginados(page)

                utils.logger.info(f"Total de arquivos para processar: {len(blocos)}")

                # 🔹 FASE 2: Download e Análise direta
                for i, bloco in enumerate(blocos, start=1):
                    utils.logger.info(
                        f"\n>>> Processando {i}/{len(blocos)}: {bloco['titulo']}"
                    )

                    # 1️⃣ Download do Arquivo
                    nome_arquivo = os.path.basename(urlparse(bloco['url']).path) or f"doc_{i}.pdf"
                    caminho_local = os.path.join(TEMP_DIR, nome_arquivo)
                    
                    try:
                        resp = self.session.get(bloco['url'], timeout=60, verify=False)
                        if resp.status_code != 200:
                            utils.logger.warning(f"Erro {resp.status_code} ao baixar {bloco['url']}")
                            continue
                        
                        with open(caminho_local, "wb") as f:
                            f.write(resp.content)
                            
                    except Exception as e:
                        utils.logger.error(f"Falha no download: {e}")
                        continue

                    # 2️⃣ Extração de Texto
                    texto = utils.extrair_texto_pdf(caminho_local)
                    if not texto or len(texto) < 100:
                        utils.logger.warning("Texto insuficiente ou arquivo ilegível. Ativando OCR...")
                        try:
                            texto = utils.extrair_texto_ocr(caminho_local)
                        except Exception as _e_ocr:
                            utils.logger.warning(f"OCR falhou: {_e_ocr}")

                    if not texto or len(texto) < 100:
                        utils.logger.warning("Texto insuficiente mesmo após OCR. Pulando.")
                        if os.path.exists(caminho_local):
                            os.remove(caminho_local)
                        continue

                    # 3️⃣ IA
                    utils.logger.info("Enviando para análise da IA...")
                    resultado_ia = ia_client.analisar_edital_completo(
                        textos=[f"Título: {bloco['titulo']}\nURL: {bloco['url']}\n\n{texto}"],
                        contexto=f"FAPEAP - {bloco['titulo']}"
                    )

                    if not resultado_ia.get("aprovado"):
                        utils.logger.warning(
                            f"⛔ REPROVADO pela IA: {resultado_ia.get('motivo_aprovacao')}"
                        )
                        if os.path.exists(caminho_local):
                            os.remove(caminho_local)
                        continue

                    # 4️⃣ APROVADO
                    utils.print_success(f"✅ APROVADO: {resultado_ia.get('titulo')}")
                    resultado_ia["icone_instituicao"] = ICONE_PADRAO

                    # 5️⃣ Cria/Atualiza Edital
                    titulo_final = resultado_ia.get("titulo") or bloco["titulo"]
                    edital_id = utils.obter_ou_criar_edital(titulo_final, INSTITUICAO, url_edital=bloco['url'])
                    utils.atualizar_edital_ia(edital_id, resultado_ia)

                    # 6️⃣ Salva Arquivo vinculado
                    utils.salvar_arquivo_no_edital(
                        edital_id=edital_id,
                        url_arquivo=bloco['url'],
                        caminho_local=caminho_local,
                        tipo="Edital"
                    )

                    # 7️⃣ JSON final
                    arquivo_json = [{
                        "tipo": "Edital",
                        "url": bloco['url'],
                        "nome": nome_arquivo
                    }]
                    utils.atualizar_edital_arquivos_json(edital_id, arquivo_json)

                    # Limpeza local pós-processamento
                    if os.path.exists(caminho_local):
                        os.remove(caminho_local)

            except Exception as e:
                utils.logger.error(f"Erro fatal no scraping da FAPEAP: {e}")

            finally:
                browser.close()

        self._preparar_diretorio_temporario()
        utils.print_success("Scraping da FAPEAP finalizado.")


if __name__ == "__main__":
    FapeapProcessor().executar()
