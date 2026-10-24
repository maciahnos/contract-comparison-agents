"""
tracing.py — Integración con Langfuse (observabilidad).

Puntos de entrada:
  * langfuse_enabled()  -> True si hay claves de Langfuse configuradas
  * traced_run(...)     -> abre la traza raíz (`contract-analysis`) y entrega
                           (callback_handler, trace_id, root_span)
  * span(...)           -> context manager que abre un span hijo (parse_*, *_agent) con
                           input, output, metadata y registro automático de errores
  * flush()             -> fuerza el envío de eventos pendientes (clave en scripts cortos)

Si faltan las claves, el sistema sigue funcionando SIN trazas (con un aviso), así el repo
es ejecutable aunque quien lo corra no tenga cuenta de Langfuse.
Requiere el SDK de Langfuse v4 (get_client, start_as_current_observation, propagate_attributes).
"""

import os
from contextlib import contextmanager
from typing import Any, Iterator, List, Optional, Tuple

from src import config  # noqa: F401  (carga .env)


def langfuse_enabled() -> bool:
    pk = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    sk = os.getenv("LANGFUSE_SECRET_KEY", "")
    return pk.startswith("pk-lf-") and sk.startswith("sk-lf-") and "xxx" not in pk + sk


def _normalize_host_env() -> None:
    """El SDK v4 usa LANGFUSE_BASE_URL; aceptamos también LANGFUSE_HOST (nombre histórico)."""
    host = os.getenv("LANGFUSE_HOST")
    if host and not os.getenv("LANGFUSE_BASE_URL"):
        os.environ["LANGFUSE_BASE_URL"] = host


def get_langfuse() -> Optional[Any]:
    """Cliente singleton de Langfuse o None si no está configurado."""
    if not langfuse_enabled():
        return None
    _normalize_host_env()
    from langfuse import get_client

    return get_client()


class _NoopSpan:
    """Se usa cuando Langfuse está apagado: acepta .update(...) sin hacer nada."""

    def update(self, **_: Any) -> None:
        return None


@contextmanager
def traced_run(
    name: str,
    input_data: Any,
    session_id: Optional[str] = None,
    tags: Optional[List[str]] = None,
    metadata: Optional[dict] = None,
) -> Iterator[Tuple[Optional[Any], Optional[str], Any]]:
    """
    Abre la traza raíz del análisis.

    Devuelve (handler, trace_id, root_span):
      - handler: CallbackHandler de LangChain, para `config={"callbacks": [handler]}`;
        hace que cada llamada al LLM de los agentes quede anidada bajo su span.
      - trace_id: id de la traza (para mostrar el link en consola).
      - root_span: span raíz, para actualizar su output al final (_NoopSpan si no hay Langfuse).
    """
    client = get_langfuse()
    if client is None:
        yield None, None, _NoopSpan()
        return

    from langfuse import propagate_attributes
    from langfuse.langchain import CallbackHandler

    clean_meta = {k: str(v)[:200] for k, v in (metadata or {}).items()}
    with client.start_as_current_observation(name=name, as_type="agent", input=input_data) as root:
        with propagate_attributes(
            session_id=session_id, tags=tags or [], metadata=clean_meta, trace_name=name
        ):
            handler = CallbackHandler()
            yield handler, client.get_current_trace_id(), root


@contextmanager
def span(
    name: str,
    as_type: str = "span",
    input_data: Any = None,
    metadata: Optional[dict] = None,
    model: Optional[str] = None,
) -> Iterator[Any]:
    """
    Abre un span hijo del span actual. Si el bloque lanza una excepción, la marca como ERROR
    en Langfuse (nivel + mensaje) y la vuelve a lanzar, para que quede visible en el dashboard.
    """
    client = get_langfuse()
    if client is None:
        yield _NoopSpan()
        return

    kwargs = {"name": name, "as_type": as_type, "input": input_data, "metadata": metadata}
    if model:
        kwargs["model"] = model
    with client.start_as_current_observation(**kwargs) as obs:
        try:
            yield obs
        except Exception as exc:  # noqa: BLE001 — se re-lanza siempre
            obs.update(level="ERROR", status_message=f"{type(exc).__name__}: {exc}")
            raise


def trace_url(trace_id: Optional[str]) -> Optional[str]:
    """Link directo a la traza en el dashboard (si Langfuse está activo)."""
    client = get_langfuse()
    if client is None or not trace_id:
        return None
    try:
        return client.get_trace_url(trace_id=trace_id)
    except Exception:  # noqa: BLE001 — el link es un extra, nunca debe romper el análisis
        return None


def flush() -> None:
    """Envía los eventos pendientes (imprescindible en scripts cortos)."""
    client = get_langfuse()
    if client is not None:
        client.flush()
