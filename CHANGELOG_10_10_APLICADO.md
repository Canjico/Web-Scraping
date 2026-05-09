# Alterações aplicadas para hardening 10/10

## Correções críticas

- `Edital.py` agora ignora `run_tests.py` e `health_check.py`, evitando execução acidental de ferramentas internas como scrapers.
- Adicionado timeout configurável para cada scraper legado: `SCRAPER_SUBPROCESS_TIMEOUT_SECONDS`.
- Adicionado timeout configurável para o fallback inteligente: `SMART_FALLBACK_TIMEOUT_SECONDS`.
- Subprocessos que travam retornam erro controlado `124` com `[TIMEOUT]` nos logs.
- Detecta `.pyc` legado incompatível com o Python atual e pula direto para o fallback inteligente quando `SKIP_INCOMPATIBLE_LEGACY_PYC=1`.
- Quando o legado falha/pula e o SMART processa a fonte, o ciclo registra `sucesso_com_fallback` mesmo que não haja novos registros persistidos por duplicidade ou ambiente sem banco.

## Compatibilidade

- Todos os 27 wrappers legados agora possuem fallback direto para execução manual.
- `_pyc_bridge.py` agora expõe relatório estruturado de compatibilidade de bytecode.
- `utils.py` não quebra mais no import quando dependências pesadas ainda não estão instaladas; as funções específicas continuam exigindo as dependências no momento de uso.

## Qualidade / QA

- Novo `health_check.py` para inspeção local sem acessar banco, IA ou internet.
- Novos testes para:
  - arquivos internos fora do ciclo;
  - timeout controlado;
  - compatibilidade `.pyc`;
  - wrappers protegidos com fallback.
- `python run_tests.py` executado com sucesso: 10 testes OK.
- `python health_check.py` executado com status OK.

## Arquivos principais alterados

- `Edital.py`
- `_pyc_bridge.py`
- todos os wrappers de fonte (`FINEP.py`, `FAPESP.py`, etc.)
- `utils.py`
- `.env.example`
- `tests/test_runtime_hardening.py`
- `health_check.py`
- `README_10_10_HARDENING.md`
