# utils.py

import os
import json
import re
import ssl
import zipfile
import hashlib
import datetime
import logging
import requests
import pyodbc
import xml.etree.ElementTree as ET

from urllib.parse import urlparse
from requests.adapters import HTTPAdapter, Retry
from urllib3.poolmanager import PoolManager
from openai import OpenAI
import PyPDF2

# Novas dependências para OCR
import pytesseract
from pdf2image import convert_from_path
from PIL import Image

import warnings
from urllib3.exceptions import InsecureRequestWarning

warnings.simplefilter("ignore", InsecureRequestWarning)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(message)s",
    datefmt="%H:%M:%S"
)

for lib in ["httpx", "httpcore", "openai", "urllib3", "azure"]:
    logging.getLogger(lib).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# ========================================================
# CONFIGURAÇÕES SEGURAS (SEM HARDCODING)
# ========================================================

AZURE_API_KEY = os.getenv("AZURE_OPENAI_KEY")
if not AZURE_API_KEY:
    logger.warning("⚠️ AZURE_OPENAI_KEY não configurada. Funcionalidades de IA falharão.")

AZURE_ENDPOINT = "https://thall-mj3g48g6-eastus2.services.ai.azure.com"
DEPLOYMENT_NAME = "gpt-5.2-chat"
API_VERSION = "2024-12-01-preview"

SQL_CONNECTION_STRING = os.getenv("SQL_CONNECTION_STRING")
if not SQL_CONNECTION_STRING:
    logger.error("❌ SQL_CONNECTION_STRING não configurada. O acesso ao banco falhará.")

def print_section(title: str):
    print(f"\n{'='*60}\n{title.center(60)}\n{'='*60}")

def print_info(msg: str):
    print(f"[INFO] {msg}")

def print_success(msg: str):
    print(f"[SUCESSO] {msg}")

def print_error(msg: str):
    print(f"[ERRO] {msg}")

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
            ssl_context=ctx
        )


def get_http_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
    adapter = TLSAdapter(max_retries=retries)

    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({
        "User-Agent": "Mozilla/5.0"
    })

    return session

def get_openai_client() -> OpenAI:
    if not AZURE_API_KEY:
        raise ValueError("AZURE_OPENAI_KEY não configurada.")
        
    return OpenAI(
        api_key=AZURE_API_KEY,
        base_url=f"{AZURE_ENDPOINT}/openai/deployments/{DEPLOYMENT_NAME}",
        default_query={"api-version": API_VERSION}
    )

def clean_json_string(texto: str) -> str:
    if "```" in texto:
        for part in texto.split("```"):
            part = part.strip()
            if part.startswith("{") or part.startswith("["):
                return part

    match = re.search(r"\{.*\}", texto, re.DOTALL)
    return match.group(0) if match else texto.strip()


def gerar_hash_id(texto: str) -> str:
    return hashlib.md5(texto.encode("utf-8")).hexdigest()


def gerar_hash_arquivo(filepath: str) -> str:
    hasher = hashlib.md5()
    try:
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
    except Exception:
        return ""

def extrair_texto_pdf(filepath: str) -> str:
    texto = ""
    try:
        with open(filepath, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                if page.extract_text():
                    texto += page.extract_text() + "\n"
    except Exception as e:
        logger.warning(f"Falha PDF: {e}")

    return texto.strip()


def extrair_texto_docx(filepath: str) -> str:
    try:
        with zipfile.ZipFile(filepath) as z:
            xml_content = z.read("word/document.xml")
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

def extrair_texto_por_url(url: str) -> str | None:
    """
    Baixa temporariamente e extrai texto de uma URL.
    """
    session = get_http_session()
    try:
        resp = session.get(url, timeout=60, verify=False)
        if resp.status_code != 200:
            return None
        
        # Cria arquivo temp
        nome = os.path.basename(urlparse(url).path) or "temp_doc.bin"
        path_temp = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"temp_{gerar_hash_id(url)}_{nome}")
        
        with open(path_temp, "wb") as f:
            f.write(resp.content)
            
        texto = extrair_texto_generico(path_temp)
        
        try:
            os.remove(path_temp)
        except:
            pass
            
        return texto
    except Exception as e:
        logger.warning(f"Erro ao extrair texto da URL {url}: {e}")
        return None


def fechar_cookies(page):
    try:
        # Seletores comuns de cookies
        seletores = [
            "#cookie-notice", ".cc-window", "#onetrust-banner-sdk", 
            "button[id*='cookie']", "button[class*='cookie']",
            "a[id*='cookie']", "div[class*='cookie']"
        ]
        for s in seletores:
            try:
                if page.locator(s).count() > 0:
                    # Tenta clicar no botão de aceitar/fechar dentro desse container
                    btn = page.locator(f"{s} button, {s} a").first
                    if btn.count() > 0:
                        btn.click(timeout=2000)
            except:
                pass
    except:
        pass

def safe_rmtree(path):
    try:
        if os.path.exists(path):
            # shutil.rmtree as vezes falha no windows se o arquivo estiver em uso
            # Implementação simples
            for root, dirs, files in os.walk(path, topdown=False):
                for name in files:
                    try: os.remove(os.path.join(root, name))
                    except: pass
                for name in dirs:
                    try: os.rmdir(os.path.join(root, name))
                    except: pass
            try: os.rmdir(path)
            except: pass
    except:
        pass

# ========================================================
# NOVAS FUNÇÕES DE MODELAGEM (RELACIONAMENTO 1:N)
# ========================================================

def obter_ou_criar_edital(titulo: str, fonte: str, url_edital: str = None) -> int:
    """
    Busca um edital pelo hash ou cria se não existir.
    Se 'url_edital' for fornecida, usa ela como base para o hash (identidade forte/estável).
    Caso contrário, usa 'fonte|titulo' (identidade fraca/instável).
    """
    
    # Estratégia de Identidade
    if url_edital:
        # Hash da URL garante que o mesmo link sempre resulte no mesmo Edital ID,
        # independente de mudanças cosméticas no título.
        hash_edital = gerar_hash_id(url_edital)
    else:
        # Fallback para identidade baseada no título
        hash_edital = gerar_hash_id(f"{fonte}|{titulo}")
    
    if not SQL_CONNECTION_STRING:
        print_error("SQL_CONNECTION_STRING ausente. Impossível salvar no banco.")
        return -1

    with pyodbc.connect(SQL_CONNECTION_STRING) as cnxn:
        cursor = cnxn.cursor()
        
        # 1. Tenta achar pelo hash (mais seguro)
        try:
            cursor.execute("SELECT id FROM editais WHERE hash_edital = ?", hash_edital)
            row = cursor.fetchone()
            if row:
                # Se já existe, retornamos o ID existente. 
                return int(row[0])
        except pyodbc.Error:
            # Se a coluna hash_edital não existir (o que seria estranho neste ponto), fallback
            pass
        
        # 2. Fallback: busca por título exato SOMENTE se não usamos URL hash
        # Se usamos URL hash e não achamos, é um edital novo, não queremos conflitar com títulos parecidos
        if not url_edital:
            try:
                cursor.execute("SELECT id FROM editais WHERE titulo = ?", titulo)
                row = cursor.fetchone()
                if row:
                    return int(row[0])
            except pyodbc.Error:
                pass
        
        # 3. Se não existe, cria novo
        # USANDO created_at e link_origem (Padrão Scraper)
        try:
            cursor.execute("""
                INSERT INTO editais (titulo, hash_edital, created_at, link_origem, json_data)
                VALUES (?, ?, GETDATE(), ?, ?)
            """, (titulo[:400], hash_edital, fonte, "{}"))

        except pyodbc.Error as e:
             logger.error(f"Erro ao criar edital: {e}")
             # Tenta fallback se colunas mudaram (retrocompatibilidade)
             cursor.execute("""
                INSERT INTO editais (titulo, hash_edital, created_at, link_origem, json_data)
                VALUES (?, ?, GETDATE(), ?, ?)
            """, (titulo[:400], hash_edital, fonte, "{}"))

        cursor.execute("SELECT @@IDENTITY")
        new_id = cursor.fetchone()[0]
        cnxn.commit()
        
        print_info(f"Novo Edital criado: ID {new_id} - {titulo[:50]}... (Hash: {hash_edital[:8]})")
        return int(new_id)

def salvar_arquivo_no_edital(edital_id, url_arquivo, caminho_local=None, caminho_temp=None, tipo="Anexo"):
    """
    Vincula um arquivo a um edital existente.
    """
    path_final = caminho_local or caminho_temp
    
    hash_url = gerar_hash_id(url_arquivo)
    nome_arquivo = os.path.basename(urlparse(url_arquivo).path) or "documento.bin"
    extensao = os.path.splitext(nome_arquivo)[1].replace(".", "").lower()

    if not SQL_CONNECTION_STRING:
        return

    with pyodbc.connect(SQL_CONNECTION_STRING) as cnxn:
        cursor = cnxn.cursor()
        
        cursor.execute("SELECT id FROM edital_arquivos WHERE hash_url = ?", hash_url)
        if cursor.fetchone():
            return # Arquivo já existe

        cursor.execute("""
            INSERT INTO edital_arquivos
            (edital_id, url_arquivo, tipo_arquivo, nome_arquivo, extensao, hash_url)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (edital_id, url_arquivo, tipo, nome_arquivo, extensao, hash_url))
        
        cnxn.commit()
        print_success(f"Arquivo anexado ao Edital {edital_id}: {nome_arquivo} ({tipo})")

def atualizar_edital_ia(edital_id: int, dados_ia: dict, link_origem: str = None):
    """
    Atualiza os metadados de um edital existente com o retorno da IA.
    Garante que a 'instituicao' no JSON corresponda ao script que originou o edital.
    """
    if not dados_ia:
        return

    if not SQL_CONNECTION_STRING:
        return

    with pyodbc.connect(SQL_CONNECTION_STRING) as cnxn:
        cursor = cnxn.cursor()
        
        # 1. Recuperar a instituição (fonte) salva na criação do edital
        # Agora buscando por 'link_origem'
        try:
            cursor.execute("SELECT link_origem FROM editais WHERE id = ?", edital_id)
            row = cursor.fetchone()
            if row and row[0]:
                # Sobrescreve a instituição no JSON com a do banco (que veio do script)
                dados_ia["instituicao"] = row[0]
        except Exception as e:
            logger.warning(f"Não foi possível sincronizar instituição para o edital {edital_id}: {e}")

        # 2. Preparar dados para update
        # Tenta pegar descrição, se falhar pega resumo
        descricao = dados_ia.get("descricao") or dados_ia.get("resumo_objetivo") or dados_ia.get("objetivo", "")
        fluxo_continuo = 1 if dados_ia.get("fluxo_continuo") else 0
        
        # Serializa o JSON já com a instituição corrigida
        json_str = json.dumps(dados_ia, ensure_ascii=False)

        data_final = dados_ia.get("data_final_submissao")
        dt_sql = None
        if isinstance(data_final, str):
            try:
                # Tenta formato YYYY-MM-DD
                dt_sql = datetime.datetime.strptime(data_final[:10], "%Y-%m-%d").date()
            except ValueError:
                pass

        # 3. Executar Update
        sql = """
            UPDATE editais 
            SET descricao=?, json_data=?, fluxo_continuo=?, data_final_submissao=?
            WHERE id=?
        """
        params = [descricao, json_str, fluxo_continuo, dt_sql, edital_id]
        
        cursor.execute(sql, params)
        cnxn.commit()
        print_success(f"Edital {edital_id} atualizado. Instituição: {dados_ia.get('instituicao')}")

def atualizar_edital_arquivos_json(edital_id: int, arquivos_json: list):
    """
    Atualiza no edital um JSON consolidado com todos os arquivos.
    Usado pela API para entrega final.
    """
    if not arquivos_json:
        return

    if not SQL_CONNECTION_STRING:
        return

    json_str = json.dumps(arquivos_json, ensure_ascii=False)

    with pyodbc.connect(SQL_CONNECTION_STRING) as cnxn:
        cursor = cnxn.cursor()
        cursor.execute(
            """
            UPDATE editais
            SET arquivos_json = ?
            WHERE id = ?
            """,
            json_str,
            edital_id
        )
        cnxn.commit()

    print_success(f"JSON de arquivos atualizado no Edital {edital_id}")


# ========================================================
# AUDITORIA & CAPTAÇÃO (NOVO)
# ========================================================

# URL do frontend para montar links no painel admin (opcional, mas útil)
FRONTEND_APP_URL = os.getenv("FRONTEND_APP_URL", "http://127.0.0.1:5500/editais.html")

# UF válidas (siglas)
UF_VALIDAS = {
    "AC","AL","AP","AM","BA","CE","DF","ES","GO","MA","MT","MS","MG","PA","PB","PR","PE","PI",
    "RJ","RN","RS","RO","RR","SC","SP","SE","TO","BR"
}

# Instituições tipicamente federais/nacionais (heurística)
INSTITUICOES_NACIONAIS = {
    "CNPQ","FINEP","CAPES","MCTI","MEC","MS","MDIC","BNDES","EMBRAPA","ANA","ANP","ANVISA","IBAMA",
    "BNB","BASA","BB","CAIXA","SERPRO","DATAPREV","FIOCRUZ"
}

def detectar_incertezas(dados_ia: dict, fonte=None) -> list[dict]:
    # Heurística simples para marcar campos onde a IA provavelmente chutou / usou fallback.
    # - Se já existir 'incertezas' no JSON, respeitamos e complementamos se necessário.
    # Retorna lista de itens: {campo, valor, motivo}

    if not isinstance(dados_ia, dict):
        return []

    incertezas: list[dict] = []

    # 1) Se a IA já entregou, usa (e garante formato)
    raw = dados_ia.get("incertezas") or dados_ia.get("campos_incertos")
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("campo"):
                incertezas.append({
                    "campo": str(item.get("campo")),
                    "valor": item.get("valor"),
                    "motivo": str(item.get("motivo") or "")
                })

    def _norm(s):
        return (s or "").strip().upper()

    inst = _norm(dados_ia.get("instituicao") or dados_ia.get("instituição"))
    fonte_norm = _norm(fonte)

    # 2) Estado
    estado = (dados_ia.get("estado") or "").strip().upper()
    if not estado:
        incertezas.append({
            "campo": "estado",
            "valor": None,
            "motivo": "UF não encontrada; campo ficou vazio"
        })
    else:
        if estado not in UF_VALIDAS:
            incertezas.append({
                "campo": "estado",
                "valor": estado,
                "motivo": "Valor fora do conjunto de UFs conhecidas (possível extração errada)"
            })
        elif estado == "BR":
            # BR pode ser correto para instituições federais. Se não parecer federal, marca.
            parece_federal = any(k in inst for k in INSTITUICOES_NACIONAIS) or any(k in fonte_norm for k in INSTITUICOES_NACIONAIS)
            if not parece_federal:
                incertezas.append({
                    "campo": "estado",
                    "valor": "BR",
                    "motivo": "UF não identificada com segurança; 'BR' parece fallback"
                })

    # 3) Data final
    data_final = dados_ia.get("data_final_submissao")
    if isinstance(data_final, str):
        df = data_final.strip()
        if df.lower() == "indefinido":
            incertezas.append({
                "campo": "data_final_submissao",
                "valor": df,
                "motivo": "Prazo não encontrado com segurança (Indefinido)"
            })
    elif data_final is None:
        incertezas.append({
            "campo": "data_final_submissao",
            "valor": None,
            "motivo": "Campo ausente (pode indicar extração incompleta)"
        })

    # 4) Valor total
    valor = dados_ia.get("valor_total")
    if isinstance(valor, str) and valor.strip().lower() == "indefinido":
        incertezas.append({
            "campo": "valor_total",
            "valor": valor,
            "motivo": "Valor total não encontrado com segurança (Indefinido)"
        })

    # Remove duplicados (campo+valor+motivo)
    seen = set()
    uniq = []
    for it in incertezas:
        key = (it.get("campo"), str(it.get("valor")), it.get("motivo"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)
    return uniq


def obter_editais_criados_desde(data_inicio: datetime.datetime) -> list[dict]:
    # Retorna editais (id, titulo, fonte, json_data) criados a partir de data_inicio.
    # Usado para montar detalhes de auditoria com incertezas.

    if not SQL_CONNECTION_STRING:
        return []

    sql = """
    SELECT id, titulo, link_origem, json_data
    FROM editais
    WHERE created_at >= ?
    """

    try:
        with pyodbc.connect(SQL_CONNECTION_STRING) as cnxn:
            cursor = cnxn.cursor()
            cursor.execute(sql, data_inicio)
            rows = cursor.fetchall()

        resultado = []
        for r in rows:
            resultado.append({
                "id": int(r[0]),
                "titulo": str(r[1] or ""),
                "fonte": str(r[2] or "Desconhecida"),
                "json_data": str(r[3] or "{}")
            })

        return resultado
    except Exception as e:
        logger.error(f"Erro ao buscar editais do ciclo: {e}")
        return []


def obter_data_sql() -> datetime.datetime:
    """
    Obtém a data/hora atual diretamente do servidor SQL.
    Evita problemas de drift de relógio entre aplicação e banco.
    """
    if not SQL_CONNECTION_STRING:
        return datetime.datetime.now()

    try:
        with pyodbc.connect(SQL_CONNECTION_STRING) as cnxn:
            cursor = cnxn.cursor()
            cursor.execute("SELECT GETDATE()")
            row = cursor.fetchone()
            return row[0] if row else datetime.datetime.now()
    except Exception as e:
        logger.error(f"Erro ao obter data do SQL: {e}")
        return datetime.datetime.now()

def obter_resumo_captacao(data_inicio: datetime.datetime) -> list:
    """
    Retorna lista de tuplas (fonte, quantidade) de editais criados APÓS a data_inicio.
    Usa created_at para filtrar.
    """
    if not SQL_CONNECTION_STRING:
        return []

    # Usando created_at
    sql = """
    SELECT link_origem, COUNT(*)
    FROM editais 
    WHERE created_at >= ? 
    GROUP BY link_origem
    """
    try:
        with pyodbc.connect(SQL_CONNECTION_STRING) as cnxn:
            cursor = cnxn.cursor()
            cursor.execute(sql, data_inicio)
            return cursor.fetchall()
    except Exception as e:
        logger.error(f"Erro ao obter resumo de captação: {e}")
        return []

def registrar_log_auditoria(ciclo: str, fonte: str, qtd: int, detalhes: dict):
    """
    Insere um registro na tabela auditoria_logs.
    """
    if not SQL_CONNECTION_STRING:
        return

    sql = """
    INSERT INTO auditoria_logs (data_execucao, ciclo, fonte, quantidade_novos, detalhes_json)
    VALUES (GETDATE(), ?, ?, ?, ?)
    """
    try:
        json_detalhes = json.dumps(detalhes, ensure_ascii=False)
        with pyodbc.connect(SQL_CONNECTION_STRING) as cnxn:
            cursor = cnxn.cursor()
            cursor.execute(sql, (ciclo, fonte, qtd, json_detalhes))
            cnxn.commit()
        print_success(f"[AUDITORIA] Log registrado: {ciclo} | {fonte} | +{qtd} editais")
    except Exception as e:
        logger.error(f"Erro ao registrar log de auditoria: {e}")

# ========================================================
# LEGADO (MANTIDO PARA RETROCOMPATIBILIDADE SE NECESSÁRIO)
# ========================================================

def salvar_no_sql_generico(dados: dict, url_arquivo: str):
    # Redireciona para o novo fluxo criando um edital "avulso" se chamado diretamente
    titulo = dados.get("titulo", "Edital Avulso")
    edital_id = obter_ou_criar_edital(titulo, "GENERICO", url_edital=url_arquivo)
    salvar_arquivo_no_edital(edital_id, url_arquivo)
    atualizar_edital_ia(edital_id, dados, url_arquivo)

def verificar_se_arquivo_existe_no_banco(caminho_arquivo: str, url_arquivo: str) -> bool:
    hash_url = gerar_hash_id(url_arquivo)
    
    if not SQL_CONNECTION_STRING:
        return False

    try:
        with pyodbc.connect(SQL_CONNECTION_STRING) as cnxn:
            cursor = cnxn.cursor()
            cursor.execute("SELECT 1 FROM edital_arquivos WHERE hash_url = ?", hash_url)
            return cursor.fetchone() is not None
    except Exception as e:
        logger.error(f"Erro BD: {e}")
        return False


# ========================================================
# OCR (NOVO RECURSO)
# ========================================================

def extrair_texto_ocr(caminho_pdf: str) -> str:
    """
    Fallback OCR para PDFs escaneados.
    """
    texto_final = []

    try:
        imagens = convert_from_path(caminho_pdf, dpi=300)

        for i, img in enumerate(imagens):
            texto = pytesseract.image_to_string(img, lang="por")
            if texto:
                texto_final.append(texto)

    except Exception as e:
        logger.warning(f"OCR falhou em {os.path.basename(caminho_pdf)}: {e}")

    return "\n".join(texto_final).strip()
