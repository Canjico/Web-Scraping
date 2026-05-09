from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
REGISTRY_PATH = os.path.join(PROJECT_DIR, "smart_sources.json")


@dataclass(frozen=True)
class SourceConfig:
    script: str
    name: str
    base_url: str
    start_urls: tuple[str, ...] = field(default_factory=tuple)
    allowed_domains: tuple[str, ...] = field(default_factory=tuple)
    keywords: tuple[str, ...] = field(default_factory=tuple)
    max_depth: int | None = None
    max_pages: int | None = None

    @property
    def key(self) -> str:
        return os.path.splitext(self.script)[0]


def _clean_url(value: str) -> str:
    value = str(value or "").strip()
    if value.endswith("z") and value.count("/") >= 2:
        # Alguns literais extraidos de .pyc vieram com um byte final textualizado como z.
        value = value[:-1]
    return value


def _domain_from_url(url: str) -> str:
    host = urlparse(url).netloc.lower().strip()
    if host.startswith("www."):
        host_no_www = host[4:]
    else:
        host_no_www = host
    return host_no_www


def _load_raw() -> dict[str, Any]:
    if not os.path.exists(REGISTRY_PATH):
        return {}
    with open(REGISTRY_PATH, "r", encoding="utf-8") as fp:
        data = json.load(fp)
    return data if isinstance(data, dict) else {}


def _to_config(script: str, data: dict[str, Any]) -> SourceConfig:
    base_url = _clean_url(data.get("base_url") or "")
    start_urls = tuple(_clean_url(u) for u in data.get("start_urls") or [] if _clean_url(u))
    domains = list(data.get("allowed_domains") or [])
    if base_url:
        domains.append(_domain_from_url(base_url))
    for url in start_urls:
        domains.append(_domain_from_url(url))
    domains_clean: list[str] = []
    for domain in domains:
        domain = str(domain or "").lower().strip()
        domain = domain[4:] if domain.startswith("www.") else domain
        if domain and domain not in domains_clean:
            domains_clean.append(domain)
    return SourceConfig(
        script=script,
        name=str(data.get("name") or os.path.splitext(script)[0]).strip(),
        base_url=base_url,
        start_urls=start_urls or ((base_url,) if base_url else tuple()),
        allowed_domains=tuple(domains_clean),
        keywords=tuple(str(k).strip().lower() for k in data.get("keywords") or [] if str(k).strip()),
        max_depth=data.get("max_depth"),
        max_pages=data.get("max_pages"),
    )


def load_sources() -> dict[str, SourceConfig]:
    raw = _load_raw()
    out: dict[str, SourceConfig] = {}
    for script, payload in raw.items():
        if isinstance(payload, dict):
            cfg = _to_config(script, payload)
            if cfg.start_urls:
                out[script] = cfg
    return out


def get_source_by_script(script: str) -> SourceConfig | None:
    if not script:
        return None
    sources = load_sources()
    if script in sources:
        return sources[script]
    normalized = os.path.basename(script)
    if normalized in sources:
        return sources[normalized]
    stem = os.path.splitext(normalized)[0].lower()
    for key, cfg in sources.items():
        if os.path.splitext(key)[0].lower() == stem:
            return cfg
    return None


def get_all_scripts() -> list[str]:
    return sorted(load_sources().keys())
