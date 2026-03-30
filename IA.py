import logging
from functools import wraps
from typing import Any, Callable

try:
    from ._pyc_bridge import load_module_from_pyc, export_module_namespace
except Exception:
    from _pyc_bridge import load_module_from_pyc, export_module_namespace

_loaded_module = load_module_from_pyc(__file__, __name__, __package__)
export_module_namespace(globals(), _loaded_module)

try:
    from . import utils as _scraper_utils
except Exception:
    try:
        import utils as _scraper_utils  # type: ignore
    except Exception:
        _scraper_utils = None


_IA_LOGGER = globals().get("logger")
if not isinstance(_IA_LOGGER, logging.Logger):
    _IA_LOGGER = logging.getLogger(__name__)

_CACHE_GUARD_ATTR = "_analysis_cache_guard"


def _extract_ia_call_payload(method_name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[Any, str, str | None]:
    if method_name == "analisar_smart":
        texto = kwargs.get("texto_completo")
        if texto is None and args:
            texto = args[0]
        contexto = kwargs.get("contexto")
        if contexto is None and len(args) > 1:
            contexto = args[1]
        url_hint = kwargs.get("url") or kwargs.get("url_hint")
        return texto, str(contexto or ""), url_hint

    textos = kwargs.get("textos")
    if textos is None and args:
        textos = args[0]
    contexto = kwargs.get("contexto")
    if contexto is None and len(args) > 1:
        contexto = args[1]
    url_hint = kwargs.get("url") or kwargs.get("url_hint")
    return textos, str(contexto or ""), url_hint


def _attach_analysis_cache(result: dict[str, Any], source: str, hash_identificacao: str | None, analise_id: int | None, edital_id: int | None) -> None:
    cache_info = result.get("__analysis_cache")
    if not isinstance(cache_info, dict):
        cache_info = {}
        result["__analysis_cache"] = cache_info

    cache_info["fonte_cache"] = source
    if hash_identificacao:
        cache_info["hash_identificacao"] = str(hash_identificacao)
    if analise_id:
        cache_info["analise_id"] = int(analise_id)
    if edital_id:
        cache_info["edital_id_relacionado"] = int(edital_id)


def _wrap_ia_method(method_name: str, original: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(original)
    def wrapped(self, *args, **kwargs):
        if getattr(self, _CACHE_GUARD_ATTR, False) or _scraper_utils is None:
            return original(self, *args, **kwargs)

        setattr(self, _CACHE_GUARD_ATTR, True)
        try:
            textos, contexto, url_hint = _extract_ia_call_payload(method_name, args, kwargs)
            cache_lookup = _scraper_utils.verificar_cache_analise_pre_ia(
                textos=textos,
                contexto=contexto,
                url_hint=url_hint,
            )

            acao = str(cache_lookup.get("acao") or "").strip().lower()
            meta = cache_lookup.get("meta") if isinstance(cache_lookup.get("meta"), dict) else {}

            if acao == "usar_cache_editais":
                resultado = cache_lookup.get("resultado")
                if isinstance(resultado, dict):
                    _attach_analysis_cache(
                        resultado,
                        source="editais",
                        hash_identificacao=(meta or {}).get("hash_identificacao"),
                        analise_id=cache_lookup.get("analise_id"),
                        edital_id=cache_lookup.get("edital_id"),
                    )
                    _IA_LOGGER.info(
                        "[CACHE-IA] Analise reutilizada (editais). contexto='%s' edital_id=%s",
                        contexto[:120],
                        cache_lookup.get("edital_id"),
                    )
                    return resultado

            if acao == "usar_cache_analisados":
                resultado = cache_lookup.get("resultado")
                if isinstance(resultado, dict):
                    _attach_analysis_cache(
                        resultado,
                        source="editais_analisados",
                        hash_identificacao=(meta or {}).get("hash_identificacao"),
                        analise_id=cache_lookup.get("analise_id"),
                        edital_id=cache_lookup.get("edital_id"),
                    )
                    _IA_LOGGER.info(
                        "[CACHE-IA] Analise reutilizada (editais_analisados). contexto='%s' analise_id=%s",
                        contexto[:120],
                        cache_lookup.get("analise_id"),
                    )
                    return resultado

            _IA_LOGGER.info(
                "[CACHE-IA] Analise nova; chamando IA. contexto='%s' hash=%s",
                contexto[:120],
                (meta or {}).get("hash_identificacao"),
            )
            resultado = original(self, *args, **kwargs)

            if isinstance(resultado, dict):
                analise_id = _scraper_utils.registrar_resultado_analise_pos_ia(meta=meta or {}, resultado=resultado)
                _attach_analysis_cache(
                    resultado,
                    source="ia",
                    hash_identificacao=(meta or {}).get("hash_identificacao"),
                    analise_id=analise_id,
                    edital_id=None,
                )
            return resultado
        except Exception as exc:
            _IA_LOGGER.warning("[CACHE-IA] Falha no cache de analise; executando fluxo original. erro=%s", exc)
            return original(self, *args, **kwargs)
        finally:
            setattr(self, _CACHE_GUARD_ATTR, False)

    return wrapped


def _patch_ia_client_with_cache() -> None:
    ia_client_cls = globals().get("IAClient")
    if ia_client_cls is None:
        return
    if getattr(ia_client_cls, "_analysis_cache_wrapped", False):
        return

    for method_name in ("analisar_edital_completo", "analisar_smart"):
        original = getattr(ia_client_cls, method_name, None)
        if callable(original):
            setattr(ia_client_cls, method_name, _wrap_ia_method(method_name, original))

    setattr(ia_client_cls, "_analysis_cache_wrapped", True)


_patch_ia_client_with_cache()
