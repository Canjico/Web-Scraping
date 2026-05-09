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


## Ajuste extra de configuracao Azure/.env

- `utils.py` agora lê `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT` e `AZURE_OPENAI_API_VERSION` do `.env`.
- Mantida compatibilidade com os valores antigos como fallback para não quebrar execução existente.
- `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_KEY` e `AZURE_API_KEY` são aceitos como chave principal.
- `.env.example` foi expandido com variáveis de banco, IA principal, Web Check, web search, crawler agressivo e timeouts.
- `health_check.py` agora valida se o `.env.example` documenta as variáveis críticas de Azure/Web Check.

## Ajuste 2026-05-07 - Link do painel x link original

- Separada a nocao de `link_painel` (`FRONTEND_APP_URL?id=<id>`) da nocao de `link_original` (URL oficial/tokenizada capturada no site fonte).
- Mantido o campo antigo `link` apontando para o painel por compatibilidade com logs/notificacoes antigas.
- Adicionado teste para garantir que URLs tokenizadas, como `/editais/share/edital/<token>`, nao sejam substituidas pelo link interno do painel.

## Patch env-loader robusto

- `utils.py` agora carrega `.env` explicitamente a partir da pasta do projeto (`Path(__file__).parent / ".env"`).
- Adicionado parser manual de `.env` como fallback caso `python-dotenv` não esteja instalado.
- `AZURE_OPENAI_KEY` e `AZURE_OPENAI_API_KEY` agora são sincronizados automaticamente quando uma delas estiver configurada.
- Mantida compatibilidade: variáveis de ambiente do servidor continuam tendo prioridade e não são sobrescritas.
