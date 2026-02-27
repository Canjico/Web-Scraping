# FAPERO.py (robusto + chunking correto para IA)

from playwright.sync_api import sync_playwright
from urllib.parse import urljoin

from . import utils
from .IA import ia_client

BASE_DOMAIN = "https://rondonia.ro.gov.br"
INSTITUICAO = "FAPERO"
ICONE_PADRAO = "/icons/fapero.png"
EXTENSOES_VALIDAS = (".pdf", ".doc", ".docx")


def analisar_texto_em_partes(texto_completo, contexto):
    """Divide o texto em blocos de 50k e envia à IA até ela decidir de forma definitiva."""
    TAMANHO_PARTE = 50000
    partes = [texto_completo[i:i + TAMANHO_PARTE] for i in range(0, len(texto_completo), TAMANHO_PARTE)]
    historico = []

    resultado_final = {"aprovado": None}

    for i, parte in enumerate(partes, start=1):
        utils.logger.info(f"Enviando parte {i}/{len(partes)} para IA")
        historico.append(parte)

        resultado = ia_client.analisar_edital_completo(
            textos=historico,
            contexto=contexto
        )

        # Se aprovado, pode encerrar imediatamente
        if resultado.get("aprovado") is True:
            return resultado

        # Se reprovado definitivo (não só por falta de trecho), encerra
        elif resultado.get("aprovado") is False and resultado.get("motivo_aprovacao") != "Prazo indefinido ou não encontrado no trecho atual":
            return resultado

        # Caso contrário, continua lendo os próximos blocos
        resultado_final = resultado

    # Retorna o último resultado caso não tenha decisão definitiva
    return resultado_final


class FaperoProcessor:

    def executar(self):
        utils.print_section("INICIANDO SCRAPING: FAPERO")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            page = browser.new_page()

            try:
                # Página inicial para pegar anos disponíveis
                utils.page_goto_humano(page, f"{BASE_DOMAIN}/fapero/publicacoes/2025-2/", timeout=120000)
                page.wait_for_load_state("networkidle")

                # 1️⃣ Captura anos dinamicamente (somente números)
                anos_disponiveis = [
                    a.inner_text()
                    for a in page.locator("a[href*='/fapero/publicacoes/']").all()
                    if a.inner_text().isdigit()
                ]
                anos_disponiveis = sorted(list(set(anos_disponiveis)))
                utils.logger.info(f"Anos disponíveis: {anos_disponiveis}")

                # 2️⃣ Itera por cada ano
                for ano in anos_disponiveis:
                    url_ano = f"{BASE_DOMAIN}/fapero/publicacoes/{ano}-2/"
                    utils.logger.info(f"\n>>> Processando ano: {ano} ({url_ano})")
                    utils.page_goto_humano(page, url_ano, timeout=120000)
                    page.wait_for_load_state("networkidle")

                    # Captura links de arquivos de forma eficiente
                    page.wait_for_selector("a[href]", timeout=60000)
                    hrefs = page.locator("a[href]").evaluate_all("els => els.map(e => e.href)")
                    links_arquivos = [h for h in hrefs if any(h.lower().endswith(ext) for ext in EXTENSOES_VALIDAS)]

                    # Se não houver arquivos válidos, usa o próprio link da página
                    if not links_arquivos:
                        links_arquivos = [url_ano]

                    utils.logger.info(f"{len(links_arquivos)} arquivos encontrados para {ano}.")

                    # 3️⃣ Processa cada arquivo
                    for url_arquivo in links_arquivos:
                        utils.logger.info(f"\n>>> Processando arquivo: {url_arquivo}")
                        try:
                            texto_arquivo = utils.extrair_texto_por_url(url_arquivo)
                            if not texto_arquivo or len(texto_arquivo) < 800:
                                utils.logger.warning("Texto insuficiente. Pulando.")
                                continue

                            resultado_ia = analisar_texto_em_partes(
                                texto_arquivo,
                                contexto="Editais FAPERO"
                            )

                            if not resultado_ia.get("aprovado"):
                                utils.logger.warning(
                                    f"⛔ REPROVADO pela IA: {resultado_ia.get('motivo_aprovacao')}"
                                )
                                continue

                            utils.print_success(f"✅ APROVADO: {resultado_ia.get('titulo')}")
                            resultado_ia["icone_instituicao"] = ICONE_PADRAO

                            # 4️⃣ Salva ou atualiza edital no SQL
                            titulo = resultado_ia.get("titulo") or "Edital FAPERO"
                            edital_id = utils.obter_ou_criar_edital(
                                titulo,
                                INSTITUICAO,
                                url_edital=url_arquivo
                            )
                            utils.atualizar_edital_ia(edital_id, resultado_ia)

                            # 5️⃣ Salva arquivos no SQL (evita duplicados)
                            utils.salvar_arquivo_no_edital(
                                edital_id,
                                url_arquivo=url_arquivo,
                                tipo="Documento",
                                evitar_duplicado=True
                            )

                        except Exception as e:
                            utils.logger.error(f"Erro ao processar arquivo FAPERO: {e}")

            finally:
                browser.close()

        utils.print_success("Scraping FAPERO finalizado.")


if __name__ == "__main__":
    FaperoProcessor().executar()
