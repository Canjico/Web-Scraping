# FAPES.py (refatorado)

from playwright.sync_api import sync_playwright
from urllib.parse import urljoin

from . import utils
from .IA import ia_client


# ==============================================
# CONFIGURAÇÃO
# ==============================================
BASE_URL = "https://fapes.es.gov.br/Editais/Abertos"
BASE_DOMAIN = "https://fapes.es.gov.br"

INSTITUICAO = "FAPES"
ICONE_PADRAO = "/icons/fapes.png"


def analisar_texto_em_partes(texto_completo, contexto):
    # Wrapper simples: delega para o modo Smart (50k por trecho)
    return ia_client.analisar_smart(texto_completo, contexto=contexto, tamanho_chunk=50000)



class FapesProcessor:

    def executar(self):

        utils.print_section("INICIANDO SCRAPING: FAPES")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            try:
                page.goto(BASE_URL, timeout=120000)

                links_pdfs = []

                for a in page.locator("a[href]").all():
                    href = a.get_attribute("href")
                    if not href:
                        continue

                    if ".pdf" in href.lower():
                        links_pdfs.append(
                            href if href.startswith("http") else urljoin(BASE_DOMAIN, href)
                        )

                utils.logger.info(f"{len(links_pdfs)} PDFs encontrados.")

                for url_pdf in links_pdfs:
                    utils.logger.info(f"\n>>> Processando PDF: {url_pdf}")

                    try:
                        texto_pdf = utils.extrair_texto_por_url(url_pdf)

                        if not texto_pdf or len(texto_pdf) < 800:
                            utils.logger.warning("Texto insuficiente. Pulando.")
                            continue

                        resultado_ia = analisar_texto_em_partes(
                            texto_pdf,
                            contexto="Editais FAPES"
                        )

                        if not resultado_ia.get("aprovado"):
                            utils.logger.warning(
                                f"⛔ REPROVADO pela IA: {resultado_ia.get('motivo_aprovacao')}"
                            )
                            continue

                        utils.print_success(
                            f"✅ APROVADO: {resultado_ia.get('titulo')}"
                        )

                        resultado_ia["icone_instituicao"] = ICONE_PADRAO

                        titulo = resultado_ia.get("titulo") or "Edital FAPES"
                        # ⚡ Aqui só atualiza arquivos se o edital já existe, não cria duplicado
                        edital_id = utils.obter_ou_criar_edital(
                    titulo,
                    INSTITUICAO,
                    url_edital=url_pdf
                )

                        utils.atualizar_edital_ia(
                            edital_id,
                            resultado_ia
                        )

                        # Salva o PDF no edital no SQL
                        utils.salvar_arquivo_no_edital(
                            edital_id,
                            url_arquivo=url_pdf,
                            tipo="Documento",
                            evitar_duplicado=True
                        )

                    except Exception as e:
                        utils.logger.error(
                            f"Erro ao processar PDF FAPES: {e}"
                        )

            finally:
                browser.close()

        utils.print_success("Scraping FAPES finalizado.")


if __name__ == "__main__":
    FapesProcessor().executar()
