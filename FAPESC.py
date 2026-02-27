# FAPESC.py

from playwright.sync_api import sync_playwright
from urllib.parse import urljoin

from . import utils
from .IA import ia_client


# ==============================================
# CONFIGURAÇÃO
# ==============================================
BASE_URL = "https://fapesc.sc.gov.br/chamadas-abertas/"
INSTITUICAO = "FAPESC"
ICONE_PADRAO = "/icons/fapesc.png"


class FapescProcessor:

    def executar(self):

        utils.print_section(f"INICIANDO SCRAPING: {INSTITUICAO}")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            try:
                page.goto(BASE_URL, timeout=120000)
                # Correção do seletor conforme análise do HTML da FAPESC (WordPress/UPK)
                page.wait_for_selector("h3.upk-title a", timeout=30000)

                anchors = page.query_selector_all("h3.upk-title a")

                links = []
                for a in anchors:
                    href = a.get_attribute("href")
                    if not href:
                        continue
                    links.append(urljoin(BASE_URL, href))

                utils.logger.info(f"{len(links)} chamadas encontradas.")

                for link in links:
                    # Filtros específicos se necessário
                    if "acordos-dfg" in link:
                        continue

                    utils.logger.info(f">>> Processando: {link}")

                    try:
                        page.goto(link, timeout=60000)
                        texto_pagina = page.inner_text("body")

                        if not texto_pagina or len(texto_pagina) < 1000:
                            utils.logger.warning("Texto insuficiente. Pulando.")
                            continue

                        # ===============================
                        # IA CENTRAL DECIDE TUDO
                        # ===============================
                        resultado_ia = ia_client.analisar_edital_completo(
                            textos=[texto_pagina],
                            contexto=f"Chamadas públicas {INSTITUICAO}"
                        )

                        if not resultado_ia.get("aprovado"):
                            utils.logger.warning(
                                f"⛔ REPROVADO: {resultado_ia.get('motivo_aprovacao')}"
                            )
                            continue

                        utils.print_success(
                            f"✅ APROVADO: {resultado_ia.get('titulo')}"
                        )

                        resultado_ia["icone_instituicao"] = ICONE_PADRAO

                        titulo = resultado_ia.get("titulo") or f"Edital {INSTITUICAO}"

                        # USA URL ORIGINAL COMO IDENTIDADE ESTÁVEL
                        edital_id = utils.obter_ou_criar_edital(
                            titulo,
                            INSTITUICAO,
                            url_edital=link
                        )

                        utils.atualizar_edital_ia(
                            edital_id,
                            resultado_ia
                        )

                        # ==========================================
                        # SALVAMENTO PADRÃO SQL
                        # ==========================================

                        arquivos_json = []

                        utils.salvar_arquivo_no_edital(
                            edital_id=edital_id,
                            url_arquivo=link,
                            tipo="Página Web"
                        )

                        arquivos_json.append({
                            "tipo": "Documento",
                            "url": link,
                            "nome": link.rstrip("/").split("/")[-1]
                        })

                        utils.atualizar_edital_arquivos_json(
                            edital_id,
                            arquivos_json
                        )

                    except Exception as e:
                        utils.logger.error(
                            f"Erro ao processar chamada {INSTITUICAO}: {e}"
                        )

            finally:
                browser.close()

        utils.print_success(f"Scraping {INSTITUICAO} finalizado.")


if __name__ == "__main__":
    FapescProcessor().executar()