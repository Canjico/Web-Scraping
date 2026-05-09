from __future__ import annotations

import datetime as _dt
import hashlib
import html
import json
import os
import re
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from typing import Any, Iterable
from urllib.parse import urldefrag, urljoin, urlparse, urlunparse, parse_qsl, urlencode

try:
    from . import utils  # type: ignore
    from .source_registry import SourceConfig  # type: ignore
except Exception:  # pragma: no cover
    try:
        import utils  # type: ignore
    except Exception as _utils_import_error:  # fallback leve para auditoria sem dependencias do projeto
        import logging
        import requests

        class _UtilsShim:
            logger = logging.getLogger("smart_crawler")

            @staticmethod
            def get_http_session():
                session = requests.Session()
                session.headers.update({"User-Agent": "Mozilla/5.0 SmartCrawler/1.0"})
                return session

            @staticmethod
            def deve_ignorar_ssl(url_or_host, fonte=None):
                return False

            @staticmethod
            def print_info(msg):
                print(msg)

            @staticmethod
            def print_error(msg):
                print(msg)

            @staticmethod
            def obter_ou_criar_edital(*args, **kwargs):
                _UtilsShim.logger.warning("utils.py indisponivel; persistencia desativada: %s", _utils_import_error)
                return -1

            @staticmethod
            def salvar_arquivo_no_edital(*args, **kwargs):
                return False

            @staticmethod
            def atualizar_edital_arquivos_json(*args, **kwargs):
                return 0

            @staticmethod
            def atualizar_edital_ia(*args, **kwargs):
                return None

            @staticmethod
            def extrair_texto_por_url(*args, **kwargs):
                return None

        utils = _UtilsShim()  # type: ignore
    from source_registry import SourceConfig  # type: ignore

DOCUMENT_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".zip", ".rar", ".7z", ".odt", ".ods"
}

POSITIVE_TERMS = {
    "edital": 4.0,
    "editais": 4.0,
    "chamada publica": 4.0,
    "chamada pública": 4.0,
    "chamamento publico": 3.2,
    "chamamento público": 3.2,
    "selecao publica": 3.0,
    "seleção pública": 3.0,
    "inscricoes abertas": 2.5,
    "inscrições abertas": 2.5,
    "subvencao": 2.6,
    "subvenção": 2.6,
    "fomento": 2.0,
    "propostas": 1.7,
    "proposta": 1.5,
    "regulamento": 1.8,
    "termo de referencia": 1.7,
    "termo de referência": 1.7,
    "resultado preliminar": 1.1,
    "bolsa": 1.3,
    "pesquisa": 1.0,
    "inovacao": 1.6,
    "inovação": 1.6,
    "programa": 1.1,
    "premio": 1.0,
    "prêmio": 1.0,
    "desafio": 1.0,
}

OPEN_TERMS = {
    "aberto": 2.2,
    "abertos": 2.2,
    "aberta": 2.2,
    "abertas": 2.2,
    "em andamento": 2.0,
    "vigente": 1.6,
    "inscricoes abertas": 3.0,
    "inscrições abertas": 3.0,
    "submissao ate": 1.5,
    "submissão até": 1.5,
    "prazo ate": 1.5,
    "prazo até": 1.5,
}

CLOSED_TERMS = {
    "encerrado": 3.5,
    "encerrada": 3.5,
    "encerrados": 3.5,
    "encerradas": 3.5,
    "fechado": 2.5,
    "fechada": 2.5,
    "finalizado": 2.5,
    "finalizada": 2.5,
    "resultado final": 1.4,
    "homologacao": 1.2,
    "homologação": 1.2,
}

NEGATIVE_TERMS = {
    "noticia": 2.0,
    "notícia": 2.0,
    "imprensa": 2.0,
    "transparencia": 1.6,
    "transparência": 1.6,
    "ouvidoria": 1.5,
    "contato": 1.6,
    "fale conosco": 1.8,
    "privacidade": 1.7,
    "cookies": 1.7,
    "login": 2.0,
    "facebook": 2.0,
    "instagram": 2.0,
    "linkedin": 2.0,
    "youtube": 2.0,
    "whatsapp": 1.5,
}

DATE_PATTERNS = [
    re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](20\d{2})\b"),
    re.compile(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b"),
]

URL_TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid"}


@dataclass
class LinkInfo:
    url: str
    text: str = ""
    title: str = ""
    source_url: str = ""
    depth: int = 0
    content_type: str = ""
    is_document: bool = False
    score: float = 0.0
    kind: str = "unknown"
    status_hint: str = "unknown"
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)


@dataclass
class PageSnapshot:
    url: str
    status_code: int
    content_type: str
    final_url: str
    title: str = ""
    headings: list[str] = field(default_factory=list)
    text: str = ""
    links: list[LinkInfo] = field(default_factory=list)
    html_hash: str = ""
    fetched_at: str = ""
    error: str = ""


@dataclass
class CandidateEdital:
    url: str
    title: str
    source_name: str
    kind: str
    score: float
    confidence: float
    status_hint: str
    reasons: list[str]
    html_text: str = ""
    documents: list[LinkInfo] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CrawlReport:
    source: str
    start_urls: list[str]
    pages_seen: int = 0
    pages_fetched: int = 0
    pages_failed: int = 0
    links_seen: int = 0
    documents_seen: int = 0
    candidates: list[CandidateEdital] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""


class _HTMLAnalyzer(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title_parts: list[str] = []
        self.headings: list[str] = []
        self.text_parts: list[str] = []
        self.links: list[dict[str, str]] = []
        self._tag_stack: list[str] = []
        self._current_anchor: dict[str, str] | None = None
        self._ignore_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        tag = tag.lower()
        attrs_dict = {k.lower(): (v or "") for k, v in attrs}
        self._tag_stack.append(tag)
        if tag in {"script", "style", "noscript"}:
            self._ignore_depth += 1
        if tag == "a" and attrs_dict.get("href"):
            self._current_anchor = {"href": attrs_dict.get("href", ""), "title": attrs_dict.get("title", ""), "text": ""}
        elif tag in {"iframe", "frame"} and attrs_dict.get("src"):
            self.links.append({"href": attrs_dict.get("src", ""), "title": attrs_dict.get("title", "iframe"), "text": "iframe"})
        elif tag == "link" and attrs_dict.get("href"):
            rel = attrs_dict.get("rel", "")
            if "alternate" in rel or "canonical" in rel:
                self.links.append({"href": attrs_dict.get("href", ""), "title": rel, "text": rel})

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        if tag == "a" and self._current_anchor:
            self.links.append(self._current_anchor)
            self._current_anchor = None
        if tag in {"script", "style", "noscript"} and self._ignore_depth > 0:
            self._ignore_depth -= 1
        if self._tag_stack:
            self._tag_stack.pop()

    def handle_data(self, data: str):
        if self._ignore_depth:
            return
        text = compact_text(data)
        if not text:
            return
        current = self._tag_stack[-1] if self._tag_stack else ""
        if current == "title":
            self.title_parts.append(text)
        if current in {"h1", "h2", "h3"}:
            self.headings.append(text)
        if self._current_anchor is not None:
            self._current_anchor["text"] = compact_text((self._current_anchor.get("text") or "") + " " + text)
        self.text_parts.append(text)


class SmartCrawler:
    def __init__(self, source: SourceConfig):
        self.source = source
        self.session = utils.get_http_session()
        self.max_depth = int(os.getenv("SMART_CRAWLER_MAX_DEPTH", source.max_depth or 2))
        self.max_pages = int(os.getenv("SMART_CRAWLER_MAX_PAGES", source.max_pages or 35))
        self.max_docs = int(os.getenv("SMART_CRAWLER_MAX_DOCS", 120))
        self.min_candidate_confidence = float(os.getenv("SMART_CRAWLER_MIN_CONFIDENCE", "0.62"))
        self.sleep_seconds = float(os.getenv("SMART_CRAWLER_SLEEP", "0.15"))
        self.timeout = int(os.getenv("SMART_CRAWLER_TIMEOUT", "35"))
        self.allow_external_documents = os.getenv("SMART_ALLOW_EXTERNAL_DOCUMENTS", "1") == "1"

    def crawl(self) -> CrawlReport:
        now = utc_now_iso()
        report = CrawlReport(source=self.source.name, start_urls=list(self.source.start_urls), started_at=now)
        initial_urls = self._initial_urls()
        queue: deque[tuple[str, int, str]] = deque((url, 0, "") for url in initial_urls)
        seen: set[str] = set()
        docs_by_url: dict[str, LinkInfo] = {}
        candidate_pages: dict[str, CandidateEdital] = {}
        page_docs: dict[str, list[LinkInfo]] = {}

        while queue and report.pages_fetched < self.max_pages:
            url, depth, parent = queue.popleft()
            normalized = normalize_url(url)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            report.pages_seen += 1

            if is_document_url(normalized):
                link = self.classify_link(LinkInfo(url=normalized, source_url=parent, depth=depth, is_document=True))
                if self._is_allowed_document(link.url):
                    docs_by_url[link.url] = link
                continue

            if not self._is_allowed_html(normalized):
                continue

            snapshot = self.fetch_page(normalized)
            if snapshot.error:
                report.pages_failed += 1
                report.warnings.append(f"Falha ao buscar {normalized}: {snapshot.error}")
                continue

            report.pages_fetched += 1
            page_score = self.classify_page(snapshot)
            page_link = LinkInfo(
                url=snapshot.final_url or snapshot.url,
                text=" ".join(snapshot.headings[:3]) or snapshot.title,
                title=snapshot.title,
                source_url=parent,
                depth=depth,
                content_type=snapshot.content_type,
            )
            page_link.score = page_score["score"]
            page_link.kind = page_score["kind"]
            page_link.status_hint = page_score["status_hint"]
            page_link.confidence = page_score["confidence"]
            page_link.reasons = page_score["reasons"]

            if page_link.confidence >= self.min_candidate_confidence and page_link.kind in {"edital", "listing_or_edital"}:
                candidate_pages[page_link.url] = CandidateEdital(
                    url=page_link.url,
                    title=best_title(snapshot.title, snapshot.headings, page_link.url),
                    source_name=self.source.name,
                    kind=page_link.kind,
                    score=page_link.score,
                    confidence=page_link.confidence,
                    status_hint=page_link.status_hint,
                    reasons=list(page_link.reasons),
                    html_text=snapshot.text[:30000],
                    metadata={
                        "html_hash": snapshot.html_hash,
                        "content_type": snapshot.content_type,
                        "fetched_at": snapshot.fetched_at,
                        "depth": depth,
                        "source_url": parent,
                        "datas_detectadas": [d.isoformat() for d in extract_dates(snapshot.text)],
                    },
                )

            for link in snapshot.links:
                classified = self.classify_link(link)
                report.links_seen += 1
                if classified.is_document:
                    if len(docs_by_url) < self.max_docs and self._is_allowed_document(classified.url):
                        docs_by_url[classified.url] = classified
                        page_docs.setdefault(snapshot.final_url or snapshot.url, []).append(classified)
                    continue
                if depth < self.max_depth and self._is_allowed_html(classified.url):
                    if classified.confidence >= 0.28 or classified.kind in {"listing", "edital", "listing_or_edital"}:
                        queue.append((classified.url, depth + 1, snapshot.final_url or snapshot.url))

            time.sleep(self.sleep_seconds)

        report.documents_seen = len(docs_by_url)

        # Vincula anexos por pagina de origem. Se o documento veio direto de uma listagem, cria candidato apenas se for forte.
        for cand in candidate_pages.values():
            attached = list(page_docs.get(cand.url, []))
            # Tambem anexa docs que apontam semanticamente para o titulo da pagina.
            title_key = compact_ascii(cand.title)
            for doc in docs_by_url.values():
                if doc in attached:
                    continue
                text_key = compact_ascii(f"{doc.text} {doc.title} {doc.url}")
                if title_key and title_key[:40] in text_key:
                    attached.append(doc)
            cand.documents = unique_links(attached)[:25]

        for doc in docs_by_url.values():
            if doc.confidence >= max(0.74, self.min_candidate_confidence) and doc.kind == "documento_edital":
                if not any(doc.url == c.url or any(d.url == doc.url for d in c.documents) for c in candidate_pages.values()):
                    candidate_pages[doc.url] = CandidateEdital(
                        url=doc.url,
                        title=best_title(doc.title or doc.text, [], doc.url),
                        source_name=self.source.name,
                        kind="documento_edital",
                        score=doc.score,
                        confidence=doc.confidence,
                        status_hint=doc.status_hint,
                        reasons=list(doc.reasons),
                        documents=[doc],
                        metadata={"source_url": doc.source_url, "content_type": doc.content_type, "depth": doc.depth},
                    )

        report.candidates = sorted(candidate_pages.values(), key=lambda c: (c.confidence, c.score), reverse=True)
        report.finished_at = utc_now_iso()
        if report.pages_fetched == 0:
            report.warnings.append("Nenhuma pagina HTML foi capturada; verifique bloqueio, JS pesado, SSL ou URL inicial.")
        return report

    def fetch_page(self, url: str) -> PageSnapshot:
        try:
            resp = self.session.get(url, timeout=self.timeout, verify=not utils.deve_ignorar_ssl(url, self.source.name))
            ctype = resp.headers.get("content-type", "")
            final_url = normalize_url(resp.url or url)
            if resp.status_code >= 400:
                return PageSnapshot(url=url, status_code=resp.status_code, content_type=ctype, final_url=final_url, error=f"HTTP {resp.status_code}")
            raw = resp.text or ""
            if os.getenv("SMART_USE_PLAYWRIGHT", "1") == "1" and looks_js_heavy(raw):
                rendered = render_html_with_playwright(final_url or url, timeout_ms=int(os.getenv("SMART_PLAYWRIGHT_TIMEOUT_MS", "12000")))
                if rendered and len(rendered) > len(raw):
                    raw = rendered

            parser = _HTMLAnalyzer(final_url or url)
            parser.feed(raw)
            if os.getenv("SMART_USE_PLAYWRIGHT", "1") == "1" and len(parser.links) < 3 and len(compact_text(" ".join(parser.text_parts))) < 800:
                rendered = render_html_with_playwright(final_url or url, timeout_ms=int(os.getenv("SMART_PLAYWRIGHT_TIMEOUT_MS", "12000")))
                if rendered and rendered != raw:
                    raw = rendered
                    parser = _HTMLAnalyzer(final_url or url)
                    parser.feed(raw)

            text = compact_text("\n".join(parser.text_parts))
            title = compact_text(" ".join(parser.title_parts))
            headings = [h for h in parser.headings if h]
            links: list[LinkInfo] = []
            for item in parser.links:
                href = html.unescape(item.get("href") or "").strip()
                if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
                    continue
                target = normalize_url(urljoin(final_url or url, href))
                if not target:
                    continue
                links.append(LinkInfo(
                    url=target,
                    text=compact_text(item.get("text") or ""),
                    title=compact_text(item.get("title") or ""),
                    source_url=final_url or url,
                    depth=0,
                    content_type="",
                    is_document=is_document_url(target),
                ))
            # Fallback: captura URLs literais dentro de JSON/script/atributos que o parser nao tratou.
            for embedded in re.findall(r"https?://[^\s\"'<>\)\]]+", raw):
                embedded = normalize_url(embedded)
                if embedded:
                    links.append(LinkInfo(
                        url=embedded,
                        text="url_embutida_html",
                        title="url_embutida_html",
                        source_url=final_url or url,
                        is_document=is_document_url(embedded),
                    ))

            return PageSnapshot(
                url=url,
                status_code=resp.status_code,
                content_type=ctype,
                final_url=final_url,
                title=title,
                headings=headings[:20],
                text=text[:80000],
                links=unique_links(links),
                html_hash=hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest(),
                fetched_at=utc_now_iso(),
            )
        except Exception as exc:
            return PageSnapshot(url=url, status_code=0, content_type="", final_url=url, error=str(exc))

    def classify_page(self, snapshot: PageSnapshot) -> dict[str, Any]:
        corpus = f"{snapshot.url} {snapshot.title} {' '.join(snapshot.headings[:8])} {snapshot.text[:12000]}"
        return score_corpus(corpus, is_document=False, source_keywords=self.source.keywords)

    def classify_link(self, link: LinkInfo) -> LinkInfo:
        link.url = normalize_url(link.url)
        link.is_document = link.is_document or is_document_url(link.url)
        corpus = f"{link.url} {link.text} {link.title}"
        scored = score_corpus(corpus, is_document=link.is_document, source_keywords=self.source.keywords)
        link.score = scored["score"]
        link.kind = "documento_edital" if link.is_document and scored["confidence"] >= 0.52 else scored["kind"]
        link.status_hint = scored["status_hint"]
        link.confidence = scored["confidence"]
        link.reasons = scored["reasons"]
        return link

    def _initial_urls(self) -> list[str]:
        urls: list[str] = []
        for url in self.source.start_urls:
            normalized = normalize_url(url)
            if normalized and normalized not in urls:
                urls.append(normalized)
        if os.getenv("SMART_USE_SITEMAP", "1") == "1":
            for url in self.discover_sitemap_urls():
                if url and url not in urls:
                    urls.append(url)
        return urls

    def discover_sitemap_urls(self) -> list[str]:
        if not self.source.base_url:
            return []
        base = self.source.base_url.rstrip("/")
        sitemap_candidates = [f"{base}/sitemap.xml", f"{base}/sitemap_index.xml"]
        out: list[str] = []
        max_items = int(os.getenv("SMART_SITEMAP_MAX_URLS", "80"))
        for sitemap_url in sitemap_candidates:
            try:
                resp = self.session.get(sitemap_url, timeout=min(self.timeout, 20), verify=not utils.deve_ignorar_ssl(sitemap_url, self.source.name))
                if resp.status_code >= 400 or not resp.text:
                    continue
                for loc in re.findall(r"<loc>\s*([^<]+?)\s*</loc>", resp.text, flags=re.I):
                    loc = normalize_url(html.unescape(loc))
                    if not loc or not self._is_allowed_html(loc):
                        continue
                    scored = score_corpus(loc, is_document=is_document_url(loc), source_keywords=self.source.keywords)
                    if scored["confidence"] >= 0.38 or any(x in normalize_for_match(loc) for x in ["edital", "chamada", "aberto", "inscricao", "inscricoes"]):
                        out.append(loc)
                        if len(out) >= max_items:
                            return unique_url_list(out)
            except Exception as exc:
                utils.logger.info("[SMART] Sitemap indisponivel em %s: %s", sitemap_url, exc)
        return unique_url_list(out)

    def _is_allowed_html(self, url: str) -> bool:
        if not url or is_document_url(url):
            return False
        host = host_key(url)
        if not host:
            return False
        if not self.source.allowed_domains:
            return True
        return any(host == d or host.endswith("." + d) for d in self.source.allowed_domains)

    def _is_allowed_document(self, url: str) -> bool:
        host = host_key(url)
        if not host:
            return False
        if self._is_allowed_html(url):
            return True
        if not self.allow_external_documents:
            return False
        # Documentos podem estar em storage/CDN oficial; evita redes sociais e encurtadores.
        blocked = {"facebook.com", "instagram.com", "youtube.com", "youtu.be", "twitter.com", "x.com", "linkedin.com", "wa.me"}
        return not any(host == d or host.endswith("." + d) for d in blocked)


def score_corpus(corpus: str, *, is_document: bool, source_keywords: Iterable[str] = ()) -> dict[str, Any]:
    raw = corpus or ""
    norm = normalize_for_match(raw)
    score = 0.0
    reasons: list[str] = []

    for term, weight in POSITIVE_TERMS.items():
        if normalize_for_match(term) in norm:
            score += weight
            if len(reasons) < 8:
                reasons.append(f"termo positivo: {term}")
    for term in source_keywords or []:
        if normalize_for_match(term) in norm:
            score += 1.2
            if len(reasons) < 8:
                reasons.append(f"keyword da fonte: {term}")

    open_score = 0.0
    closed_score = 0.0
    for term, weight in OPEN_TERMS.items():
        if normalize_for_match(term) in norm:
            open_score += weight
    for term, weight in CLOSED_TERMS.items():
        if normalize_for_match(term) in norm:
            closed_score += weight

    for term, weight in NEGATIVE_TERMS.items():
        if normalize_for_match(term) in norm:
            score -= weight
            if len(reasons) < 8:
                reasons.append(f"termo negativo: {term}")

    dates = extract_dates(raw)
    future_dates = [d for d in dates if d >= today_br()]
    if future_dates:
        score += 1.7
        reasons.append("data futura detectada")
    elif dates and closed_score:
        score -= 0.7

    if is_document:
        score += 1.1
        reasons.append("link/documento detectado")

    path = urlparse(extract_first_url(raw) or raw).path.lower()
    if any(piece in path for piece in ["edital", "chamada", "chamadas", "inscricoes-abertas", "abertos", "selecao"]):
        score += 2.0
        reasons.append("URL indica edital/chamada")

    if closed_score > open_score and not future_dates:
        status_hint = "encerrado"
        score -= min(closed_score, 2.0)
    elif open_score > 0 or future_dates:
        status_hint = "aberto"
        score += min(open_score, 2.5)
    else:
        status_hint = "unknown"

    if score >= 8:
        confidence = 0.95
    elif score >= 6:
        confidence = 0.86
    elif score >= 4:
        confidence = 0.70
    elif score >= 2.6:
        confidence = 0.55
    elif score >= 1.5:
        confidence = 0.38
    else:
        confidence = 0.15

    if is_document and confidence >= 0.52:
        kind = "documento_edital"
    elif confidence >= 0.70 and ("edital" in norm or "chamada" in norm or "chamamento" in norm):
        kind = "edital"
    elif confidence >= 0.50:
        kind = "listing_or_edital"
    elif any(x in norm for x in ["editais", "chamadas", "abertos", "oportunidades"]):
        kind = "listing"
    else:
        kind = "irrelevante"

    return {
        "score": round(score, 3),
        "confidence": round(confidence, 3),
        "kind": kind,
        "status_hint": status_hint,
        "reasons": reasons[:12],
    }


def persist_report(report: CrawlReport, *, use_ia: bool = True, max_candidates: int | None = None) -> dict[str, Any]:
    summary = {
        "source": report.source,
        "candidates": len(report.candidates),
        "persisted": 0,
        "skipped": 0,
        "errors": [],
        "ids": [],
    }
    limit = max_candidates or int(os.getenv("SMART_PERSIST_MAX_CANDIDATES", "25"))
    for cand in report.candidates[:limit]:
        try:
            if cand.confidence < float(os.getenv("SMART_PERSIST_MIN_CONFIDENCE", "0.66")):
                summary["skipped"] += 1
                continue
            # Segurança anti-spam: se parece encerrado e nao tem data futura, nao persiste automaticamente.
            if cand.status_hint == "encerrado" and os.getenv("SMART_PERSIST_CLOSED", "0") != "1":
                summary["skipped"] += 1
                continue
            edital_id = utils.obter_ou_criar_edital(
                titulo=cand.title,
                fonte=cand.source_name,
                url_edital=cand.url,
                chave_externa=cand.url,
            )
            if not edital_id or edital_id == -1:
                summary["skipped"] += 1
                continue

            arquivos_json: list[dict[str, Any]] = []
            for doc in cand.documents:
                utils.salvar_arquivo_no_edital(edital_id, doc.url, tipo="Anexo smart")
                arquivos_json.append({
                    "url": doc.url,
                    "nome": file_name_from_url(doc.url),
                    "tipo": "Anexo smart",
                    "extensao": file_ext(doc.url).replace(".", ""),
                    "score": doc.score,
                    "confianca": doc.confidence,
                    "origem": doc.source_url,
                    "motivos": doc.reasons,
                })
            if arquivos_json:
                utils.atualizar_edital_arquivos_json(edital_id, arquivos_json)

            dados = build_rule_based_payload(cand, report)
            if use_ia and os.getenv("SMART_USE_IA", "1") == "1":
                ia_payload = analyze_with_existing_ia(cand)
                if isinstance(ia_payload, dict) and ia_payload:
                    dados.update({k: v for k, v in ia_payload.items() if v not in (None, "", [])})
                    dados.setdefault("captura_automatica", True)
                    dados.setdefault("smart_crawler", build_smart_metadata(cand, report))
            utils.atualizar_edital_ia(edital_id, dados, link_origem=cand.url)
            summary["persisted"] += 1
            summary["ids"].append(edital_id)
        except Exception as exc:
            summary["errors"].append(f"{cand.url}: {exc}")
    return summary


def build_rule_based_payload(cand: CandidateEdital, report: CrawlReport) -> dict[str, Any]:
    dates = []
    for source in [cand.html_text, cand.title, cand.url] + [f"{d.text} {d.url}" for d in cand.documents]:
        dates.extend(extract_dates(source or ""))
    final_date = max(dates).isoformat() if dates else None
    aberto = cand.status_hint != "encerrado"
    if dates and max(dates) < today_br():
        aberto = False
    return {
        "aprovado": bool(aberto and cand.confidence >= 0.66),
        "titulo": cand.title,
        "instituicao": cand.source_name,
        "link_original": cand.url,
        "descricao": compact_text(cand.html_text[:800]) if cand.html_text else "Capturado automaticamente pelo Smart Crawler.",
        "data_final_submissao": final_date,
        "status_detectado": "aberto" if aberto else "encerrado_ou_incerto",
        "motivo_aprovacao": "; ".join(cand.reasons[:6]),
        "confianca_captura": cand.confidence,
        "captura_automatica": True,
        "smart_crawler": build_smart_metadata(cand, report),
    }


def build_smart_metadata(cand: CandidateEdital, report: CrawlReport) -> dict[str, Any]:
    return {
        "versao": "2026-05-06",
        "fonte": report.source,
        "url": cand.url,
        "kind": cand.kind,
        "score": cand.score,
        "confidence": cand.confidence,
        "status_hint": cand.status_hint,
        "motivos": cand.reasons,
        "documentos_detectados": len(cand.documents),
        "pages_fetched": report.pages_fetched,
        "links_seen": report.links_seen,
        "documents_seen": report.documents_seen,
        "metadata": cand.metadata,
    }


def analyze_with_existing_ia(cand: CandidateEdital) -> dict[str, Any] | None:
    try:
        try:
            from .IA import IAClient  # type: ignore
        except Exception:
            from IA import IAClient  # type: ignore
        client = IAClient()
    except Exception as exc:
        utils.logger.info("[SMART] IA indisponivel; usando payload rule-based. Motivo: %s", exc)
        return None

    doc_texts: list[str] = []
    max_docs_to_read = int(os.getenv("SMART_IA_MAX_DOCS_READ", "3"))
    for doc in cand.documents[:max_docs_to_read]:
        try:
            text = utils.extrair_texto_por_url(doc.url)
            if text:
                doc_texts.append(f"DOCUMENTO: {doc.url}\n{text[:12000]}")
        except Exception:
            continue
    texto = "\n\n".join([
        f"URL: {cand.url}",
        f"TITULO: {cand.title}",
        f"STATUS_HINT: {cand.status_hint}",
        f"MOTIVOS: {'; '.join(cand.reasons)}",
        cand.html_text[:18000],
        *doc_texts,
    ])[:45000]
    contexto = f"SmartCrawler: classifique se esta pagina/documento e edital aberto real da fonte {cand.source_name}."
    try:
        result = client.analisar_smart(texto_completo=texto, contexto=contexto, url=cand.url)
        return result if isinstance(result, dict) else None
    except TypeError:
        try:
            result = client.analisar_smart(texto, contexto, cand.url)
            return result if isinstance(result, dict) else None
        except Exception:
            pass
    except Exception as exc:
        utils.logger.info("[SMART] Falha em analisar_smart: %s", exc)
    try:
        result = client.analisar_edital_completo([texto], contexto=contexto, url=cand.url)
        return result if isinstance(result, dict) else None
    except Exception as exc:
        utils.logger.info("[SMART] Falha em analisar_edital_completo: %s", exc)
        return None


def report_to_dict(report: CrawlReport) -> dict[str, Any]:
    data = asdict(report)
    # Reduz payload de texto para log JSON.
    for cand in data.get("candidates", []):
        if isinstance(cand, dict) and isinstance(cand.get("html_text"), str):
            cand["html_text_preview"] = cand["html_text"][:500]
            cand.pop("html_text", None)
    return data


def looks_js_heavy(raw_html: str) -> bool:
    raw = raw_html or ""
    lowered = raw.lower()
    if len(raw) < 500:
        return True
    js_markers = ["__next_data__", "window.__", "data-reactroot", "ng-version", "id=\"app\"", "id='app'", "vue", "webpack"]
    if any(marker in lowered for marker in js_markers) and lowered.count("<a ") < 3:
        return True
    body_text = re.sub(r"<script[\s\S]*?</script>", "", lowered)
    body_text = re.sub(r"<style[\s\S]*?</style>", "", body_text)
    visible = re.sub(r"<[^>]+>", " ", body_text)
    return len(compact_text(visible)) < 300 and lowered.count("<script") >= 3


def render_html_with_playwright(url: str, timeout_ms: int = 12000) -> str:
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception:
        return ""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent="Mozilla/5.0 SmartCrawler/1.0")
            page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            try:
                utils.fechar_cookies(page)
            except Exception:
                pass
            content = page.content()
            browser.close()
            return content or ""
    except Exception as exc:
        utils.logger.info("[SMART] Playwright nao conseguiu renderizar %s: %s", url, exc)
        return ""


def unique_url_list(urls: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for url in urls:
        normalized = normalize_url(url)
        if normalized and normalized not in seen:
            seen.add(normalized)
            out.append(normalized)
    return out


def normalize_url(url: str) -> str:
    url = html.unescape(str(url or "").strip())
    if not url:
        return ""
    url, _frag = urldefrag(url)
    parsed = urlparse(url)
    if not parsed.scheme:
        return ""
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        return ""
    host = parsed.netloc.lower()
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    query_pairs = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k.lower() not in URL_TRACKING_PARAMS]
    query = urlencode(query_pairs, doseq=True)
    normalized = urlunparse((scheme, host, path, "", query, ""))
    if normalized.endswith("/") and path != "/":
        normalized = normalized[:-1]
    return normalized


def host_key(url: str) -> str:
    host = urlparse(url).netloc.lower().strip()
    return host[4:] if host.startswith("www.") else host


def file_ext(url: str) -> str:
    return os.path.splitext(urlparse(url).path.lower())[1]


def file_name_from_url(url: str) -> str:
    return os.path.basename(urlparse(url).path) or "documento"


def is_document_url(url: str) -> bool:
    return file_ext(url) in DOCUMENT_EXTENSIONS


def unique_links(links: Iterable[LinkInfo]) -> list[LinkInfo]:
    out: list[LinkInfo] = []
    seen: set[str] = set()
    for link in links:
        key = normalize_url(link.url)
        if not key or key in seen:
            continue
        seen.add(key)
        link.url = key
        out.append(link)
    return out


def compact_text(value: str) -> str:
    value = html.unescape(str(value or ""))
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def normalize_for_match(value: str) -> str:
    value = compact_text(value).lower()
    try:
        import unicodedata
        value = "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c))
    except Exception:
        pass
    return value


def compact_ascii(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", normalize_for_match(value))


def extract_first_url(value: str) -> str:
    match = re.search(r"https?://[^\s\)\]\}\"'>]+", value or "")
    return match.group(0) if match else ""


def extract_dates(value: str) -> list[_dt.date]:
    dates: list[_dt.date] = []
    for pattern in DATE_PATTERNS:
        for match in pattern.finditer(value or ""):
            try:
                if len(match.group(1)) == 4:
                    y, m, d = int(match.group(1)), int(match.group(2)), int(match.group(3))
                else:
                    d, m, y = int(match.group(1)), int(match.group(2)), int(match.group(3))
                parsed = _dt.date(y, m, d)
                if _dt.date(2020, 1, 1) <= parsed <= _dt.date(2035, 12, 31):
                    dates.append(parsed)
            except Exception:
                continue
    return sorted(set(dates))


def today_br() -> _dt.date:
    try:
        from zoneinfo import ZoneInfo
        return _dt.datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    except Exception:
        return _dt.date.today()


def utc_now_iso() -> str:
    return _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def best_title(title: str, headings: list[str], url: str) -> str:
    for item in [title, *(headings or [])]:
        clean = compact_text(item)
        if len(clean) >= 8:
            return clean[:400]
    name = file_name_from_url(url)
    name = re.sub(r"[-_]+", " ", os.path.splitext(name)[0]).strip()
    return (name or url)[:400]
