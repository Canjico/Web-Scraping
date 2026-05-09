# Hardening 10/10 do Web Scraping de Editais

Este pacote mantém os scrapers legados e adiciona reforços sem reescrever tudo do zero. A prioridade foi: **não quebrar o fluxo atual**, mas remover falhas silenciosas e aumentar a captura de editais/documentos em sites instáveis.

## Principais melhorias aplicadas

### 1. Execução mais segura no `Edital.py`

Antes, o executor assumia que a pasta do projeto sempre se chamava `sites` e rodava:

```bash
python -m sites.FONTE
```

Agora ele roda direto pelo caminho do arquivo por padrão:

```bash
python FONTE.py
```

Se você realmente quiser executar como pacote, use:

```env
SCRAPER_EXEC_AS_MODULE=1
SCRAPER_PACKAGE_NAME=sites
```

### 2. Fallback não mente mais sucesso

Antes, o `smart_fallback.py` podia retornar `ok: true` mesmo com:

```json
{"pages_fetched": 0, "candidates": 0}
```

Agora isso vira erro controlado:

```json
{
  "ok": false,
  "status": "fonte_nao_capturada",
  "degraded": true
}
```

Isso evita o pior tipo de bug: o robô falha e ainda sai sorrindo na foto.

### 3. Detecção melhor de documentos sem extensão

O crawler agora reconhece rotas como:

```text
/download?id=123
/arquivo/visualizar/999
/api/documento/123
?filename=edital.pdf
```

Além de usar `HEAD`/`Content-Type`/`Content-Disposition` para confirmar anexos quando o link não termina com `.pdf`, `.docx`, `.xlsx` etc.

Variáveis relevantes:

```env
SMART_PROBE_DOCUMENTS=1
SMART_MAX_HEAD_PROBES=80
```

### 4. Extração de links escondidos no HTML

Além de `<a href>`, agora captura links em:

- `iframe`, `embed`, `object`, `source`
- `form action`
- `data-url`, `data-href`, `data-file`, `data-download`, `data-arquivo`, `data-documento`
- `onclick="window.open('/arquivo')"`
- URLs absolutas e relativas dentro de JSON/script embutido

Isso melhora muito em sites públicos modernos que escondem download atrás de botão bonito e HTML amaldiçoado.

### 5. Sitemap/robots mais forte

O crawler agora tenta:

- `/sitemap.xml`
- `/sitemap_index.xml`
- `robots.txt` procurando linhas `Sitemap:`
- sitemaps encadeados

Com limite para não sair mordendo a internet inteira.

### 6. Prazo final mais inteligente

Antes o sistema pegava a **maior data da página** como `data_final_submissao`. Isso é perigoso porque a maior data pode ser:

- resultado final;
- recurso;
- homologação;
- evento;
- cronograma posterior.

Agora existe `extract_deadline_dates()`, que prioriza datas próximas de termos como:

- prazo;
- submissão;
- inscrição;
- envio de propostas;
- data limite;
- encerramento.

O payload também registra o método:

```json
"data_final_submissao_metodo": "prazo_contextual"
```

Se não houver prazo claro, ele cai para `maior_data_detectada` ou `nao_detectado`.

### 7. Playwright com aviso explícito

Se o site parece depender de JavaScript e o Playwright não estiver disponível, o relatório agora registra warning. Antes ele seguia como se nada tivesse acontecido.

### 8. Ponte `.pyc` mais auditável

A `_pyc_bridge.py` agora:

- procura `.pyc` compatível com a versão atual do Python;
- informa claramente quando existe `.pyc`, mas ele é incompatível;
- cria alias de pacote para reduzir erro de import relativo em execução direta.

Importante: `.pyc` de Python 3.11 não roda em Python 3.13. Isso não é bug do crawler; é regra do Python. O fallback inteligente cobre isso no ciclo principal.

### 9. Testes de fumaça incluídos

Rode:

```bash
python run_tests.py
```

Ele valida:

- prazo contextual;
- documento sem extensão;
- URL relativa embutida em script/JSON;
- links em `data-url`, `onclick` e `form action`.

## Instalação sugerida

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
python run_tests.py
```

No Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
python run_tests.py
```

## Arquivos alterados/adicionados

Alterados:

- `Edital.py`
- `smart_crawler.py`
- `smart_fallback.py`
- `_pyc_bridge.py`

Adicionados:

- `requirements.txt`
- `.env.example`
- `run_tests.py`
- `tests/test_smart_crawler_hardening.py`
- `README_10_10_HARDENING.md`
- `__init__.py`

## Nota honesta

Isso deixa o projeto muito mais perto de um scraping 10/10, mas scraping literalmente 100% imortal não existe. O que existe é: fallback, auditoria, teste, detecção por múltiplas estratégias e falha explícita. Agora o projeto está bem mais nessa pegada.
