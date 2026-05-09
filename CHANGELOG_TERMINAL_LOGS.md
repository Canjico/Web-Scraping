# Atualização: logs detalhados no terminal

## Objetivo
Melhorar a visibilidade do scraping durante a execução real, sem depender apenas do arquivo em `../Log`.

## Alterações aplicadas

- `Edital.py`
  - Adicionado resumo por fonte no terminal.
  - Adicionado detalhamento por edital salvo: ID, título, IA, status final, prazo, confiança, documentos detectados, `link_original` e `link_painel`.
  - Adicionado resumo do `smart_fallback`: páginas, links, documentos, candidatos, persistidos, pulados, warnings e erros.
  - Adicionado streaming opcional de stdout/stderr dos subprocessos em tempo real.
  - Aumentados limites configuráveis de stdout/stderr gravados no TXT e na auditoria.

- `smart_fallback.py`
  - Adicionado modo `SMART_VERBOSE_LOG=1` para imprimir candidatos, documentos, motivos, persistidos e pulados.

- `smart_crawler.py`
  - `persist_report()` agora retorna detalhes de candidatos persistidos e pulados para auditoria.

- `.env.example`
  - Incluídas variáveis de controle de log e auditoria.

## Variáveis novas

```env
SCRAPER_LOG_TO_CONSOLE=1
SCRAPER_LOG_VERBOSE=1
SCRAPER_STREAM_SUBPROCESS=1
SMART_VERBOSE_LOG=1
SMART_VERBOSE_MAX_CANDIDATES=20
SMART_VERBOSE_MAX_DOCS_PER_CANDIDATE=8
SCRAPER_CONSOLE_SHOW_STDOUT_TAIL=0
SCRAPER_CONSOLE_OUTPUT_CHARS=8000
SCRAPER_CONSOLE_MAX_EDITAIS=50
SCRAPER_FILE_STDOUT_CHARS=50000
SCRAPER_FILE_STDERR_CHARS=50000
SCRAPER_AUDIT_STDOUT_CHARS=12000
SCRAPER_AUDIT_STDERR_CHARS=12000
```

## Validação

- `python -m py_compile Edital.py smart_fallback.py smart_crawler.py`
- `python run_tests.py` → 12 testes OK
- `python health_check.py` → status OK
