import datetime
import hashlib
import json
import logging
import os
import re
import ssl
import sys
import unicodedata
import warnings
import xml.etree.ElementTree as ET
import zipfile
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlparse

import PyPDF2
import pyodbc
import pytesseract
import requests
from dotenv import load_dotenv
from openai import OpenAI
from pdf2image import convert_from_path
from requests.adapters import HTTPAdapter, Retry
from urllib3.exceptions import InsecureRequestWarning
from urllib3.poolmanager import PoolManager


warnings.simplefilter("ignore", InsecureRequestWarning)


EMOJI_REPLACEMENTS = {
    "\u2705": "[SUCESSO]",
    "\u274C": "[ERRO]",
    "\u26A0\uFE0F": "[AVISO]",
    "\u26A0": "[AVISO]",
    "\U0001F4C4": "[INFO]",
    "\U0001F50D": "[INFO]",
    "\u26D4": "[ERRO]",
    "\U0001F9E0": "[INFO]",
    "\u23F3": "[INFO]",
    "\U0001F3C1": "[INFO]",
    "\U0001F525": "[INFO]",
    "\U0001F539": "[INFO]",
    "\U0001F512": "[INFO]",
    "\U0001F447": "[INFO]",
    "\u26A1": "[INFO]",
}


def _configure_stdio_utf8() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if not stream or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _sanitize_text(value: object) -> str:
    text = "" if value is None else str(value)
    for src, dst in EMOJI_REPLACEMENTS.items():
        text = text.replace(src, dst)
    text = text.replace("\uFE0F", "")
    return text


def _to_stream_safe_text(text: str, stream) -> str:
    encoding = getattr(stream, "encoding", None) or "cp1252"
    try:
        text.encode(encoding)
        return text
    except Exception:
        return text.encode(encoding, errors="replace").decode(encoding, errors="replace")


def _safe_write(stream, message: object) -> None:
    line = _sanitize_text(message)
    try:
        stream.write(f"{line}\n")
        stream.flush()
    except UnicodeEncodeError:
        safe_line = _to_stream_safe_text(f"{line}\n", stream)
        try:
            stream.write(safe_line)
            stream.flush()
        except Exception:
            pass
    except Exception:
        pass


class _SafeTextStream:
    def __init__(self, stream):
        self._stream = stream

    def write(self, data):
        text = _sanitize_text(data)
        try:
            return self._stream.write(text)
        except UnicodeEncodeError:
            fallback = _to_stream_safe_text(text, self._stream)
            return self._stream.write(fallback)

    def flush(self):
        try:
            return self._stream.flush()
        except Exception:
            return None

    def __getattr__(self, item):
        return getattr(self._stream, item)


class _SafeLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)
        record.msg = _sanitize_text(msg)
        record.args = ()
        return True


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - [%(levelname)s] - %(message)s",
        datefmt="%H:%M:%S",
    )
    root = logging.getLogger()
    safe_filter = _SafeLogFilter()

    for handler in list(root.handlers):
        try:
            handler.addFilter(safe_filter)
        except Exception:
            pass

        if isinstance(handler, logging.FileHandler):
            current_encoding = (getattr(handler, "encoding", "") or "").lower()
            if current_encoding != "utf-8":
                try:
                    replacement = logging.FileHandler(
                        handler.baseFilename,
                        mode=getattr(handler, "mode", "a"),
                        encoding="utf-8",
                        delay=getattr(handler, "delay", False),
                    )
                    replacement.setLevel(handler.level)
                    if handler.formatter:
                        replacement.setFormatter(handler.formatter)
                    for flt in handler.filters:
                        replacement.addFilter(flt)
                    root.removeHandler(handler)
                    root.addHandler(replacement)
                    handler.close()
                    handler = replacement
                except Exception:
                    pass

        if isinstance(handler, logging.StreamHandler):
            stream = getattr(handler, "stream", None)
            if stream and not isinstance(stream, _SafeTextStream):
                try:
                    handler.setStream(_SafeTextStream(stream))
                except Exception:
                    handler.stream = _SafeTextStream(stream)


_configure_stdio_utf8()
_configure_logging()

for lib in ("httpx", "httpcore", "openai", "urllib3", "azure"):
    logging.getLogger(lib).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

try:
    from . import web_check as _web_check_service  # type: ignore
except Exception:
    try:
        import web_check as _web_check_service  # type: ignore
    except Exception:
        _web_check_service = None

ANALISE_STATUS_APROVADO = "aprovado"
ANALISE_STATUS_REPROVADO = "reprovado"

_ANALISE_SCHEMA_READY = False

_URL_REGEX = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)
_NUMERO_EDITAL_REGEX = re.compile(
    r"(?:edital|chamada|processo|convocatoria|convocat[oó]ria)\s*(?:n[ºo°.]*)?\s*([a-z0-9][a-z0-9./_-]{1,80})",
    re.IGNORECASE,
)
_DATE_REGEX = re.compile(r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b")
_GENERIC_CONTEXT_HINTS = (
    "edital",
    "editais",
    "chamada",
    "chamadas",
    "programa",
    "programas",
    "plataforma",
    "abertos",
    "abertas",
    "fluxo",
)


def _format_status_line(prefix: str, msg: object) -> str:
    body = _sanitize_text(msg).strip()
    if body.startswith(prefix):
        return body
    return f"{prefix} {body}" if body else prefix


def print_section(title: str):
    _safe_write(
        sys.stdout,
        f"\n{'='*60}\n{_sanitize_text(title).center(60)}\n{'='*60}",
    )


def print_info(msg: str):
    _safe_write(sys.stdout, _format_status_line("[INFO]", msg))


def print_success(msg: str):
    _safe_write(sys.stdout, _format_status_line("[SUCESSO]", msg))


def print_error(msg: str):
    _safe_write(sys.stderr, _format_status_line("[ERRO]", msg))


load_dotenv()

AZURE_API_KEY = os.getenv("AZURE_OPENAI_KEY")
AZURE_ENDPOINT = "https://thall-mj3g48g6-eastus2.services.ai.azure.com"
DEPLOYMENT_NAME = "gpt-5.2-chat"
API_VERSION = "2024-12-01-preview"

SQL_CONNECTION_STRING = os.getenv("SQL_CONNECTION_STRING")
FRONTEND_APP_URL = os.getenv("FRONTEND_APP_URL", "http://127.0.0.1:5500/editais.html")

if not AZURE_API_KEY:
    logger.warning("[AVISO] AZURE_OPENAI_KEY nao configurada. Funcionalidades de IA podem falhar.")
if not SQL_CONNECTION_STRING:
    logger.error("[ERRO] SQL_CONNECTION_STRING nao configurada. O acesso ao banco pode falhar.")


def _parse_env_csv(name: str, default: str = "") -> list[str]:
    raw = os.getenv(name, default) or ""
    return [item.strip() for item in raw.split(",") if item and item.strip()]


def _normalize_key(texto: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", (texto or "").strip().upper()).strip("_")


def _normalizar_titulo_identidade(titulo: str) -> str:
    value = (titulo or "").strip()
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^A-Z0-9]+", " ", value.upper())
    return re.sub(r"\s+", " ", value).strip()


def _normalize_host(url_or_host: str) -> str:
    value = (url_or_host or "").strip().lower()
    if not value:
        return ""
    if "://" not in value:
        value = f"https://{value}"
    parsed = urlparse(value)
    host = (parsed.hostname or "").strip().lower()
    return host[4:] if host.startswith("www.") else host


SSL_BYPASS_HOSTS = {
    _normalize_host(h)
    for h in _parse_env_csv("SCRAPER_SSL_BYPASS_HOSTS", "www.ideiasparaummundomelhor25.com.br")
    if _normalize_host(h)
}

FONTE_SSL_BYPASS_HOSTS = {
    "COCA_COLA": {_normalize_host("www.ideiasparaummundomelhor25.com.br")},
}


def deve_ignorar_ssl(url_or_host: str, fonte: str | None = None) -> bool:
    host = _normalize_host(url_or_host)
    if not host:
        return False
    if host in SSL_BYPASS_HOSTS:
        return True
    if fonte:
        fonte_hosts = FONTE_SSL_BYPASS_HOSTS.get(_normalize_key(fonte), set())
        if host in fonte_hosts:
            return True
    return False


_TRACKING_QUERY_PREFIXES = ("utm_", "gclid", "fbclid", "mc_", "mkt_")


def normalizar_url_identidade(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    except Exception:
        return raw

    scheme = (parsed.scheme or "https").lower()
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]

    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    query_items = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        key_lower = (key or "").lower()
        if any(key_lower.startswith(prefix) for prefix in _TRACKING_QUERY_PREFIXES):
            continue
        query_items.append((key, value))
    query_items.sort()
    query = urlencode(query_items, doseq=True)

    normalized = f"{scheme}://{host}{path}"
    if query:
        normalized = f"{normalized}?{query}"
    return normalized


def gerar_hash_id(texto: str) -> str:
    return hashlib.md5((texto or "").encode("utf-8")).hexdigest()


def _is_unique_constraint_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "unique" in msg or "2627" in msg or "2601" in msg


def _normalize_compact(value: str) -> str:
    normalized = _normalizar_titulo_identidade(value or "")
    return normalized.replace(" ", "").strip()


def _clean_value(value: Any, max_len: int = 0) -> str:
    text = str(value or "").strip()
    if max_len and len(text) > max_len:
        return text[:max_len]
    return text


def _to_date(value: Any) -> Optional[datetime.date]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
        return value
    if isinstance(value, datetime.datetime):
        return value.date()

    raw = str(value or "").strip()
    if not raw:
        return None

    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y"):
        try:
            dt = datetime.datetime.strptime(raw[:10], fmt)
            return dt.date()
        except Exception:
            continue
    return None


def _extract_urls(*chunks: Any) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for chunk in chunks:
        text = str(chunk or "")
        for match in _URL_REGEX.findall(text):
            normalized = normalizar_url_identidade(match) or match.strip()
            if not normalized:
                continue
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(normalized)
    return out


def _looks_like_generic_context(contexto: str) -> bool:
    normalized = _normalizar_titulo_identidade(contexto or "").lower()
    if not normalized:
        return True

    words = [w for w in normalized.split(" ") if w]
    if len(words) <= 2:
        return True

    has_generic = any(h in normalized for h in _GENERIC_CONTEXT_HINTS)
    has_digits = any(ch.isdigit() for ch in normalized)
    return has_generic and not has_digits and len(words) <= 8


def _extract_title_candidate(textos: list[str], contexto: str) -> str:
    ctx = _clean_value(contexto, 500)
    if ctx and not _looks_like_generic_context(ctx):
        return ctx

    joined = "\n".join(_clean_value(t, 0) for t in textos if _clean_value(t, 0))
    if not joined:
        return ctx

    for raw_line in joined.splitlines():
        line = re.sub(r"\s+", " ", raw_line.strip(" \t:-|")).strip()
        if not line:
            continue
        if len(line) < 12 or len(line) > 260:
            continue
        low = line.lower()
        if low.startswith("http://") or low.startswith("https://"):
            continue
        if any(token in low for token in ("processando", "enviando", "fonte:", "status:", "arquivo:")):
            continue
        return line[:500]

    return ctx


def _extract_orgao_candidate(contexto: str, link_original: str) -> str:
    ctx = _clean_value(contexto, 255)
    if ctx:
        if " - " in ctx:
            left = ctx.split(" - ", 1)[0].strip()
            if left:
                return left[:255]
        return ctx[:255]

    host = _normalize_host(link_original or "")
    if host:
        return host.upper()[:255]
    return ""


def _extract_numero_edital(textos: list[str], contexto: str) -> str:
    scope = "\n".join([_clean_value(contexto, 0), *[_clean_value(t, 0) for t in textos[:3]]])
    match = _NUMERO_EDITAL_REGEX.search(scope or "")
    if not match:
        return ""
    return _clean_value(match.group(1), 120)


def _extract_data_publicacao(textos: list[str], contexto: str) -> Optional[datetime.date]:
    scope = "\n".join([_clean_value(contexto, 0), *[_clean_value(t, 0) for t in textos[:2]]])
    for raw in _DATE_REGEX.findall(scope or ""):
        parsed = _to_date(raw)
        if parsed:
            return parsed
    return None


def _textos_to_list(textos: Any) -> list[str]:
    if textos is None:
        return []
    if isinstance(textos, str):
        return [textos]
    if isinstance(textos, tuple):
        textos = list(textos)
    if isinstance(textos, list):
        out = []
        for item in textos:
            if item is None:
                continue
            text = str(item).strip()
            if text:
                out.append(text)
        return out
    text = str(textos).strip()
    return [text] if text else []


def montar_metadados_analise_ia(
    textos: Any,
    contexto: str,
    url_hint: str | None = None,
) -> dict[str, Any]:
    textos_list = _textos_to_list(textos)
    contexto_limpo = _clean_value(contexto, 255)
    urls = _extract_urls(url_hint, contexto_limpo, "\n".join(textos_list[:2]))
    link_original = urls[0] if urls else (normalizar_url_identidade(url_hint or "") or _clean_value(url_hint or "", 1000))

    titulo = _extract_title_candidate(textos_list, contexto_limpo)
    orgao = _extract_orgao_candidate(contexto_limpo, link_original)
    numero_edital = _extract_numero_edital(textos_list, contexto_limpo)
    data_publicacao = _extract_data_publicacao(textos_list, contexto_limpo)

    titulo_normalizado = _normalizar_titulo_identidade(titulo)
    orgao_normalizado = _normalizar_titulo_identidade(orgao)
    numero_normalizado = _normalize_compact(numero_edital)

    content_seed = re.sub(
        r"\s+",
        " ",
        _normalizar_titulo_identidade(" ".join(textos_list)[:24000]),
    ).strip()
    hash_conteudo = gerar_hash_id(content_seed or _clean_value(contexto_limpo, 0))

    hash_identidade_base = gerar_hash_id(
        (link_original or "") or f"{_normalize_key(orgao)}|{_normalize_key(titulo)}"
    )

    fingerprint_parts = [
        link_original.lower() if link_original else "",
        numero_normalizado,
        titulo_normalizado,
        orgao_normalizado,
        data_publicacao.isoformat() if data_publicacao else "",
        hash_conteudo,
    ]
    hash_identificacao = gerar_hash_id("|".join(fingerprint_parts))

    return {
        "fonte": contexto_limpo[:255] or orgao[:255] or "Desconhecida",
        "contexto": contexto_limpo,
        "link_original": _clean_value(link_original, 1000),
        "titulo": _clean_value(titulo, 500),
        "titulo_normalizado": _clean_value(titulo_normalizado, 500),
        "orgao": _clean_value(orgao, 255),
        "orgao_normalizado": _clean_value(orgao_normalizado, 255),
        "data_publicacao": data_publicacao,
        "data_abertura": None,
        "data_encerramento": None,
        "numero_edital": _clean_value(numero_edital, 120),
        "numero_edital_normalizado": _clean_value(numero_normalizado, 120),
        "hash_conteudo": hash_conteudo,
        "hash_identidade_base": hash_identidade_base,
        "hash_identificacao": hash_identificacao,
    }


def _parse_confianca(valor: Any) -> Optional[float]:
    if valor is None or valor == "":
        return None
    try:
        conf = float(str(valor).replace("%", "").replace(",", ".").strip())
    except Exception:
        return None
    if conf > 1.0 and conf <= 100.0:
        conf = conf / 100.0
    if conf < 0:
        return None
    if conf > 1:
        conf = 1.0
    return conf


def _extrair_confianca_ia(resultado: dict[str, Any]) -> Optional[float]:
    for key in (
        "confianca_ia",
        "confianca",
        "confidence",
        "confianca_geral",
        "score_confianca",
        "score_confidence",
    ):
        conf = _parse_confianca(resultado.get(key))
        if conf is not None:
            return conf
    return None


def _resultado_aprovado(resultado: dict[str, Any]) -> bool:
    value = resultado.get("aprovado")
    if isinstance(value, bool):
        return value
    token = str(value or "").strip().lower()
    if token in {"true", "1", "sim", "yes", "aprovado"}:
        return True
    if token in {"false", "0", "nao", "não", "no", "reprovado"}:
        return False
    status = str(resultado.get("status") or "").strip().lower()
    return status == ANALISE_STATUS_APROVADO


def _clean_resultado_ia_for_storage(resultado: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in (resultado or {}).items():
        if str(key).startswith("__"):
            continue
        clean[str(key)] = value
    return clean

class TLSAdapter(HTTPAdapter):
    def init_poolmanager(self, connections, maxsize, block=False):
        ctx = ssl.create_default_context()
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        self.poolmanager = PoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            ssl_context=ctx,
        )


def get_http_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=1, status_forcelist=(500, 502, 503, 504))
    adapter = TLSAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    return session


def get_openai_client() -> OpenAI:
    if not AZURE_API_KEY:
        raise ValueError("AZURE_OPENAI_KEY nao configurada.")
    return OpenAI(
        api_key=AZURE_API_KEY,
        base_url=f"{AZURE_ENDPOINT}/openai/deployments/{DEPLOYMENT_NAME}",
        default_query={"api-version": API_VERSION},
    )


def extrair_texto_pdf(filepath: str) -> str:
    texto = ""
    try:
        with open(filepath, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                page_text = page.extract_text() or ""
                if page_text:
                    texto += page_text + "\n"
    except Exception as e:
        logger.warning(f"Falha PDF: {e}")
    return texto.strip()


def extrair_texto_docx(filepath: str) -> str:
    try:
        with zipfile.ZipFile(filepath) as zf:
            xml_content = zf.read("word/document.xml")
        tree = ET.fromstring(xml_content)
        return "\n".join(
            elem.text for elem in tree.iter() if elem.tag.endswith("}t") and elem.text
        )
    except Exception:
        return ""


def extrair_texto_generico(filepath: str) -> str:
    ext = os.path.splitext(filepath)[1].lower()
    if ext == ".pdf":
        return extrair_texto_pdf(filepath)
    if ext == ".docx":
        return extrair_texto_docx(filepath)
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""


def extrair_texto_ocr(caminho_pdf: str) -> str:
    texto_final = []
    try:
        imagens = convert_from_path(caminho_pdf, dpi=300)
        for img in imagens:
            texto = pytesseract.image_to_string(img, lang="por")
            if texto:
                texto_final.append(texto)
    except Exception as e:
        logger.warning(f"OCR falhou em {os.path.basename(caminho_pdf)}: {e}")
    return "\n".join(texto_final).strip()


def extrair_texto_por_url(url: str) -> str | None:
    session = get_http_session()
    path_temp = None
    try:
        resp = session.get(url, timeout=60, verify=False)
        if resp.status_code != 200:
            return None
        nome = os.path.basename(urlparse(url).path) or "temp_doc.bin"
        path_temp = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"temp_{gerar_hash_id(url)}_{nome}")
        with open(path_temp, "wb") as f:
            f.write(resp.content)
        texto = extrair_texto_generico(path_temp) or ""
        if (not texto or len(texto) < 300) and nome.lower().endswith(".pdf"):
            logger.warning(f"Texto insuficiente em PDF ({nome}). Ativando OCR para {url}...")
            texto = extrair_texto_ocr(path_temp) or ""
        return texto.strip() or None
    except Exception as e:
        logger.warning(f"Erro ao extrair texto da URL {url}: {e}")
        return None
    finally:
        if path_temp and os.path.exists(path_temp):
            try:
                os.remove(path_temp)
            except Exception:
                pass


def fechar_cookies(page):
    try:
        seletores = [
            "#cookie-notice", ".cc-window", "#onetrust-banner-sdk",
            "button[id*='cookie']", "button[class*='cookie']",
            "a[id*='cookie']", "div[class*='cookie']",
        ]
        for s in seletores:
            try:
                if page.locator(s).count() > 0:
                    btn = page.locator(f"{s} button, {s} a").first
                    if btn.count() > 0:
                        btn.click(timeout=2000)
            except Exception:
                pass
    except Exception:
        pass


def safe_rmtree(path: str):
    try:
        if os.path.exists(path):
            for root, dirs, files in os.walk(path, topdown=False):
                for name in files:
                    try:
                        os.remove(os.path.join(root, name))
                    except Exception:
                        pass
                for name in dirs:
                    try:
                        os.rmdir(os.path.join(root, name))
                    except Exception:
                        pass
            try:
                os.rmdir(path)
            except Exception:
                pass
    except Exception:
        pass


def _connect_sql():
    if not SQL_CONNECTION_STRING:
        raise RuntimeError("SQL_CONNECTION_STRING ausente.")
    return pyodbc.connect(SQL_CONNECTION_STRING)


def _garantir_indices_deduplicacao() -> None:
    if not SQL_CONNECTION_STRING:
        return
    try:
        with _connect_sql() as cnxn:
            cursor = cnxn.cursor()
            cursor.execute(
                """
                IF OBJECT_ID('dbo.editais', 'U') IS NOT NULL
                   AND COL_LENGTH('dbo.editais', 'hash_edital') IS NOT NULL
                   AND NOT EXISTS (
                        SELECT 1
                        FROM sys.indexes
                        WHERE object_id = OBJECT_ID('dbo.editais')
                          AND name = 'UX_editais_hash_edital'
                   )
                BEGIN
                    BEGIN TRY
                        CREATE UNIQUE INDEX UX_editais_hash_edital
                            ON dbo.editais (hash_edital)
                            WHERE hash_edital IS NOT NULL;
                    END TRY
                    BEGIN CATCH
                    END CATCH
                END

                IF OBJECT_ID('dbo.edital_arquivos', 'U') IS NOT NULL
                   AND COL_LENGTH('dbo.edital_arquivos', 'hash_url') IS NOT NULL
                   AND NOT EXISTS (
                        SELECT 1
                        FROM sys.indexes
                        WHERE object_id = OBJECT_ID('dbo.edital_arquivos')
                          AND name = 'UX_edital_arquivos_hash_url'
                   )
                BEGIN
                    BEGIN TRY
                        CREATE UNIQUE INDEX UX_edital_arquivos_hash_url
                            ON dbo.edital_arquivos (hash_url)
                            WHERE hash_url IS NOT NULL;
                    END TRY
                    BEGIN CATCH
                    END CATCH
                END
                """
            )
            cnxn.commit()
    except Exception as e:
        logger.warning(f"[AVISO] Nao foi possivel garantir indices de deduplicacao: {e}")


def _ensure_editais_analisados_schema() -> None:
    global _ANALISE_SCHEMA_READY
    if _ANALISE_SCHEMA_READY or not SQL_CONNECTION_STRING:
        return

    try:
        with _connect_sql() as cnxn:
            cursor = cnxn.cursor()
            cursor.execute(
                """
                IF OBJECT_ID('dbo.editais_analisados', 'U') IS NULL
                BEGIN
                    CREATE TABLE dbo.editais_analisados (
                        id INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
                        fonte NVARCHAR(255) NULL,
                        link_original NVARCHAR(1000) NULL,
                        titulo NVARCHAR(500) NULL,
                        titulo_normalizado NVARCHAR(500) NULL,
                        orgao NVARCHAR(255) NULL,
                        orgao_normalizado NVARCHAR(255) NULL,
                        data_publicacao DATE NULL,
                        data_abertura DATE NULL,
                        data_encerramento DATE NULL,
                        numero_edital NVARCHAR(120) NULL,
                        numero_edital_normalizado NVARCHAR(120) NULL,
                        hash_identificacao NVARCHAR(64) NOT NULL,
                        status_analise NVARCHAR(20) NOT NULL,
                        motivo_reprovacao NVARCHAR(MAX) NULL,
                        confianca_ia FLOAT NULL,
                        dados_extraidos_json NVARCHAR(MAX) NULL,
                        edital_id_relacionado INT NULL,
                        criado_em DATETIME NOT NULL DEFAULT GETDATE(),
                        atualizado_em DATETIME NOT NULL DEFAULT GETDATE()
                    );
                END

                IF COL_LENGTH('dbo.editais_analisados', 'hash_identificacao') IS NULL
                    ALTER TABLE dbo.editais_analisados ADD hash_identificacao NVARCHAR(64) NULL;
                IF COL_LENGTH('dbo.editais_analisados', 'status_analise') IS NULL
                    ALTER TABLE dbo.editais_analisados ADD status_analise NVARCHAR(20) NULL;
                IF COL_LENGTH('dbo.editais_analisados', 'dados_extraidos_json') IS NULL
                    ALTER TABLE dbo.editais_analisados ADD dados_extraidos_json NVARCHAR(MAX) NULL;
                IF COL_LENGTH('dbo.editais_analisados', 'confianca_ia') IS NULL
                    ALTER TABLE dbo.editais_analisados ADD confianca_ia FLOAT NULL;
                IF COL_LENGTH('dbo.editais_analisados', 'edital_id_relacionado') IS NULL
                    ALTER TABLE dbo.editais_analisados ADD edital_id_relacionado INT NULL;
                IF COL_LENGTH('dbo.editais_analisados', 'criado_em') IS NULL
                    ALTER TABLE dbo.editais_analisados ADD criado_em DATETIME NULL;
                IF COL_LENGTH('dbo.editais_analisados', 'atualizado_em') IS NULL
                    ALTER TABLE dbo.editais_analisados ADD atualizado_em DATETIME NULL;

                UPDATE dbo.editais_analisados
                   SET criado_em = ISNULL(criado_em, GETDATE()),
                       atualizado_em = ISNULL(atualizado_em, GETDATE())
                 WHERE criado_em IS NULL OR atualizado_em IS NULL;

                IF EXISTS (
                    SELECT 1
                    FROM sys.columns
                    WHERE object_id = OBJECT_ID('dbo.editais_analisados')
                      AND name = 'hash_identificacao'
                      AND is_nullable = 1
                )
                BEGIN
                    UPDATE dbo.editais_analisados
                       SET hash_identificacao = CONVERT(NVARCHAR(64), HASHBYTES('SHA2_256', CONCAT('legacy-', id)), 2)
                     WHERE hash_identificacao IS NULL OR LTRIM(RTRIM(hash_identificacao)) = '';
                    BEGIN TRY
                        ALTER TABLE dbo.editais_analisados ALTER COLUMN hash_identificacao NVARCHAR(64) NOT NULL;
                    END TRY
                    BEGIN CATCH
                    END CATCH
                END

                IF EXISTS (
                    SELECT 1
                    FROM sys.columns
                    WHERE object_id = OBJECT_ID('dbo.editais_analisados')
                      AND name = 'status_analise'
                      AND is_nullable = 1
                )
                BEGIN
                    UPDATE dbo.editais_analisados
                       SET status_analise = ISNULL(status_analise, 'reprovado')
                     WHERE status_analise IS NULL;
                    BEGIN TRY
                        ALTER TABLE dbo.editais_analisados ALTER COLUMN status_analise NVARCHAR(20) NOT NULL;
                    END TRY
                    BEGIN CATCH
                    END CATCH
                END

                IF NOT EXISTS (
                    SELECT 1
                    FROM sys.indexes
                    WHERE object_id = OBJECT_ID('dbo.editais_analisados')
                      AND name = 'UX_editais_analisados_hash_identificacao'
                )
                BEGIN
                    BEGIN TRY
                        CREATE UNIQUE INDEX UX_editais_analisados_hash_identificacao
                            ON dbo.editais_analisados (hash_identificacao);
                    END TRY
                    BEGIN CATCH
                    END CATCH
                END

                IF NOT EXISTS (
                    SELECT 1
                    FROM sys.indexes
                    WHERE object_id = OBJECT_ID('dbo.editais_analisados')
                      AND name = 'IX_editais_analisados_link_original'
                )
                BEGIN
                    BEGIN TRY
                        CREATE INDEX IX_editais_analisados_link_original
                            ON dbo.editais_analisados (link_original);
                    END TRY
                    BEGIN CATCH
                    END CATCH
                END

                IF NOT EXISTS (
                    SELECT 1
                    FROM sys.indexes
                    WHERE object_id = OBJECT_ID('dbo.editais_analisados')
                      AND name = 'IX_editais_analisados_num_org_data'
                )
                BEGIN
                    BEGIN TRY
                        CREATE INDEX IX_editais_analisados_num_org_data
                            ON dbo.editais_analisados (numero_edital_normalizado, orgao_normalizado, data_publicacao);
                    END TRY
                    BEGIN CATCH
                    END CATCH
                END

                IF NOT EXISTS (
                    SELECT 1
                    FROM sys.indexes
                    WHERE object_id = OBJECT_ID('dbo.editais_analisados')
                      AND name = 'IX_editais_analisados_titulo_org_data'
                )
                BEGIN
                    BEGIN TRY
                        CREATE INDEX IX_editais_analisados_titulo_org_data
                            ON dbo.editais_analisados (titulo_normalizado, orgao_normalizado, data_publicacao);
                    END TRY
                    BEGIN CATCH
                    END CATCH
                END

                IF NOT EXISTS (
                    SELECT 1
                    FROM sys.indexes
                    WHERE object_id = OBJECT_ID('dbo.editais_analisados')
                      AND name = 'IX_editais_analisados_status'
                )
                BEGIN
                    BEGIN TRY
                        CREATE INDEX IX_editais_analisados_status
                            ON dbo.editais_analisados (status_analise);
                    END TRY
                    BEGIN CATCH
                    END CATCH
                END
                """
            )
            cnxn.commit()
        _ANALISE_SCHEMA_READY = True
    except Exception as exc:
        logger.warning(f"[AVISO] Falha ao garantir schema de editais_analisados: {exc}")


def _build_result_from_edital_row(edital_row: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    payload = {}
    raw_json = edital_row.get("json_data")
    if isinstance(raw_json, str) and raw_json.strip():
        try:
            parsed = json.loads(raw_json)
            if isinstance(parsed, dict):
                payload = parsed
        except Exception:
            payload = {}
    elif isinstance(raw_json, dict):
        payload = dict(raw_json)

    payload.setdefault("aprovado", True)
    payload.setdefault("titulo", edital_row.get("titulo") or meta.get("titulo") or "Edital")
    payload.setdefault("motivo_aprovacao", "Edital ja existente na base principal.")
    if meta.get("orgao") and not payload.get("instituicao"):
        payload["instituicao"] = meta.get("orgao")
    return payload


def _build_result_from_analysis_row(row: dict[str, Any]) -> dict[str, Any]:
    payload = {}
    raw_json = row.get("dados_extraidos_json")
    if isinstance(raw_json, str) and raw_json.strip():
        try:
            parsed = json.loads(raw_json)
            if isinstance(parsed, dict):
                payload = parsed
        except Exception:
            payload = {}

    status = str(row.get("status_analise") or ANALISE_STATUS_REPROVADO).strip().lower()
    aprovado = status == ANALISE_STATUS_APROVADO
    payload["aprovado"] = aprovado
    if row.get("titulo") and not payload.get("titulo"):
        payload["titulo"] = row.get("titulo")
    if row.get("orgao") and not payload.get("instituicao"):
        payload["instituicao"] = row.get("orgao")

    if aprovado:
        payload.setdefault("motivo_aprovacao", "Aprovado anteriormente (cache de analises).")
    else:
        payload["motivo_aprovacao"] = row.get("motivo_reprovacao") or "Reprovado anteriormente."

    return payload


def _row_to_analysis_dict(row: Any) -> Optional[dict[str, Any]]:
    if not row:
        return None
    return {
        "id": int(row[0]),
        "fonte": _clean_value(row[1], 255),
        "link_original": _clean_value(row[2], 1000),
        "titulo": _clean_value(row[3], 500),
        "titulo_normalizado": _clean_value(row[4], 500),
        "orgao": _clean_value(row[5], 255),
        "orgao_normalizado": _clean_value(row[6], 255),
        "data_publicacao": _to_date(row[7]),
        "data_abertura": _to_date(row[8]),
        "data_encerramento": _to_date(row[9]),
        "numero_edital": _clean_value(row[10], 120),
        "numero_edital_normalizado": _clean_value(row[11], 120),
        "hash_identificacao": _clean_value(row[12], 64),
        "status_analise": _clean_value(row[13], 20).lower(),
        "motivo_reprovacao": _clean_value(row[14], 0),
        "confianca_ia": _parse_confianca(row[15]),
        "dados_extraidos_json": row[16] if isinstance(row[16], str) else "",
        "edital_id_relacionado": int(row[17]) if row[17] is not None else None,
        "criado_em": row[18],
        "atualizado_em": row[19],
    }


def _select_analysis_row_by_hash(cursor, hash_identificacao: str) -> Optional[dict[str, Any]]:
    if not hash_identificacao:
        return None
    cursor.execute(
        """
        SELECT TOP 1
            id, fonte, link_original, titulo, titulo_normalizado, orgao, orgao_normalizado,
            data_publicacao, data_abertura, data_encerramento,
            numero_edital, numero_edital_normalizado, hash_identificacao,
            status_analise, motivo_reprovacao, confianca_ia, dados_extraidos_json,
            edital_id_relacionado, criado_em, atualizado_em
        FROM editais_analisados
        WHERE hash_identificacao = ?
        ORDER BY atualizado_em DESC, id DESC
        """,
        hash_identificacao,
    )
    return _row_to_analysis_dict(cursor.fetchone())


def _select_analysis_row(cursor, meta: dict[str, Any]) -> Optional[dict[str, Any]]:
    by_hash = _select_analysis_row_by_hash(cursor, _clean_value(meta.get("hash_identificacao"), 64))
    if by_hash:
        return by_hash

    link_original = _clean_value(meta.get("link_original"), 1000)
    if link_original:
        cursor.execute(
            """
            SELECT TOP 1
                id, fonte, link_original, titulo, titulo_normalizado, orgao, orgao_normalizado,
                data_publicacao, data_abertura, data_encerramento,
                numero_edital, numero_edital_normalizado, hash_identificacao,
                status_analise, motivo_reprovacao, confianca_ia, dados_extraidos_json,
                edital_id_relacionado, criado_em, atualizado_em
            FROM editais_analisados
            WHERE LOWER(LTRIM(RTRIM(link_original))) = LOWER(LTRIM(RTRIM(?)))
            ORDER BY atualizado_em DESC, id DESC
            """,
            link_original,
        )
        row = _row_to_analysis_dict(cursor.fetchone())
        if row:
            return row

    numero_norm = _clean_value(meta.get("numero_edital_normalizado"), 120)
    orgao_norm = _clean_value(meta.get("orgao_normalizado"), 255)
    data_pub = _to_date(meta.get("data_publicacao"))
    if numero_norm and orgao_norm:
        if data_pub:
            cursor.execute(
                """
                SELECT TOP 1
                    id, fonte, link_original, titulo, titulo_normalizado, orgao, orgao_normalizado,
                    data_publicacao, data_abertura, data_encerramento,
                    numero_edital, numero_edital_normalizado, hash_identificacao,
                    status_analise, motivo_reprovacao, confianca_ia, dados_extraidos_json,
                    edital_id_relacionado, criado_em, atualizado_em
                FROM editais_analisados
                WHERE numero_edital_normalizado = ?
                  AND orgao_normalizado = ?
                  AND data_publicacao = ?
                ORDER BY atualizado_em DESC, id DESC
                """,
                (numero_norm, orgao_norm, data_pub),
            )
        else:
            cursor.execute(
                """
                SELECT TOP 1
                    id, fonte, link_original, titulo, titulo_normalizado, orgao, orgao_normalizado,
                    data_publicacao, data_abertura, data_encerramento,
                    numero_edital, numero_edital_normalizado, hash_identificacao,
                    status_analise, motivo_reprovacao, confianca_ia, dados_extraidos_json,
                    edital_id_relacionado, criado_em, atualizado_em
                FROM editais_analisados
                WHERE numero_edital_normalizado = ?
                  AND orgao_normalizado = ?
                ORDER BY atualizado_em DESC, id DESC
                """,
                (numero_norm, orgao_norm),
            )
        row = _row_to_analysis_dict(cursor.fetchone())
        if row:
            return row

    titulo_norm = _clean_value(meta.get("titulo_normalizado"), 500)
    if titulo_norm and orgao_norm:
        cursor.execute(
            """
            SELECT TOP 1
                id, fonte, link_original, titulo, titulo_normalizado, orgao, orgao_normalizado,
                data_publicacao, data_abertura, data_encerramento,
                numero_edital, numero_edital_normalizado, hash_identificacao,
                status_analise, motivo_reprovacao, confianca_ia, dados_extraidos_json,
                edital_id_relacionado, criado_em, atualizado_em
            FROM editais_analisados
            WHERE titulo_normalizado = ?
              AND orgao_normalizado = ?
            ORDER BY atualizado_em DESC, id DESC
            """,
            (titulo_norm, orgao_norm),
        )
        row = _row_to_analysis_dict(cursor.fetchone())
        if row:
            return row

    return None


def _select_existing_edital_by_meta(cursor, meta: dict[str, Any]) -> Optional[dict[str, Any]]:
    hash_identidade_base = _clean_value(meta.get("hash_identidade_base"), 64)
    if hash_identidade_base:
        cursor.execute(
            "SELECT TOP 1 id, titulo, link_origem, json_data FROM editais WHERE hash_edital = ? ORDER BY id DESC",
            hash_identidade_base,
        )
        row = cursor.fetchone()
        if row:
            return {
                "id": int(row[0]),
                "titulo": _clean_value(row[1], 400),
                "link_origem": _clean_value(row[2], 255),
                "json_data": row[3],
            }

    link_original = _clean_value(meta.get("link_original"), 1000)
    if link_original:
        hash_url = gerar_hash_id(link_original)
        cursor.execute(
            """
            SELECT TOP 1 e.id, e.titulo, e.link_origem, e.json_data
            FROM edital_arquivos ea
            INNER JOIN editais e ON e.id = ea.edital_id
            WHERE ea.hash_url = ?
            ORDER BY ea.id DESC
            """,
            hash_url,
        )
        row = cursor.fetchone()
        if row:
            return {
                "id": int(row[0]),
                "titulo": _clean_value(row[1], 400),
                "link_origem": _clean_value(row[2], 255),
                "json_data": row[3],
            }

    titulo = _clean_value(meta.get("titulo"), 400)
    fonte = _clean_value(meta.get("orgao"), 255) or _clean_value(meta.get("fonte"), 255)
    if titulo and fonte:
        cursor.execute(
            """
            SELECT TOP 1 id, titulo, link_origem, json_data
            FROM editais
            WHERE LOWER(LTRIM(RTRIM(titulo))) = LOWER(LTRIM(RTRIM(?)))
              AND LOWER(LTRIM(RTRIM(link_origem))) = LOWER(LTRIM(RTRIM(?)))
            ORDER BY id DESC
            """,
            (titulo, fonte),
        )
        row = cursor.fetchone()
        if row:
            return {
                "id": int(row[0]),
                "titulo": _clean_value(row[1], 400),
                "link_origem": _clean_value(row[2], 255),
                "json_data": row[3],
            }

    return None


def _merge_analysis_record(existing: Optional[dict[str, Any]], incoming: dict[str, Any]) -> dict[str, Any]:
    if not existing:
        merged = dict(incoming)
        merged.setdefault("status_analise", ANALISE_STATUS_REPROVADO)
        return merged

    merged = dict(existing)
    for key, value in incoming.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        merged[key] = value

    if str(existing.get("status_analise") or "").lower() == ANALISE_STATUS_APROVADO:
        merged["status_analise"] = ANALISE_STATUS_APROVADO
    elif str(incoming.get("status_analise") or "").lower() == ANALISE_STATUS_APROVADO:
        merged["status_analise"] = ANALISE_STATUS_APROVADO
    else:
        merged["status_analise"] = ANALISE_STATUS_REPROVADO

    if merged.get("status_analise") == ANALISE_STATUS_APROVADO:
        merged["motivo_reprovacao"] = None

    return merged


def _upsert_analysis_record(cursor, registro: dict[str, Any]) -> int:
    hash_identificacao = _clean_value(registro.get("hash_identificacao"), 64)
    if not hash_identificacao:
        hash_identificacao = gerar_hash_id(json.dumps(registro, ensure_ascii=False, sort_keys=True))
        registro["hash_identificacao"] = hash_identificacao

    existing = _select_analysis_row_by_hash(cursor, hash_identificacao)
    merged = _merge_analysis_record(existing, registro)

    payload_json = ""
    if isinstance(merged.get("dados_extraidos_json"), str):
        payload_json = merged.get("dados_extraidos_json") or ""
    else:
        payload_json = json.dumps(merged.get("dados_extraidos_json") or {}, ensure_ascii=False)

    params = (
        _clean_value(merged.get("fonte"), 255),
        _clean_value(merged.get("link_original"), 1000),
        _clean_value(merged.get("titulo"), 500),
        _clean_value(merged.get("titulo_normalizado"), 500),
        _clean_value(merged.get("orgao"), 255),
        _clean_value(merged.get("orgao_normalizado"), 255),
        _to_date(merged.get("data_publicacao")),
        _to_date(merged.get("data_abertura")),
        _to_date(merged.get("data_encerramento")),
        _clean_value(merged.get("numero_edital"), 120),
        _clean_value(merged.get("numero_edital_normalizado"), 120),
        _clean_value(merged.get("hash_identificacao"), 64),
        _clean_value(merged.get("status_analise"), 20) or ANALISE_STATUS_REPROVADO,
        _clean_value(merged.get("motivo_reprovacao"), 0) or None,
        _parse_confianca(merged.get("confianca_ia")),
        payload_json or None,
        int(merged.get("edital_id_relacionado")) if merged.get("edital_id_relacionado") else None,
    )

    if existing and existing.get("id"):
        cursor.execute(
            """
            UPDATE editais_analisados
               SET fonte = ?,
                   link_original = ?,
                   titulo = ?,
                   titulo_normalizado = ?,
                   orgao = ?,
                   orgao_normalizado = ?,
                   data_publicacao = ?,
                   data_abertura = ?,
                   data_encerramento = ?,
                   numero_edital = ?,
                   numero_edital_normalizado = ?,
                   hash_identificacao = ?,
                   status_analise = ?,
                   motivo_reprovacao = ?,
                   confianca_ia = ?,
                   dados_extraidos_json = ?,
                   edital_id_relacionado = ?,
                   atualizado_em = GETDATE()
             WHERE id = ?
            """,
            (*params, int(existing["id"])),
        )
        return int(existing["id"])

    cursor.execute(
        """
        INSERT INTO editais_analisados (
            fonte, link_original, titulo, titulo_normalizado, orgao, orgao_normalizado,
            data_publicacao, data_abertura, data_encerramento,
            numero_edital, numero_edital_normalizado,
            hash_identificacao, status_analise, motivo_reprovacao, confianca_ia,
            dados_extraidos_json, edital_id_relacionado, criado_em, atualizado_em
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, GETDATE(), GETDATE())
        """,
        params,
    )
    cursor.execute("SELECT @@IDENTITY")
    new_id = cursor.fetchone()
    return int(new_id[0]) if new_id else -1


def _build_analysis_registro(
    meta: dict[str, Any],
    resultado: dict[str, Any],
    edital_id_relacionado: Optional[int] = None,
) -> dict[str, Any]:
    clean_result = _clean_resultado_ia_for_storage(resultado)
    status_analise = ANALISE_STATUS_APROVADO if _resultado_aprovado(clean_result) else ANALISE_STATUS_REPROVADO
    motivo = _clean_value(clean_result.get("motivo_aprovacao"), 0) or None
    if status_analise == ANALISE_STATUS_APROVADO:
        motivo = None

    data_publicacao = _to_date(clean_result.get("data_publicacao") or meta.get("data_publicacao"))
    data_abertura = _to_date(clean_result.get("data_abertura") or meta.get("data_abertura"))
    data_encerramento = _to_date(
        clean_result.get("data_encerramento")
        or clean_result.get("data_final_submissao")
        or meta.get("data_encerramento")
    )
    numero_edital = _clean_value(
        clean_result.get("numero_edital")
        or clean_result.get("numero")
        or meta.get("numero_edital"),
        120,
    )
    numero_norm = _clean_value(_normalize_compact(numero_edital), 120)

    titulo = _clean_value(clean_result.get("titulo") or meta.get("titulo"), 500)
    titulo_norm = _clean_value(_normalizar_titulo_identidade(titulo), 500)
    orgao = _clean_value(
        clean_result.get("orgao")
        or clean_result.get("instituicao")
        or clean_result.get("instituição")
        or meta.get("orgao")
        or meta.get("fonte"),
        255,
    )
    orgao_norm = _clean_value(_normalizar_titulo_identidade(orgao), 255)

    confianca = _extrair_confianca_ia(clean_result)
    cache_info = clean_result.get("__analysis_cache") if isinstance(clean_result.get("__analysis_cache"), dict) else {}
    related_id = edital_id_relacionado or cache_info.get("edital_id_relacionado")

    return {
        "fonte": _clean_value(meta.get("fonte"), 255) or orgao,
        "link_original": _clean_value(meta.get("link_original"), 1000),
        "titulo": titulo,
        "titulo_normalizado": titulo_norm,
        "orgao": orgao,
        "orgao_normalizado": orgao_norm,
        "data_publicacao": data_publicacao,
        "data_abertura": data_abertura,
        "data_encerramento": data_encerramento,
        "numero_edital": numero_edital,
        "numero_edital_normalizado": numero_norm,
        "hash_identificacao": _clean_value(
            cache_info.get("hash_identificacao") or meta.get("hash_identificacao"),
            64,
        ),
        "status_analise": status_analise,
        "motivo_reprovacao": motivo,
        "confianca_ia": confianca,
        "dados_extraidos_json": clean_result,
        "edital_id_relacionado": int(related_id) if related_id else None,
    }


def registrar_resultado_analise_pos_ia(
    meta: dict[str, Any],
    resultado: dict[str, Any],
    edital_id_relacionado: Optional[int] = None,
) -> Optional[int]:
    if not SQL_CONNECTION_STRING:
        return None
    if not isinstance(resultado, dict):
        return None

    try:
        _ensure_editais_analisados_schema()
        with _connect_sql() as cnxn:
            cursor = cnxn.cursor()
            registro = _build_analysis_registro(
                meta=meta or {},
                resultado=resultado,
                edital_id_relacionado=edital_id_relacionado,
            )
            analise_id = _upsert_analysis_record(cursor, registro)
            cnxn.commit()
            return analise_id
    except Exception as exc:
        logger.error(f"Erro ao registrar resultado em editais_analisados: {exc}")
        return None


def verificar_cache_analise_pre_ia(
    textos: Any,
    contexto: str,
    url_hint: str | None = None,
) -> dict[str, Any]:
    meta = montar_metadados_analise_ia(textos=textos, contexto=contexto, url_hint=url_hint)
    if not SQL_CONNECTION_STRING:
        return {"acao": "analisar_ia", "meta": meta}

    try:
        _ensure_editais_analisados_schema()
        with _connect_sql() as cnxn:
            cursor = cnxn.cursor()

            edital_existente = _select_existing_edital_by_meta(cursor, meta)
            if edital_existente:
                resultado = _build_result_from_edital_row(edital_existente, meta)
                resultado.setdefault("__analysis_cache", {})
                resultado["__analysis_cache"].update(
                    {
                        "fonte_cache": "editais",
                        "hash_identificacao": meta.get("hash_identificacao"),
                        "edital_id_relacionado": edital_existente.get("id"),
                    }
                )
                analise_id = _upsert_analysis_record(
                    cursor,
                    _build_analysis_registro(
                        meta=meta,
                        resultado=resultado,
                        edital_id_relacionado=int(edital_existente.get("id")),
                    ),
                )
                cnxn.commit()
                resultado["__analysis_cache"]["analise_id"] = analise_id
                return {
                    "acao": "usar_cache_editais",
                    "meta": meta,
                    "resultado": resultado,
                    "edital_id": int(edital_existente.get("id")),
                    "analise_id": analise_id,
                }

            analise_existente = _select_analysis_row(cursor, meta)
            if analise_existente:
                resultado = _build_result_from_analysis_row(analise_existente)
                resultado.setdefault("__analysis_cache", {})
                resultado["__analysis_cache"].update(
                    {
                        "fonte_cache": "editais_analisados",
                        "hash_identificacao": analise_existente.get("hash_identificacao") or meta.get("hash_identificacao"),
                        "analise_id": analise_existente.get("id"),
                        "edital_id_relacionado": analise_existente.get("edital_id_relacionado"),
                    }
                )
                return {
                    "acao": "usar_cache_analisados",
                    "meta": meta,
                    "resultado": resultado,
                    "analise_id": analise_existente.get("id"),
                    "edital_id": analise_existente.get("edital_id_relacionado"),
                }

        return {"acao": "analisar_ia", "meta": meta}
    except Exception as exc:
        logger.error(f"Erro ao consultar cache de analise pre-IA: {exc}")
        return {"acao": "analisar_ia", "meta": meta}


def vincular_analise_a_edital(resultado_ia: dict[str, Any], edital_id: int) -> Optional[int]:
    if not SQL_CONNECTION_STRING or not edital_id:
        return None
    if not isinstance(resultado_ia, dict):
        return None

    cache_info = resultado_ia.get("__analysis_cache") if isinstance(resultado_ia.get("__analysis_cache"), dict) else {}
    hash_identificacao = _clean_value(cache_info.get("hash_identificacao"), 64)
    analise_id = cache_info.get("analise_id")

    try:
        _ensure_editais_analisados_schema()
        with _connect_sql() as cnxn:
            cursor = cnxn.cursor()

            if analise_id:
                cursor.execute(
                    """
                    UPDATE editais_analisados
                       SET edital_id_relacionado = ?, atualizado_em = GETDATE(), status_analise = ?
                     WHERE id = ?
                    """,
                    (int(edital_id), ANALISE_STATUS_APROVADO, int(analise_id)),
                )
                cnxn.commit()
                return int(analise_id)

            if hash_identificacao:
                cursor.execute(
                    """
                    UPDATE editais_analisados
                       SET edital_id_relacionado = ?, atualizado_em = GETDATE(), status_analise = ?
                     WHERE hash_identificacao = ?
                    """,
                    (int(edital_id), ANALISE_STATUS_APROVADO, hash_identificacao),
                )
                cnxn.commit()
                row = _select_analysis_row_by_hash(cursor, hash_identificacao)
                return int(row["id"]) if row and row.get("id") else None
    except Exception as exc:
        logger.error(f"Erro ao vincular analise ao edital {edital_id}: {exc}")
    return None


def _buscar_edital_existente(
    cursor,
    hash_edital: str,
    titulo_limpo: str,
    fonte_limpa: str,
    url_normalizada: str = "",
    chave_externa: str = "",
) -> int | None:
    cursor.execute("SELECT TOP 1 id FROM editais WHERE hash_edital = ?", hash_edital)
    row = cursor.fetchone()
    if row:
        return int(row[0])

    for raw_url in (url_normalizada, chave_externa):
        if not raw_url:
            continue
        normalized = normalizar_url_identidade(raw_url) or raw_url.strip()
        if not normalized:
            continue
        hash_url = gerar_hash_id(normalized)
        cursor.execute(
            "SELECT TOP 1 edital_id FROM edital_arquivos WHERE hash_url = ? ORDER BY id DESC",
            hash_url,
        )
        row = cursor.fetchone()
        if row:
            return int(row[0])

    cursor.execute(
        """
        SELECT TOP 1 id
        FROM editais
        WHERE LOWER(LTRIM(RTRIM(titulo))) = LOWER(LTRIM(RTRIM(?)))
          AND LOWER(LTRIM(RTRIM(link_origem))) = LOWER(LTRIM(RTRIM(?)))
        ORDER BY id DESC
        """,
        (titulo_limpo[:400], fonte_limpa[:255]),
    )
    row = cursor.fetchone()
    if row:
        return int(row[0])

    alvo = _normalizar_titulo_identidade(titulo_limpo)
    if not alvo:
        return None

    cursor.execute(
        """
        SELECT TOP 300 id, titulo
        FROM editais
        WHERE LOWER(LTRIM(RTRIM(link_origem))) = LOWER(LTRIM(RTRIM(?)))
        ORDER BY id DESC
        """,
        (fonte_limpa[:255],),
    )
    for cand in cursor.fetchall() or []:
        if _normalizar_titulo_identidade(str(cand[1] or "")) == alvo:
            return int(cand[0])

    return None


def _atualizar_hash_edital_se_vazio(cursor, edital_id: int, hash_edital: str) -> None:
    if not edital_id or not hash_edital:
        return
    try:
        cursor.execute(
            """
            UPDATE editais
            SET hash_edital = CASE
                WHEN hash_edital IS NULL OR LTRIM(RTRIM(hash_edital)) = '' THEN ?
                ELSE hash_edital
            END
            WHERE id = ?
            """,
            (hash_edital, edital_id),
        )
    except Exception:
        pass


if os.getenv("SCRAPER_ENSURE_DEDUP_INDEXES", "0") == "1":
    _garantir_indices_deduplicacao()


def obter_data_sql() -> datetime.datetime:
    if not SQL_CONNECTION_STRING:
        return datetime.datetime.now()
    try:
        with _connect_sql() as cnxn:
            cursor = cnxn.cursor()
            cursor.execute("SELECT GETDATE()")
            row = cursor.fetchone()
            return row[0] if row else datetime.datetime.now()
    except Exception as e:
        logger.error(f"Erro ao obter data do SQL: {e}")
        return datetime.datetime.now()


def obter_editais_criados_desde(data_inicio: datetime.datetime) -> list[dict]:
    if not SQL_CONNECTION_STRING:
        return []
    try:
        with _connect_sql() as cnxn:
            cursor = cnxn.cursor()
            cursor.execute(
                "SELECT id, titulo, link_origem, json_data FROM editais WHERE created_at >= ?",
                data_inicio,
            )
            rows = cursor.fetchall()
        return [
            {"id": int(r[0]), "titulo": str(r[1] or ""), "fonte": str(r[2] or ""), "json_data": str(r[3] or "{}")}
            for r in rows
        ]
    except Exception as e:
        logger.error(f"Erro ao buscar editais do ciclo: {e}")
        return []


def obter_ou_criar_edital(titulo: str, fonte: str, url_edital: str = None, chave_externa: str = None) -> int:
    titulo_limpo = (titulo or "").strip() or "Edital sem titulo"
    fonte_limpa = (fonte or "").strip() or "Desconhecida"
    url_normalizada = normalizar_url_identidade(url_edital) if url_edital else ""
    chave_identidade = (chave_externa or "").strip() or url_normalizada or f"{_normalize_key(fonte_limpa)}|{_normalize_key(titulo_limpo)}"
    hash_edital = gerar_hash_id(chave_identidade)

    if not SQL_CONNECTION_STRING:
        print_error("SQL_CONNECTION_STRING ausente. Impossivel salvar no banco.")
        return -1

    try:
        with _connect_sql() as cnxn:
            cursor = cnxn.cursor()
            try:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            except Exception:
                pass

            edital_id = _buscar_edital_existente(
                cursor=cursor,
                hash_edital=hash_edital,
                titulo_limpo=titulo_limpo,
                fonte_limpa=fonte_limpa,
                url_normalizada=url_normalizada,
                chave_externa=(chave_externa or "").strip(),
            )
            if edital_id:
                _atualizar_hash_edital_se_vazio(cursor, edital_id, hash_edital)
                cnxn.commit()
                return edital_id

            cursor.execute(
                "INSERT INTO editais (titulo, hash_edital, created_at, link_origem, json_data) VALUES (?, ?, GETDATE(), ?, ?)",
                (titulo_limpo[:400], hash_edital, fonte_limpa[:255], "{}"),
            )
            cursor.execute("SELECT @@IDENTITY")
            new_id = int(cursor.fetchone()[0])
            cnxn.commit()
            return new_id
    except Exception as e:
        if _is_unique_constraint_error(e):
            try:
                with _connect_sql() as cnxn:
                    cursor = cnxn.cursor()
                    edital_id = _buscar_edital_existente(
                        cursor=cursor,
                        hash_edital=hash_edital,
                        titulo_limpo=titulo_limpo,
                        fonte_limpa=fonte_limpa,
                        url_normalizada=url_normalizada,
                        chave_externa=(chave_externa or "").strip(),
                    )
                    if edital_id:
                        return edital_id
            except Exception:
                pass
        logger.error(f"Erro ao criar edital: {e}")
        return -1


def salvar_arquivo_no_edital(edital_id, url_arquivo, caminho_local=None, caminho_temp=None, tipo="Anexo", evitar_duplicado: bool = True):
    if not edital_id or not url_arquivo or not SQL_CONNECTION_STRING:
        return False
    url_norm = normalizar_url_identidade(url_arquivo) or str(url_arquivo).strip()
    hash_url = gerar_hash_id(url_norm)
    nome_arquivo = os.path.basename(urlparse(url_norm).path) or "documento.bin"
    extensao = os.path.splitext(nome_arquivo)[1].replace(".", "").lower()
    try:
        with _connect_sql() as cnxn:
            cursor = cnxn.cursor()
            if evitar_duplicado:
                cursor.execute(
                    "SELECT TOP 1 edital_id FROM edital_arquivos WHERE hash_url = ? ORDER BY id DESC",
                    hash_url,
                )
                row_any = cursor.fetchone()
                if row_any:
                    edital_existente = int(row_any[0])
                    if edital_existente != int(edital_id):
                        print_info(
                            f"Documento ja vinculado ao edital {edital_existente}. Ignorando vinculo duplicado no edital {edital_id}."
                        )
                        return False
                    return False
            cursor.execute(
                "INSERT INTO edital_arquivos (edital_id, url_arquivo, tipo_arquivo, nome_arquivo, extensao, hash_url) VALUES (?, ?, ?, ?, ?, ?)",
                (edital_id, url_norm, tipo, nome_arquivo, extensao, hash_url),
            )
            cnxn.commit()
            return True
    except Exception as e:
        if evitar_duplicado and _is_unique_constraint_error(e):
            return False
        logger.error(f"Erro ao salvar documento do edital {edital_id}: {e}")
        return False


def _buscar_dados_edital_para_web_check(edital_id: int) -> dict[str, Any]:
    if not edital_id or not SQL_CONNECTION_STRING:
        return {}
    try:
        with _connect_sql() as cnxn:
            cursor = cnxn.cursor()
            cursor.execute(
                "SELECT TOP 1 titulo, link_origem, json_data, data_final_submissao FROM editais WHERE id = ?",
                edital_id,
            )
            row = cursor.fetchone()
            if not row:
                return {}
            parsed_json: dict[str, Any] = {}
            if row[2]:
                try:
                    loaded = json.loads(row[2])
                    if isinstance(loaded, dict):
                        parsed_json = loaded
                except Exception:
                    parsed_json = {}
            return {
                "titulo": _clean_value(row[0], 500),
                "link_origem": _clean_value(row[1], 1000),
                "json_data": parsed_json,
                "data_final_submissao": row[3],
            }
    except Exception as exc:
        logger.warning("[WEB-CHECK] Falha ao buscar dados do edital %s: %s", edital_id, exc)
        return {}


def _persistir_web_check_no_json_data(edital_id: int, dados_ia_limpos: dict[str, Any], web_check_payload: dict[str, Any]) -> None:
    if not edital_id or not SQL_CONNECTION_STRING or not isinstance(web_check_payload, dict):
        return
    try:
        with _connect_sql() as cnxn:
            cursor = cnxn.cursor()
            merged_json = dict(dados_ia_limpos if isinstance(dados_ia_limpos, dict) else {})
            cursor.execute("SELECT TOP 1 json_data FROM editais WHERE id = ?", edital_id)
            row = cursor.fetchone()
            if row and row[0]:
                try:
                    loaded = json.loads(row[0])
                    if isinstance(loaded, dict):
                        merged_json = loaded
                except Exception:
                    pass
            merged_json["web_check"] = web_check_payload
            cursor.execute(
                "UPDATE editais SET json_data = ? WHERE id = ?",
                (json.dumps(merged_json, ensure_ascii=False), edital_id),
            )
            cnxn.commit()
    except Exception as exc:
        logger.error("[WEB-CHECK] Falha ao persistir Web Check no edital %s: %s", edital_id, exc)


def _executar_web_check_pos_persistencia(edital_id: int, dados_ia_limpos: dict[str, Any], link_origem: str | None = None) -> None:
    if not edital_id:
        return
    if _web_check_service is None:
        logger.info("[WEB-CHECK] Modulo indisponivel. Pulando edital_id=%s.", edital_id)
        return

    try:
        if not _web_check_service.is_web_check_enabled():
            logger.info("[WEB-CHECK] Pulado (ENABLE_WEB_CHECK/AZURE_OPENAI_WEBSEARCH_ENABLED desabilitado) para edital_id=%s.", edital_id)
            return
    except Exception:
        logger.info("[WEB-CHECK] Nao foi possivel validar flag de habilitacao. Pulando edital_id=%s.", edital_id)
        return

    try:
        contexto_edital = _buscar_dados_edital_para_web_check(edital_id)
        json_atual = contexto_edital.get("json_data") if isinstance(contexto_edital.get("json_data"), dict) else {}
        cached_web_check = json_atual.get("web_check") if isinstance(json_atual.get("web_check"), dict) else None

        titulo_ref = (
            _clean_value(dados_ia_limpos.get("titulo"), 500)
            or _clean_value(contexto_edital.get("titulo"), 500)
            or _clean_value(json_atual.get("titulo"), 500)
        )
        numero_ref = _clean_value(
            dados_ia_limpos.get("numero_edital")
            or json_atual.get("numero_edital")
            or "",
            120,
        )
        orgao_ref = _clean_value(
            dados_ia_limpos.get("instituicao")
            or dados_ia_limpos.get("instituição")
            or dados_ia_limpos.get("orgao")
            or json_atual.get("instituicao")
            or json_atual.get("instituição")
            or json_atual.get("orgao")
            or json_atual.get("fonte")
            or "",
            255,
        )
        link_ref = _clean_value(link_origem or contexto_edital.get("link_origem") or json_atual.get("link_original") or "", 1000)
        data_final_ref = dados_ia_limpos.get("data_final_submissao") or contexto_edital.get("data_final_submissao")

        if not titulo_ref:
            logger.info("[WEB-CHECK] Titulo ausente. Pulando edital_id=%s.", edital_id)
            return

        logger.info("[WEB-CHECK] Iniciando validacao para edital_id=%s titulo='%s'.", edital_id, titulo_ref[:120])

        web_check_payload = _web_check_service.executar_web_check(
            edital_id=edital_id,
            titulo=titulo_ref,
            numero_edital=numero_ref,
            orgao=orgao_ref,
            link_original=link_ref,
            data_encerramento_arquivo=data_final_ref,
            dados_arquivo=dados_ia_limpos,
            cached_web_check=cached_web_check,
        )

        if not isinstance(web_check_payload, dict):
            logger.warning("[WEB-CHECK] Retorno invalido para edital_id=%s. Pulando persistencia.", edital_id)
            return

        _persistir_web_check_no_json_data(edital_id=edital_id, dados_ia_limpos=dados_ia_limpos, web_check_payload=web_check_payload)

        cache_meta = web_check_payload.get("cache") if isinstance(web_check_payload.get("cache"), dict) else {}
        if cache_meta.get("cache_hit"):
            logger.info("[WEB-CHECK] Cache reaproveitado para edital_id=%s.", edital_id)
        if web_check_payload.get("divergencia_status"):
            logger.warning(
                "[WEB-CHECK] Divergencia registrada no edital_id=%s: arquivo=%s web=%s final=%s.",
                edital_id,
                web_check_payload.get("status_extraido_arquivo"),
                web_check_payload.get("status_confirmado_web"),
                web_check_payload.get("status_final"),
            )
        if str(web_check_payload.get("fonte_decisao_final") or "").strip().lower() == "web_oficial":
            logger.info("[WEB-CHECK] Fonte web oficial venceu no edital_id=%s.", edital_id)
    except Exception as exc:
        logger.error("[WEB-CHECK] Falha controlada ao executar Web Check no edital %s: %s", edital_id, exc)


def atualizar_edital_ia(edital_id: int, dados_ia: dict, link_origem: str = None):
    if not dados_ia or not SQL_CONNECTION_STRING:
        return
    try:
        dados_ia_limpos = _clean_resultado_ia_for_storage(dados_ia if isinstance(dados_ia, dict) else {})
        with _connect_sql() as cnxn:
            cursor = cnxn.cursor()
            json_str = json.dumps(dados_ia_limpos, ensure_ascii=False)
            fluxo_continuo = 1 if dados_ia_limpos.get("fluxo_continuo") else 0
            descricao = (
                dados_ia_limpos.get("descricao")
                or dados_ia_limpos.get("resumo_objetivo")
                or dados_ia_limpos.get("objetivo", "")
            )
            data_final = dados_ia_limpos.get("data_final_submissao")
            dt_sql = None
            if isinstance(data_final, str):
                try:
                    dt_sql = datetime.datetime.strptime(data_final[:10], "%Y-%m-%d").date()
                except Exception:
                    dt_sql = None
            cursor.execute(
                "UPDATE editais SET descricao=?, json_data=?, fluxo_continuo=?, data_final_submissao=? WHERE id=?",
                (descricao, json_str, fluxo_continuo, dt_sql, edital_id),
            )
            cnxn.commit()

        analise_id = vincular_analise_a_edital(dados_ia, edital_id)
        if analise_id:
            logger.info(
                "[CACHE-IA] Analise %s vinculada ao edital %s apos persistencia.",
                analise_id,
                edital_id,
            )

        _executar_web_check_pos_persistencia(
            edital_id=edital_id,
            dados_ia_limpos=dados_ia_limpos,
            link_origem=link_origem,
        )
    except Exception as e:
        logger.error(f"Erro ao atualizar edital {edital_id}: {e}")


def atualizar_edital_arquivos_json(edital_id: int, arquivos_json: list):
    if not isinstance(arquivos_json, list) or not SQL_CONNECTION_STRING:
        return 0
    try:
        with _connect_sql() as cnxn:
            cursor = cnxn.cursor()
            cursor.execute("SELECT arquivos_json FROM editais WHERE id = ?", edital_id)
            row = cursor.fetchone()
            existentes = []
            if row and row[0]:
                try:
                    parsed = json.loads(row[0])
                    if isinstance(parsed, list):
                        existentes = parsed
                except Exception:
                    existentes = []

            seen = {json.dumps(item, sort_keys=True, ensure_ascii=False) for item in existentes if isinstance(item, dict)}
            merged = list(existentes)
            novos = 0
            for item in arquivos_json:
                if not isinstance(item, dict):
                    continue
                key = json.dumps(item, sort_keys=True, ensure_ascii=False)
                if key in seen:
                    continue
                seen.add(key)
                merged.append(item)
                novos += 1

            cursor.execute("UPDATE editais SET arquivos_json = ? WHERE id = ?", (json.dumps(merged, ensure_ascii=False), edital_id))
            cnxn.commit()
            return novos
    except Exception as e:
        logger.error(f"Erro ao atualizar arquivos_json do edital {edital_id}: {e}")
        return 0


def verificar_se_arquivo_existe_no_banco(caminho_arquivo: str, url_arquivo: str) -> bool:
    if not SQL_CONNECTION_STRING:
        return False
    hash_url = gerar_hash_id(normalizar_url_identidade(url_arquivo) or url_arquivo)
    try:
        with _connect_sql() as cnxn:
            cursor = cnxn.cursor()
            cursor.execute("SELECT 1 FROM edital_arquivos WHERE hash_url = ?", hash_url)
            return cursor.fetchone() is not None
    except Exception as e:
        logger.error(f"Erro BD: {e}")
        return False


def _sanitize_payload(value: Any) -> Any:
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, list):
        return [_sanitize_payload(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_payload(v) for v in value)
    if isinstance(value, dict):
        return {_sanitize_text(k): _sanitize_payload(v) for k, v in value.items()}
    return value


def registrar_log_auditoria(ciclo: str, fonte: str, qtd: int, detalhes: dict, status: str = "sucesso"):
    if not SQL_CONNECTION_STRING:
        return
    detalhes_safe = _sanitize_payload(detalhes if isinstance(detalhes, dict) else {"detalhes": detalhes})
    detalhes_safe.setdefault("status_execucao", status)
    try:
        with _connect_sql() as cnxn:
            cursor = cnxn.cursor()
            cursor.execute(
                "INSERT INTO auditoria_logs (data_execucao, ciclo, fonte, quantidade_novos, detalhes_json) VALUES (GETDATE(), ?, ?, ?, ?)",
                (ciclo, fonte, qtd, json.dumps(detalhes_safe, ensure_ascii=False)),
            )
            cnxn.commit()
        print_success(f"[AUDITORIA] {str(status or 'sucesso').upper()}: {ciclo} | {fonte} | +{qtd} editais")
    except Exception as e:
        try:
            logger.error(f"Erro ao registrar log de auditoria: {e}")
        except Exception:
            pass
