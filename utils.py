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
from typing import Any
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


def atualizar_edital_ia(edital_id: int, dados_ia: dict, link_origem: str = None):
    if not dados_ia or not SQL_CONNECTION_STRING:
        return
    try:
        with _connect_sql() as cnxn:
            cursor = cnxn.cursor()
            json_str = json.dumps(dados_ia, ensure_ascii=False)
            fluxo_continuo = 1 if dados_ia.get("fluxo_continuo") else 0
            descricao = dados_ia.get("descricao") or dados_ia.get("resumo_objetivo") or dados_ia.get("objetivo", "")
            data_final = dados_ia.get("data_final_submissao")
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
