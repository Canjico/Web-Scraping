import datetime
import hashlib
import json
import logging
import os
import re
import unicodedata
from typing import Any
from urllib.parse import urlparse

import requests


logger = logging.getLogger(__name__)

DEFAULT_RESPONSES_API_VERSION = "2025-04-01-preview"
DEFAULT_RESPONSES_ENDPOINT = "https://thall-mj3g48g6-eastus2.cognitiveservices.azure.com/openai/responses"
DEFAULT_WEBCHECK_DEPLOYMENT = "gpt-5.2-chat"

_STATUS_CANONICAL = {"aberto", "encerrado", "suspenso", "revogado", "incerto"}

_STATUS_KEYWORDS = {
    "revogado": (
        "revogado",
        "revogada",
        "anulado",
        "anulada",
        "cancelado",
        "cancelada",
    ),
    "suspenso": (
        "suspenso",
        "suspensa",
        "suspensao",
        "suspensao temporaria",
        "temporariamente interrompido",
    ),
    "encerrado": (
        "encerrado",
        "encerrada",
        "fechado",
        "fechada",
        "expirado",
        "expirada",
        "finalizado",
        "finalizada",
        "prazo encerrado",
        "inscricoes encerradas",
        "concluido",
        "concluida",
    ),
    "aberto": (
        "aberto",
        "aberta",
        "vigente",
        "em andamento",
        "inscricoes abertas",
        "recebimento aberto",
    ),
}

_DEFAULT_ORGAO_DOMAIN_HINTS = {
    "FAPESP": ["fapesp.br"],
    "CNPQ": ["cnpq.br", "gov.br"],
    "FINEP": ["finep.gov.br", "gov.br"],
    "CAPES": ["capes.gov.br", "gov.br"],
    "CONFAP": ["confap.org.br"],
    "FAPESC": ["fapesc.sc.gov.br", "sc.gov.br"],
    "FAPERN": ["fapern.rn.gov.br", "rn.gov.br"],
    "FAPES": ["fapes.es.gov.br", "es.gov.br"],
    "FACEPE": ["facepe.br"],
    "FUNDECT": ["fundect.ms.gov.br", "ms.gov.br"],
    "FUNCAP": ["funcap.ce.gov.br", "ce.gov.br"],
    "FAPEG": ["fapeg.go.gov.br", "go.gov.br"],
    "FAPERO": ["fapero.ro.gov.br", "ro.gov.br"],
    "FAPESQ": ["fapesq.rpp.br", "pb.gov.br"],
    "FAPESB": ["fapesb.ba.gov.br", "ba.gov.br"],
    "FAPEAL": ["fapeal.br"],
    "FAPEAP": ["fapeap.ap.gov.br", "ap.gov.br"],
    "CRESOL": ["cresol.com.br"],
    "SENAI": ["senai.br", "portaldaindustria.com.br"],
}


def _is_truthy_env(value: Any) -> bool:
    raw = str(value or "").strip().lower()
    return raw in {"1", "true", "on", "yes", "sim"}


def is_web_check_enabled() -> bool:
    return (
        _is_truthy_env(os.getenv("ENABLE_WEB_CHECK"))
        or _is_truthy_env(os.getenv("AZURE_OPENAI_WEBSEARCH_ENABLED"))
    )


def _normalize_text(text: Any) -> str:
    return str(text or "").strip()


def _normalize_ascii_key(text: Any) -> str:
    value = unicodedata.normalize("NFKD", _normalize_text(text))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^A-Z0-9]+", " ", value.upper())
    return re.sub(r"\s+", " ", value).strip()


def _normalize_host(url_or_host: Any) -> str:
    raw = _normalize_text(url_or_host).lower()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"https://{raw}"
    try:
        parsed = urlparse(raw)
    except Exception:
        return ""
    host = (parsed.hostname or "").strip().lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _is_likely_official_domain(host: str) -> bool:
    if not host:
        return False
    return (
        host.endswith(".gov.br")
        or host.endswith(".leg.br")
        or host.endswith(".jus.br")
        or host.endswith(".edu.br")
    )


def _load_extra_domain_hints() -> dict[str, list[str]]:
    raw = _normalize_text(os.getenv("WEB_CHECK_ORGAO_DOMAINS_JSON"))
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        logger.warning("[WEB-CHECK] WEB_CHECK_ORGAO_DOMAINS_JSON invalido; ignorando.")
        return {}
    if not isinstance(parsed, dict):
        return {}

    out: dict[str, list[str]] = {}
    for key, value in parsed.items():
        norm_key = _normalize_ascii_key(key)
        if not norm_key:
            continue
        domains: list[str] = []
        if isinstance(value, list):
            for item in value:
                host = _normalize_host(item)
                if host:
                    domains.append(host)
        elif isinstance(value, str):
            host = _normalize_host(value)
            if host:
                domains.append(host)
        if domains:
            out[norm_key] = domains
    return out


def _build_domain_hints() -> dict[str, list[str]]:
    hints: dict[str, list[str]] = {}
    for key, domains in _DEFAULT_ORGAO_DOMAIN_HINTS.items():
        hints[_normalize_ascii_key(key)] = [_normalize_host(domain) for domain in domains if _normalize_host(domain)]
    for key, domains in _load_extra_domain_hints().items():
        existing = set(hints.get(key, []))
        for domain in domains:
            if domain and domain not in existing:
                hints.setdefault(key, []).append(domain)
    return hints



def _load_default_allowed_domains() -> set[str]:
    raw = _normalize_text(
        os.getenv("AZURE_OPENAI_WEBSEARCH_ALLOWED_DOMAINS_DEFAULT")
        or os.getenv("WEB_CHECK_ALLOWED_DOMAINS_DEFAULT")
    )
    if not raw:
        return set()
    domains: set[str] = set()
    for item in raw.split(','):
        host = _normalize_host(item)
        if host:
            domains.add(host)
    return domains


def _max_sources() -> int:
    raw = _normalize_text(os.getenv("AZURE_OPENAI_WEBSEARCH_MAX_SOURCES") or os.getenv("WEB_CHECK_MAX_SOURCES"))
    if not raw:
        return 8
    try:
        return max(1, min(int(raw), 20))
    except Exception:
        return 8


def _request_timeout_seconds() -> int:
    raw = _normalize_text(os.getenv("AZURE_OPENAI_WEBSEARCH_TIMEOUT") or os.getenv("WEB_CHECK_TIMEOUT"))
    if not raw:
        return 180
    try:
        return max(15, min(int(raw), 600))
    except Exception:
        return 180


def _official_domains_for_context(orgao: str, link_original: str) -> set[str]:
    domains: set[str] = set(_load_default_allowed_domains())
    hints = _build_domain_hints()
    orgao_key = _normalize_ascii_key(orgao)
    if orgao_key:
        for key, values in hints.items():
            if key and key in orgao_key:
                domains.update(values)

    link_host = _normalize_host(link_original)
    if link_host and (_is_likely_official_domain(link_host) or domains):
        domains.add(link_host)
    return {d for d in domains if d}


def _is_official_source(url: str, official_domains: set[str]) -> bool:
    host = _normalize_host(url)
    if not host:
        return False
    if _is_likely_official_domain(host):
        return True
    for domain in official_domains:
        if host == domain or host.endswith(f".{domain}"):
            return True
    return False


def _parse_date(value: Any) -> datetime.date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value

    text = _normalize_text(value)
    if not text:
        return None
    if "fluxo" in text.lower() or "indefin" in text.lower():
        return None

    iso_match = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if iso_match:
        try:
            return datetime.date(int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3)))
        except Exception:
            return None

    br_match = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b", text)
    if br_match:
        day = int(br_match.group(1))
        month = int(br_match.group(2))
        year = int(br_match.group(3))
        if year < 100:
            year = 2000 + year
        try:
            return datetime.date(year, month, day)
        except Exception:
            return None
    return None


def _to_iso_date(value: Any) -> str:
    parsed = _parse_date(value)
    return parsed.isoformat() if parsed else ""


def _normalize_status(value: Any) -> str:
    text = _normalize_text(value)
    if not text:
        return "incerto"
    key = _normalize_ascii_key(text).lower()
    if key in _STATUS_CANONICAL:
        return key

    for canonical, words in _STATUS_KEYWORDS.items():
        for word in words:
            if _normalize_ascii_key(word).lower() in key:
                return canonical
    return "incerto"


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = _normalize_text(value).lower()
    if text in {"true", "1", "sim", "yes"}:
        return True
    if text in {"false", "0", "nao", "no"}:
        return False
    return None


def _pick_first(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in payload and payload.get(key) not in (None, ""):
            return payload.get(key)
    return None


def _infer_status_from_file(dados_arquivo: dict[str, Any], data_encerramento_hint: Any = None) -> tuple[str, str]:
    if not isinstance(dados_arquivo, dict):
        dados_arquivo = {}

    explicit_status = _pick_first(
        dados_arquivo,
        (
            "status_final",
            "status",
            "situacao",
            "situacao_atual",
            "status_atual",
            "situacao_edital",
        ),
    )
    normalized_explicit = _normalize_status(explicit_status)
    if normalized_explicit != "incerto":
        return normalized_explicit, f"status_explicito:{_normalize_text(explicit_status)}"

    fluxo_value = _pick_first(dados_arquivo, ("fluxo_continuo", "fluxo"))
    if _as_bool(fluxo_value) is True:
        return "aberto", "fluxo_continuo=true"

    data_limite = _pick_first(
        dados_arquivo,
        (
            "data_final_submissao",
            "data_encerramento",
            "prazo_final",
            "data_limite",
        ),
    )
    if not data_limite:
        data_limite = data_encerramento_hint

    parsed = _parse_date(data_limite)
    if parsed:
        today = datetime.date.today()
        return ("aberto", f"prazo:{parsed.isoformat()}>=hoje") if parsed >= today else ("encerrado", f"prazo:{parsed.isoformat()}<hoje")

    aprovado = _as_bool(_pick_first(dados_arquivo, ("aprovado",)))
    if aprovado is True:
        return "aberto", "aprovado=true_sem_prazo"

    motivo = _normalize_text(_pick_first(dados_arquivo, ("motivo_aprovacao", "motivo", "justificativa")))
    motivo_status = _normalize_status(motivo)
    if motivo_status != "incerto":
        return motivo_status, "motivo_aprovacao"

    return "incerto", "sem_sinal_suficiente"


def _infer_status_from_text(text: str) -> str:
    key = _normalize_ascii_key(text).lower()
    if not key:
        return "incerto"
    for canonical in ("revogado", "suspenso", "encerrado", "aberto"):
        for token in _STATUS_KEYWORDS.get(canonical, ()):
            if _normalize_ascii_key(token).lower() in key:
                return canonical
    return "incerto"


def _build_query(titulo: str, numero_edital: str, orgao: str, data_referencia: Any = None) -> str:
    terms: list[str] = []
    clean_titulo = _normalize_text(titulo)
    if clean_titulo:
        terms.append(f"\"{clean_titulo}\"")
    terms.append("edital")
    if numero_edital:
        terms.append(f"\"{_normalize_text(numero_edital)}\"")
    if orgao:
        terms.append(f"\"{_normalize_text(orgao)}\"")

    year = ""
    parsed_date = _parse_date(data_referencia)
    if parsed_date:
        year = str(parsed_date.year)
    else:
        y_match = re.search(r"\b(20\d{2})\b", _normalize_text(data_referencia))
        if y_match:
            year = y_match.group(1)
    if year:
        terms.append(year)

    terms.extend(["chamada publica", "prorrogacao", "errata", "revogacao", "encerramento"])

    seen: set[str] = set()
    ordered: list[str] = []
    for term in terms:
        token = _normalize_text(term)
        if not token:
            continue
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(token)
    return " ".join(ordered).strip()


def _build_prompt(
    *,
    query_principal: str,
    titulo: str,
    numero_edital: str,
    orgao: str,
    link_original: str,
    status_arquivo: str,
    data_encerramento_arquivo: str,
    dados_arquivo: dict[str, Any],
    official_domains: set[str],
) -> str:
    domains_text = ", ".join(sorted(official_domains)) if official_domains else "nao mapeado"
    dados_json = json.dumps(dados_arquivo or {}, ensure_ascii=False)
    return f"""
Voce e a segunda etapa de validacao chamada Web Check.

Objetivo:
- Nao substituir a analise principal.
- Pesquisar principalmente pelo NOME do edital e validar dados publicos atuais.
- Priorizar fontes oficiais.

Regras obrigatorias:
1) Use principalmente esta query principal: {query_principal}
2) Em caso de conflito:
   - STATUS e PRAZO atual: web oficial vence.
   - Conteudo detalhado, regras, anexos e texto integral: arquivo local vence.
3) Se nao houver fonte oficial suficiente, sinalize baixa confianca e marque revisao.
4) Reporte divergencias com clareza.
5) Responda APENAS com JSON valido (sem markdown).

Dominios oficiais prioritarios para este caso: {domains_text}

Dados recebidos do arquivo principal:
- titulo: {titulo}
- numero_edital: {numero_edital}
- orgao_ou_fonte: {orgao}
- link_original: {link_original}
- status_arquivo: {status_arquivo}
- data_encerramento_arquivo: {data_encerramento_arquivo}
- dados_extraidos_arquivo_json: {dados_json}

Retorne JSON com este formato:
{{
  "titulo_oficial": "",
  "numero_edital": "",
  "orgao": "",
  "data_publicacao": "",
  "data_encerramento": "",
  "situacao_atual": "aberto|encerrado|suspenso|revogado|incerto",
  "status_confirmado_web": "",
  "bate_com_arquivo": true,
  "divergencias": [],
  "confianca_web": 0.0,
  "resumo_web": "",
  "fontes": [
    {{"titulo": "", "url": "", "oficial": true}}
  ]
}}
""".strip()


def _resolve_api_key() -> str:
    return (
        _normalize_text(os.getenv("AZURE_OPENAI_WEBCHECK_API_KEY"))
        or _normalize_text(os.getenv("AZURE_OPENAI_API_KEY"))
        or _normalize_text(os.getenv("AZURE_OPENAI_KEY"))
        or _normalize_text(os.getenv("AZURE_API_KEY"))
    )


def _resolve_responses_endpoint() -> str:
    explicit = (
        _normalize_text(os.getenv("AZURE_OPENAI_WEBCHECK_ENDPOINT"))
        or _normalize_text(os.getenv("AZURE_OPENAI_RESPONSES_ENDPOINT"))
    )
    if explicit:
        endpoint = explicit.rstrip("/")
    else:
        endpoint = _normalize_text(os.getenv("AZURE_OPENAI_ENDPOINT"))
        if not endpoint:
            endpoint = _normalize_text(os.getenv("AZURE_ENDPOINT"))
        if not endpoint:
            endpoint = DEFAULT_RESPONSES_ENDPOINT
        endpoint = endpoint.rstrip("/")

    if endpoint.endswith("/openai/responses"):
        return endpoint
    if endpoint.endswith("/openai"):
        return f"{endpoint}/responses"
    return f"{endpoint}/openai/responses"


def _resolve_responses_api_version() -> str:
    return (
        _normalize_text(os.getenv("AZURE_OPENAI_WEBCHECK_API_VERSION"))
        or _normalize_text(os.getenv("AZURE_OPENAI_RESPONSES_API_VERSION"))
        or DEFAULT_RESPONSES_API_VERSION
    )


def _resolve_deployment() -> str:
    return (
        _normalize_text(os.getenv("AZURE_OPENAI_WEBCHECK_DEPLOYMENT"))
        or _normalize_text(os.getenv("AZURE_OPENAI_DEPLOYMENT"))
        or DEFAULT_WEBCHECK_DEPLOYMENT
    )


def _extract_output_text(response_json: dict[str, Any]) -> str:
    top = response_json.get("output_text")
    if isinstance(top, str) and top.strip():
        return top.strip()

    chunks: list[str] = []
    output_items = response_json.get("output")
    if isinstance(output_items, list):
        for item in output_items:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    chunks.append(text.strip())
    return "\n".join(chunks).strip()


def _extract_json_from_text(raw_text: str) -> dict[str, Any]:
    if not raw_text:
        return {}
    text = raw_text.strip()
    fenced = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
    for candidate in (fenced, text):
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue

    start = text.find("{")
    while start >= 0:
        depth = 0
        for idx in range(start, len(text)):
            char = text[idx]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    snippet = text[start : idx + 1]
                    try:
                        parsed = json.loads(snippet)
                        if isinstance(parsed, dict):
                            return parsed
                    except Exception:
                        break
        start = text.find("{", start + 1)
    return {}


def _extract_sources_from_response(response_json: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    output_items = response_json.get("output")
    if not isinstance(output_items, list):
        return out

    for item in output_items:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "web_search_call":
            continue
        action = item.get("action")
        if not isinstance(action, dict):
            continue
        sources = action.get("sources")
        if not isinstance(sources, list):
            continue
        for source in sources:
            if not isinstance(source, dict):
                continue
            url = _normalize_text(source.get("url"))
            if not url:
                continue
            key = url.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "titulo": _normalize_text(source.get("title") or source.get("name")) or "Fonte web",
                    "url": url,
                }
            )
    return out


def _extract_sources_from_model_json(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_sources = payload.get("fontes")
    if raw_sources is None:
        raw_sources = payload.get("fontes_consultadas")
    if raw_sources is None:
        raw_sources = payload.get("sources")
    if not isinstance(raw_sources, list):
        return []

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_sources:
        if not isinstance(item, dict):
            continue
        url = _normalize_text(item.get("url") or item.get("link"))
        if not url:
            continue
        key = url.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "titulo": _normalize_text(item.get("titulo") or item.get("title")) or "Fonte web",
                "url": url,
                "oficial": bool(item.get("oficial")) if isinstance(item.get("oficial"), bool) else None,
            }
        )
    return out


def _merge_sources(
    api_sources: list[dict[str, Any]],
    model_sources: list[dict[str, Any]],
    official_domains: set[str],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in [*api_sources, *model_sources]:
        url = _normalize_text(source.get("url"))
        if not url:
            continue
        key = url.lower()
        if key in seen:
            continue
        seen.add(key)
        explicit = source.get("oficial")
        official = bool(explicit) if isinstance(explicit, bool) else _is_official_source(url, official_domains)
        merged.append(
            {
                "titulo": _normalize_text(source.get("titulo") or source.get("title")) or "Fonte web",
                "url": url,
                "oficial": official,
            }
        )
    return merged


def _cache_ttl_hours() -> int:
    raw = _normalize_text(os.getenv("WEB_CHECK_CACHE_TTL_HOURS"))
    if not raw:
        return 24
    try:
        ttl = int(raw)
        return max(ttl, 0)
    except Exception:
        return 24


def _build_cache_key(titulo: str, numero_edital: str, orgao: str, link_original: str) -> str:
    seed = "|".join(
        [
            _normalize_ascii_key(titulo),
            _normalize_ascii_key(numero_edital),
            _normalize_ascii_key(orgao),
            _normalize_host(link_original),
        ]
    )
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _parse_iso_datetime(value: Any) -> datetime.datetime | None:
    text = _normalize_text(value)
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.datetime.fromisoformat(normalized)
    except Exception:
        return None


def _can_use_cache(cached_web_check: dict[str, Any] | None, cache_key: str) -> bool:
    if not isinstance(cached_web_check, dict):
        return False
    if cached_web_check.get("executado") is not True:
        return False

    cache_info = cached_web_check.get("cache")
    if not isinstance(cache_info, dict):
        return False
    if _normalize_text(cache_info.get("chave")) != cache_key:
        return False

    ttl = _cache_ttl_hours()
    if ttl <= 0:
        return False

    executed_at = _parse_iso_datetime(cached_web_check.get("executado_em"))
    if not executed_at:
        return False
    if executed_at.tzinfo is None:
        executed_at = executed_at.replace(tzinfo=datetime.timezone.utc)

    now = datetime.datetime.now(datetime.timezone.utc)
    return (now - executed_at) <= datetime.timedelta(hours=ttl)


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def _default_web_check_payload(
    *,
    edital_id: int,
    query_principal: str,
    titulo: str,
    status_arquivo: str,
    data_encerramento_arquivo: str,
    cache_key: str,
) -> dict[str, Any]:
    return {
        "executado": False,
        "edital_id": int(edital_id) if edital_id else None,
        "query_principal": query_principal,
        "titulo_pesquisado": titulo,
        "titulo_web": "",
        "numero_edital_web": "",
        "orgao_web": "",
        "data_publicacao_web": "",
        "data_encerramento_web": "",
        "situacao_web": "incerto",
        "status_confirmado_web": "incerto",
        "status_extraido_arquivo": status_arquivo,
        "data_encerramento_arquivo": data_encerramento_arquivo,
        "status_final": status_arquivo if status_arquivo != "incerto" else "incerto",
        "data_encerramento_final": data_encerramento_arquivo,
        "fonte_decisao_final": "arquivo" if status_arquivo != "incerto" else "incerto",
        "fonte_prazo_final": "arquivo" if data_encerramento_arquivo else "incerto",
        "divergencia_status": False,
        "divergencia_prazo": False,
        "divergencias": [],
        "confianca_web": 0.0,
        "confianca_validacao": 0.0,
        "resumo_web": "",
        "fontes": [],
        "marcar_para_revisao": True,
        "nivel_alerta": "baixo",
        "link_original_oficial": False,
        "hierarquia_confianca": {
            "web_oficial": False,
            "link_oficial_coletado": False,
            "arquivo_local": True,
            "fonte_secundaria": False,
        },
        "erro": None,
        "executado_em": _now_iso(),
        "cache": {
            "chave": cache_key,
            "cache_hit": False,
            "ttl_horas": _cache_ttl_hours(),
        },
    }


def _call_azure_web_search(prompt: str, allowed_domains: set[str] | None = None) -> dict[str, Any]:
    api_key = _resolve_api_key()
    endpoint = _resolve_responses_endpoint()
    deployment = _resolve_deployment()
    api_version = _resolve_responses_api_version()

    if not api_key:
        raise RuntimeError("AZURE_OPENAI_WEBCHECK_API_KEY/AZURE_OPENAI_API_KEY/AZURE_OPENAI_KEY nao configurada para Web Check.")
    if not endpoint:
        raise RuntimeError("Endpoint do Responses API nao configurado para Web Check.")
    if not deployment:
        raise RuntimeError("AZURE_OPENAI_WEBCHECK_DEPLOYMENT nao configurado.")

    tool: dict[str, Any] = {"type": "web_search"}
    normalized_domains = sorted({_normalize_host(item) for item in (allowed_domains or set()) if _normalize_host(item)})
    if normalized_domains:
        tool["filters"] = {"allowed_domains": normalized_domains}

    payload = {
        "model": deployment,
        "tools": [tool],
        "tool_choice": "auto",
        "include": ["web_search_call.action.sources"],
        "input": prompt,
    }
    headers = {
        "api-key": api_key,
        "Content-Type": "application/json",
    }

    response = requests.post(
        endpoint,
        params={"api-version": api_version},
        headers=headers,
        json=payload,
        timeout=_request_timeout_seconds(),
    )
    response.raise_for_status()
    return response.json() if response.content else {}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        number = float(str(value).replace(",", ".").replace("%", "").strip())
        if number > 1.0:
            number = number / 100.0
        if number < 0:
            return default
        return min(number, 1.0)
    except Exception:
        return default


def executar_web_check(
    *,
    edital_id: int,
    titulo: str,
    numero_edital: str = "",
    orgao: str = "",
    link_original: str = "",
    data_encerramento_arquivo: Any = None,
    dados_arquivo: dict[str, Any] | None = None,
    cached_web_check: dict[str, Any] | None = None,
) -> dict[str, Any]:
    dados_arquivo = dados_arquivo if isinstance(dados_arquivo, dict) else {}
    status_arquivo, regra_status_arquivo = _infer_status_from_file(dados_arquivo, data_encerramento_arquivo)
    data_arquivo_iso = _to_iso_date(
        _pick_first(dados_arquivo, ("data_encerramento", "data_final_submissao", "prazo_final")) or data_encerramento_arquivo
    )
    query_principal = _build_query(
        titulo=titulo,
        numero_edital=numero_edital,
        orgao=orgao,
        data_referencia=data_arquivo_iso,
    )
    cache_key = _build_cache_key(titulo=titulo, numero_edital=numero_edital, orgao=orgao, link_original=link_original)

    base = _default_web_check_payload(
        edital_id=edital_id,
        query_principal=query_principal,
        titulo=_normalize_text(titulo),
        status_arquivo=status_arquivo,
        data_encerramento_arquivo=data_arquivo_iso,
        cache_key=cache_key,
    )
    base["regra_status_arquivo"] = regra_status_arquivo

    if not is_web_check_enabled():
        base["erro"] = "web_check_desabilitado"
        base["resumo_web"] = "Web Check desabilitado por ENABLE_WEB_CHECK/AZURE_OPENAI_WEBSEARCH_ENABLED."
        return base

    if _can_use_cache(cached_web_check, cache_key):
        cached_copy = dict(cached_web_check or {})
        cache_info = cached_copy.get("cache")
        if not isinstance(cache_info, dict):
            cache_info = {}
            cached_copy["cache"] = cache_info
        cache_info["cache_hit"] = True
        cache_info["chave"] = cache_key
        cache_info["ttl_horas"] = _cache_ttl_hours()
        logger.info("[WEB-CHECK] Usando cache do Web Check para edital_id=%s.", edital_id)
        return cached_copy

    official_domains = _official_domains_for_context(orgao=orgao, link_original=link_original)
    link_original_oficial = _is_official_source(link_original, official_domains) if _normalize_text(link_original) else False
    prompt = _build_prompt(
        query_principal=query_principal,
        titulo=_normalize_text(titulo),
        numero_edital=_normalize_text(numero_edital),
        orgao=_normalize_text(orgao),
        link_original=_normalize_text(link_original),
        status_arquivo=status_arquivo,
        data_encerramento_arquivo=data_arquivo_iso,
        dados_arquivo=dados_arquivo,
        official_domains=official_domains,
    )

    logger.info("[WEB-CHECK] Iniciando web search para edital_id=%s.", edital_id)
    try:
        response_json = _call_azure_web_search(prompt, allowed_domains=official_domains)
        response_text = _extract_output_text(response_json)
        model_payload = _extract_json_from_text(response_text)

        api_sources = _extract_sources_from_response(response_json)
        model_sources = _extract_sources_from_model_json(model_payload)
        fontes = _merge_sources(api_sources, model_sources, official_domains)[: _max_sources()]
        oficiais = [f for f in fontes if f.get("oficial") is True]
        secundarias = [f for f in fontes if f.get("oficial") is not True]

        titulo_web = _normalize_text(_pick_first(model_payload, ("titulo_oficial", "titulo_web", "titulo")))
        numero_web = _normalize_text(_pick_first(model_payload, ("numero_edital", "numero", "numero_web")))
        orgao_web = _normalize_text(_pick_first(model_payload, ("orgao", "instituicao", "orgao_web")))

        data_publicacao_web = _to_iso_date(_pick_first(model_payload, ("data_publicacao",)))
        data_encerramento_web = _to_iso_date(
            _pick_first(
                model_payload,
                ("data_encerramento", "data_final_submissao", "prazo_final", "data_limite"),
            )
        )

        situacao_web = _normalize_status(
            _pick_first(
                model_payload,
                (
                    "situacao_atual",
                    "situacao",
                    "status_confirmado_web",
                    "status",
                ),
            )
        )
        if situacao_web == "incerto":
            situacao_web = _infer_status_from_text(response_text)

        tem_web_oficial = len(oficiais) > 0
        tem_web_secundaria = len(secundarias) > 0
        divergencias: list[dict[str, Any]] = []

        status_final = status_arquivo if status_arquivo != "incerto" else "incerto"
        fonte_decisao = "arquivo" if status_final != "incerto" else "incerto"
        data_encerramento_final = data_arquivo_iso
        fonte_prazo_final = "arquivo" if data_encerramento_final else "incerto"
        marcar_revisao = False
        nivel_alerta = "baixo"
        divergencia_status = False
        divergencia_prazo = False

        if tem_web_oficial and situacao_web != "incerto":
            status_final = situacao_web
            fonte_decisao = "web_oficial"
            if status_arquivo != "incerto" and situacao_web != status_arquivo:
                divergencia_status = True
                divergencias.append(
                    {
                        "campo": "status",
                        "arquivo": status_arquivo,
                        "web": situacao_web,
                        "fonte_vencedora": "web_oficial",
                    }
                )
                if status_arquivo == "aberto" and situacao_web in {"encerrado", "revogado"}:
                    nivel_alerta = "alto"
                else:
                    nivel_alerta = "medio"
        elif tem_web_secundaria and situacao_web != "incerto":
            marcar_revisao = True
            if status_arquivo != "incerto" and situacao_web != status_arquivo:
                divergencia_status = True
                divergencias.append(
                    {
                        "campo": "status",
                        "arquivo": status_arquivo,
                        "web": situacao_web,
                        "fonte_vencedora": "arquivo",
                        "motivo": "web_secundaria_sem_confirmacao_oficial",
                    }
                )
                if nivel_alerta == "baixo":
                    nivel_alerta = "medio"
        else:
            marcar_revisao = True

        if tem_web_oficial and data_encerramento_web:
            if data_encerramento_final and data_encerramento_final != data_encerramento_web:
                divergencia_prazo = True
                divergencias.append(
                    {
                        "campo": "prazo",
                        "arquivo": data_encerramento_final,
                        "web": data_encerramento_web,
                        "fonte_vencedora": "web_oficial",
                    }
                )
                if nivel_alerta == "baixo":
                    nivel_alerta = "medio"
            data_encerramento_final = data_encerramento_web
            fonte_prazo_final = "web_oficial"
        elif tem_web_secundaria and data_encerramento_web:
            if data_encerramento_final and data_encerramento_final != data_encerramento_web:
                divergencia_prazo = True
                marcar_revisao = True
                divergencias.append(
                    {
                        "campo": "prazo",
                        "arquivo": data_encerramento_final,
                        "web": data_encerramento_web,
                        "fonte_vencedora": "arquivo",
                        "motivo": "web_secundaria_sem_confirmacao_oficial",
                    }
                )
                if nivel_alerta == "baixo":
                    nivel_alerta = "medio"

        bate_com_arquivo = None
        if status_arquivo != "incerto" and situacao_web != "incerto":
            bate_com_arquivo = status_arquivo == situacao_web
        elif isinstance(model_payload.get("bate_com_arquivo"), bool):
            bate_com_arquivo = bool(model_payload.get("bate_com_arquivo"))

        if tem_web_oficial:
            confianca_web = 0.85 if situacao_web != "incerto" else 0.65
        elif tem_web_secundaria:
            confianca_web = 0.35 if situacao_web != "incerto" else 0.25
        elif link_original_oficial:
            confianca_web = 0.22
        else:
            confianca_web = 0.15

        model_confidence = _safe_float(
            _pick_first(model_payload, ("confianca_web", "confianca", "confidence")),
            default=confianca_web,
        )
        confianca_web = max(confianca_web, model_confidence)
        if marcar_revisao:
            confianca_validacao = min(confianca_web, 0.5)
        else:
            confianca_validacao = min(1.0, confianca_web + 0.05)
        if not tem_web_oficial and link_original_oficial and status_final != "incerto":
            confianca_validacao = max(confianca_validacao, min(0.45, confianca_web + 0.15))

        resumo_web = _normalize_text(_pick_first(model_payload, ("resumo_web", "resumo", "sumario")))
        if not resumo_web:
            resumo_web = _normalize_text(response_text)[:1200]

        result = {
            **base,
            "executado": True,
            "titulo_web": titulo_web,
            "numero_edital_web": numero_web,
            "orgao_web": orgao_web,
            "data_publicacao_web": data_publicacao_web,
            "data_encerramento_web": data_encerramento_web,
            "situacao_web": situacao_web,
            "status_confirmado_web": situacao_web,
            "status_final": status_final,
            "data_encerramento_final": data_encerramento_final,
            "fonte_decisao_final": fonte_decisao,
            "fonte_prazo_final": fonte_prazo_final,
            "divergencia_status": divergencia_status,
            "divergencia_prazo": divergencia_prazo,
            "divergencias": divergencias,
            "confianca_web": round(float(confianca_web), 4),
            "confianca_validacao": round(float(confianca_validacao), 4),
            "resumo_web": resumo_web,
            "fontes": fontes,
            "marcar_para_revisao": bool(marcar_revisao),
            "nivel_alerta": nivel_alerta,
            "bate_com_arquivo": bate_com_arquivo,
            "link_original_oficial": bool(link_original_oficial),
            "hierarquia_confianca": {
                "web_oficial": bool(tem_web_oficial),
                "link_oficial_coletado": bool(link_original_oficial),
                "arquivo_local": True,
                "fonte_secundaria": bool(tem_web_secundaria),
            },
            "erro": None,
            "executado_em": _now_iso(),
            "cache": {
                "chave": cache_key,
                "cache_hit": False,
                "ttl_horas": _cache_ttl_hours(),
            },
        }

        if result.get("divergencia_status"):
            logger.warning(
                "[WEB-CHECK] Divergencia de status edital_id=%s arquivo=%s web=%s final=%s.",
                edital_id,
                status_arquivo,
                situacao_web,
                result.get("status_final"),
            )
        if result.get("fonte_decisao_final") == "web_oficial":
            logger.info(
                "[WEB-CHECK] Fonte web oficial venceu para edital_id=%s (status_final=%s).",
                edital_id,
                result.get("status_final"),
            )
        return result

    except Exception as exc:
        logger.error("[WEB-CHECK] Falha no web check do edital_id=%s: %s", edital_id, exc)
        base["erro"] = str(exc)
        base["resumo_web"] = "Falha controlada na validacao web."
        base["marcar_para_revisao"] = True
        base["confianca_web"] = 0.0
        base["confianca_validacao"] = 0.0
        base["executado"] = False
        base["executado_em"] = _now_iso()
        return base
