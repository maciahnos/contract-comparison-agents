"""
contextualization_agent.py — Agente 1: "Analista Legal Senior".

Responsabilidad ÚNICA: entender la ESTRUCTURA de los dos documentos y construir un mapa de
correspondencia entre sus secciones. NO detecta ni describe cambios: esa es la tarea del
Agente 2. Separarlo así hace que cada agente tenga un prompt corto y enfocado, y que el
resultado del Agente 1 sea un "mapa" reutilizable que guía la auditoría del Agente 2.
"""

import time
from typing import Any, List, Optional

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from src import config
from src.tracing import span

SYSTEM_PROMPT = """Sos un Analista Legal Senior con 15 años de experiencia en contratos comerciales
(licencias, servicios, SaaS). Tu rol es de ORIENTACIÓN ESTRUCTURAL: leés dos versiones de un
mismo acuerdo (el contrato ORIGINAL y su ENMIENDA) y preparás un mapa para que un auditor
pueda compararlas después, cláusula por cláusula.

Producí SIEMPRE este informe, en español, con estas tres secciones:

## 1. Identificación
Para cada documento: tipo de contrato, partes (con su rol), fecha y cantidad de cláusulas.

## 2. Mapa de correspondencia
Una tabla en markdown con las columnas:
| Cláusula en ORIGINAL | Cláusula en ENMIENDA | Correspondencia | Propósito de la cláusula |
- "Correspondencia" solo puede ser: "presente en ambos", "solo en el original" o "solo en la enmienda".
- "Propósito" explica en una frase para qué sirve esa cláusula dentro del contrato.
- Emparejá por función, no solo por número: si una cláusula cambió de número o de título, igual va en la misma fila.

## 3. Observaciones estructurales
Renumeraciones, cláusulas que aparecen o desaparecen, cambios de orden, y todo dato de contexto
(partes, objeto, jerarquía) que ayude al auditor.

REGLAS ESTRICTAS:
- NO describas qué cambió en el contenido (cifras, plazos, condiciones): no compares valores.
  Esa es la tarea de otro agente. Vos solo mapeás estructura y propósito.
- Basate únicamente en los textos recibidos; no inventes cláusulas."""

HUMAN_PROMPT = """=== CONTRATO ORIGINAL ===
{original_text}

=== ENMIENDA ===
{amendment_text}

Preparame el mapa estructural."""


class ContextualizationAgent:
    """Construye el mapa estructural comparado de ORIGINAL vs ENMIENDA."""

    SPAN_NAME = "contextualization_agent"

    def __init__(self, llm: Optional[ChatOpenAI] = None):
        self.llm = llm or ChatOpenAI(
            model=config.AGENT_MODEL,
            temperature=0,  # tarea analítica: queremos resultados reproducibles
            timeout=config.REQUEST_TIMEOUT_S,
            max_retries=config.MAX_API_RETRIES,
        )
        self._prompt = ChatPromptTemplate.from_messages(
            [("system", SYSTEM_PROMPT), ("human", HUMAN_PROMPT)]
        )

    def run(self, original_text: str, amendment_text: str, callbacks: Optional[List[Any]] = None) -> str:
        """Devuelve el mapa contextual (texto estructurado en markdown)."""
        span_input = {"original_text": original_text, "amendment_text": amendment_text}
        with span(self.SPAN_NAME, as_type="agent", input_data=span_input,
                  metadata={"role": "Analista Legal Senior", "model": config.AGENT_MODEL}) as obs:
            started = time.perf_counter()
            chain = self._prompt | self.llm
            message = chain.invoke(
                {"original_text": original_text, "amendment_text": amendment_text},
                config={"callbacks": callbacks or []},
            )
            context_map = StrOutputParser().invoke(message).strip()
            usage = getattr(message, "usage_metadata", None) or {}
            obs.update(
                output=context_map,
                metadata={
                    "role": "Analista Legal Senior",
                    "model": config.AGENT_MODEL,
                    "latency_ms": round((time.perf_counter() - started) * 1000),
                    "input_tokens": usage.get("input_tokens"),
                    "output_tokens": usage.get("output_tokens"),
                    "total_tokens": usage.get("total_tokens"),
                },
            )
            if len(context_map) < 50:
                raise RuntimeError("El ContextualizationAgent devolvió un mapa vacío o inutilizable.")
            return context_map
