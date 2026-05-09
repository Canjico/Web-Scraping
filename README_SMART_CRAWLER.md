# Smart Crawler / Fallback Inteligente

Esta camada foi adicionada sem remover os scrapers legados. A ideia é simples: o scraper específico continua rodando; depois, o `smart_fallback.py` revisa a fonte oficial com um crawler genérico para descobrir páginas de edital, anexos, PDFs/DOCX/XLSX e links embutidos no HTML.

## O que mudou

- `Edital.py`
  - Não executa mais `web_check.py` como se fosse scraper.
  - Ignora os módulos internos novos (`source_registry.py`, `smart_crawler.py`, `smart_fallback.py`).
  - Corrige o total de fontes para refletir as fontes implementadas, salvo override por variável de ambiente.
  - Executa fallback inteligente por fonte após o scraper legado.
  - Se o scraper legado falhar, mas o fallback persistir candidatos confiáveis, a fonte entra como `sucesso_com_fallback`.
  - Grava resumo `[SMART-JSON]` no log do ciclo.

- `smart_sources.json`
  - Registry explícito das fontes oficiais.
  - Evita depender de “todo `.py` da pasta” para descobrir fonte.

- `source_registry.py`
  - Carrega as fontes do registry.
  - Normaliza domínio, URL inicial e nome da fonte.

- `smart_crawler.py`
  - Busca HTML com `requests`.
  - Usa `sitemap.xml` / `sitemap_index.xml` como reforço.
  - Extrai links de `<a>`, `iframe`, canonical/alternate e URLs embutidas em HTML/JSON/script.
  - Classifica link como edital, listagem, documento de edital ou irrelevante.
  - Detecta documentos por extensão: PDF, DOC, DOCX, XLS, XLSX, CSV, ZIP, RAR, ODT, ODS.
  - Detecta indícios de status aberto/encerrado por texto e datas futuras.
  - Gera score, confiança e motivos da decisão.
  - Renderiza com Playwright opcionalmente quando o HTML parece depender muito de JavaScript.
  - Salva auditoria em `../Log/smart_crawler/<fonte>.json`.

- `smart_fallback.py`
  - CLI para rodar o fallback de uma fonte específica.
  - Pode persistir candidatos confiáveis no banco usando as funções existentes do `utils.py`.

## Como testar uma fonte

```bash
python smart_fallback.py --script FINEP.py --json
```

Com persistência:

```bash
python smart_fallback.py --script FINEP.py --persist
```

## Variáveis úteis

```bash
ENABLE_SMART_FALLBACK=1              # liga/desliga fallback no ciclo principal
SMART_FALLBACK_ON_SUCCESS=1          # roda fallback mesmo quando o legado não falha
SMART_CRAWLER_MAX_DEPTH=2            # profundidade máxima
SMART_CRAWLER_MAX_PAGES=35           # páginas HTML por fonte
SMART_CRAWLER_MAX_DOCS=120           # documentos por fonte
SMART_CRAWLER_MIN_CONFIDENCE=0.62    # confiança mínima para virar candidato
SMART_PERSIST_MIN_CONFIDENCE=0.66    # confiança mínima para salvar no banco
SMART_PERSIST_MAX_CANDIDATES=25      # limite de candidatos persistidos por fonte
SMART_PERSIST_CLOSED=0               # por padrão não salva encerrados automaticamente
SMART_USE_SITEMAP=1                  # tenta sitemap.xml
SMART_USE_PLAYWRIGHT=1               # tenta renderizar sites JS-heavy se Playwright existir
SMART_USE_IA=1                       # usa IA existente quando disponível
SMART_IA_MAX_DOCS_READ=3             # documentos lidos para análise da IA por candidato
```

## Observação honesta

Nenhum scraping fica literalmente 100% imortal. O ganho aqui é que a falha deixa de ser silenciosa: o sistema tenta seletores legados, fallback genérico, sitemap, URLs embutidas, classificação semântica e ainda gera auditoria por fonte. Isso aproxima o projeto de um coletor 10/10 porque ele passa a ser resiliente, rastreável e revisável.

## Hardening adicional

Veja também `README_10_10_HARDENING.md` para o pacote de melhorias aplicado: execução adaptativa, detecção de documentos sem extensão, links escondidos em HTML/JS, sitemap encadeado, prazo contextual e testes de fumaça.
