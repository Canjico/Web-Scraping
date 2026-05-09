# Web-Scraping

Scrapers de editais com execução agendada e fallback inteligente.

## Execução principal

```bash
python Edital.py --once
```

ou, quando usado como pacote `sites`:

```bash
python -m sites.Edital --once
```

## Fallback inteligente

A camada nova está documentada em `README_SMART_CRAWLER.md`.

Teste rápido de uma fonte:

```bash
python smart_fallback.py --script FINEP.py --json
```

Persistindo candidatos confiáveis:

```bash
python smart_fallback.py --script FINEP.py --persist
```
