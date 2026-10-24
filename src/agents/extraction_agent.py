"""
extraction_agent.py — Agente 2: "Auditor de Cumplimiento".

Responsabilidad ÚNICA: identificar y describir cada cambio entre el contrato original y la
enmienda. Recibe el mapa del Agente 1 (handoff) más los dos textos, y devuelve un
`ContractChangeOutput` ya validado con Pydantic.

Validación en dos capas:
  1. Structured Outputs de OpenAI (json_schema) fuerzan al modelo a respetar el esquema.
  2. Aun así validamos nosotros con `model_validate` (defensa en profundidad): si algo
     falla, le devolvemos el error de Pydantic al modelo y reintentamos una vez.
"""

import json
import time
from typing import Any, List, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import ValidationError

from src import config
from src.models import ContractChangeOutput
from src.tracing import span

SYSTEM_PROMPT = """Sos un Auditor de Cumplimiento (compliance) legal. Tu trabajo es detectar con
exactitud TODAS las diferencias entre un contrato ORIGINAL y su ENMIENDA. Un cambio que se te
escape puede costarle dinero a la empresa; un cambio inventado, también.

Recibís: (a) un MAPA ESTRUCTURAL preparado por un analista, que te dice qué cláusula del
original corresponde a cuál de la enmienda, y (b) los textos completos de ambos documentos.

Método de trabajo:
1. Recorré el mapa fila por fila. Para cada cláusula "presente en ambos", compará el texto
   original con el de la enmienda frase por frase: cifras, monedas, plazos, porcentajes,
   fechas, canales, obligaciones, y también cambios sutiles de redacción
   (por ejemplo, una palabra eliminada como "intransferible" cambia el alcance de la cláusula).
2. Las cláusulas "solo en la enmienda" son ADICIONES; las "solo en el original" son ELIMINACIONES.
3. Ignorá diferencias puramente de formato (espacios, saltos de línea, tipografía).
4. Basate SOLO en los textos. No supongas, no completes y no incluyas nada que no esté respaldado.

Formato de salida (JSON exacto, sin campos extra):
- sections_changed: lista de las cláusulas afectadas, con número y título tal como figuran,
  indicando si son nuevas o eliminadas. Ej.: "Cláusula 2 - Plazo", "Cláusula 7 - Protección de Datos (nueva)".
- topics_touched: lista de temas afectados, en minúsculas y breves. Ej.: "plazo", "precio", "soporte técnico".
- summary_of_the_change: resumen en español que describa CADA cambio con su valor anterior y su
  valor nuevo (ej.: "El plazo pasa de 12 a 24 meses."), una oración por cambio, sin omitir ninguno."""

HUMAN_PROMPT = """=== MAPA ESTRUCTURAL (del Analista) ===
{context_map}

=== CONTRATO ORIGINAL ===
{original_text}

=== ENMIENDA ===
{amendment_text}

Auditá los cambios y devolvé el JSON."""

RETRY_PROMPT = """Tu respuesta anterior NO cumplió el esquema requerido. Error de validación:
{error}

Corregilo y devolvé únicamente el JSON válido con los tres campos: sections_changed (lista de textos),
topics_touched (lista de textos) y summary_of_the_change (texto), sin campos adicionales."""


class ExtractionError(RuntimeError):
    """El Agente 2 no logró producir una salida que cumpla el esquema tras los reintentos."""


class ExtractionAgent:
    """Extrae y valida los cambios entre contrato original y enmienda."""

    SPAN_NAME = "extraction_agent"

    def __init__(self, llm: Optional[ChatOpenAI] = None):
        self.llm = llm or ChatOpenAI(
            model=config.AGENT_MODEL,
            temperature=0,
            timeout=config.REQUEST_TIMEOUT_S,
            max_retries=config.MAX_API_RETRIES,
        )
        # include_raw=True: nos devuelve también el mensaje crudo, para poder validarlo nosotros
        # y para leer los tokens consumidos. method="json_schema" = Structured Outputs de OpenAI.
        self._structured = self.llm.with_structured_output(
            ContractChangeOutput, method="json_schema", include_raw=True
        )

    @staticmethod
    def _validate(result: dict) -> ContractChangeOutput:
        """
        Convierte la respuesta del modelo en un ContractChangeOutput válido, o lanza
        ValidationError / ValueError con un mensaje claro.
        """
        parsed = result.get("parsed")
        if parsed is not None:
            # model_validate sobre el dict: vuelve a correr TODAS las reglas del modelo.
            return ContractChangeOutput.model_validate(parsed.model_dump())

        raw = result.get("raw")
        parsing_error = result.get("parsing_error")
        content = getattr(raw, "content", "") or ""
        if content:
            return ContractChangeOutput.model_validate(json.loads(content))
        raise ValueError(f"El modelo no devolvió JSON utilizable ({parsing_error})")

    def run(
        self,
        original_text: str,
        amendment_text: str,
        context_map: str,
        callbacks: Optional[List[Any]] = None,
    ) -> ContractChangeOutput:
        """Devuelve la salida validada. Lanza ExtractionError si no se puede validar."""
        span_input = {
            "context_map": context_map,
            "original_text": original_text,
            "amendment_text": amendment_text,
        }
        with span(self.SPAN_NAME, as_type="agent", input_data=span_input,
                  metadata={"role": "Auditor de Cumplimiento", "model": config.AGENT_MODEL}) as obs:
            started = time.perf_counter()
            messages: List[Any] = [
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(content=HUMAN_PROMPT.format(
                    context_map=context_map,
                    original_text=original_text,
                    amendment_text=amendment_text,
                )),
            ]
            in_tokens = out_tokens = 0
            attempts = 0
            last_error: Optional[Exception] = None
            output: Optional[ContractChangeOutput] = None

            for attempts in range(1, config.MAX_VALIDATION_RETRIES + 2):
                result = self._structured.invoke(messages, config={"callbacks": callbacks or []})
                usage = getattr(result.get("raw"), "usage_metadata", None) or {}
                in_tokens += usage.get("input_tokens", 0)
                out_tokens += usage.get("output_tokens", 0)
                try:
                    output = self._validate(result)
                    break
                except (ValidationError, ValueError) as exc:
                    last_error = exc
                    print(f"  [validación {attempts}] la salida del Agente 2 no cumplió el esquema: {exc}")
                    raw_content = getattr(result.get("raw"), "content", "") or ""
                    messages.append(AIMessage(content=raw_content))
                    messages.append(HumanMessage(content=RETRY_PROMPT.format(error=exc)))

            metadata = {
                "role": "Auditor de Cumplimiento",
                "model": config.AGENT_MODEL,
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "input_tokens": in_tokens,
                "output_tokens": out_tokens,
                "total_tokens": in_tokens + out_tokens,
                "validation_attempts": attempts,
                "validation_ok": output is not None,
            }
            if output is None:
                obs.update(metadata=metadata)
                raise ExtractionError(
                    f"La salida del Agente 2 no cumplió el esquema tras {attempts} intento(s): {last_error}"
                ) from last_error

            obs.update(output=output.model_dump(), metadata=metadata)
            return output
