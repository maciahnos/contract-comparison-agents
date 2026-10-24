"""
main.py — Script principal: compara un contrato original contra su enmienda.

Uso:
    python src/main.py data/test_contracts/documento_1__original.jpg data/test_contracts/documento_1__enmienda.jpg
    python -m src.main <imagen_original> <imagen_enmienda> [--out salida.json] [--verbose]

Flujo (todo dentro de UNA traza de Langfuse llamada `contract-analysis`):
    imagen original  --parse_original_contract-->  texto original   ┐
    imagen enmienda  --parse_amendment_contract--> texto enmienda   ┤
                                                                     ├-> contextualization_agent -> mapa
    texto original + texto enmienda + mapa -------> extraction_agent -> ContractChangeOutput (JSON)
"""

import argparse
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

# Permite ejecutar tanto `python src/main.py` como `python -m src.main`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, tracing  # noqa: E402
from src.agents.contextualization_agent import ContextualizationAgent  # noqa: E402
from src.agents.extraction_agent import ExtractionAgent, ExtractionError  # noqa: E402
from src.image_parser import ImageParsingError, ImageValidationError, parse_contract_image, validate_image  # noqa: E402
from src.models import ContractChangeOutput  # noqa: E402


def analyze_contracts(
    original_image: str,
    amendment_image: str,
    session_id: Optional[str] = None,
    verbose: bool = False,
    tags: Optional[list] = None,
) -> dict:
    """
    Ejecuta el pipeline completo y devuelve un dict con el resultado y datos de la corrida:
    {"output": ContractChangeOutput, "trace_id", "trace_url", "latency_s", "intermediate": {...}}
    """
    # Validamos las DOS imágenes antes de abrir la traza o gastar tokens: falla rápido y claro.
    validate_image(original_image)
    validate_image(amendment_image)
    config.require_openai_key()

    session_id = session_id or f"contract-{uuid.uuid4().hex[:8]}"
    started = time.perf_counter()

    with tracing.traced_run(
        "contract-analysis",
        input_data={"original_image": Path(original_image).name, "amendment_image": Path(amendment_image).name},
        session_id=session_id,
        tags=["contract-comparison"] + (tags or []),
        metadata={"vision_model": config.VISION_MODEL, "agent_model": config.AGENT_MODEL},
    ) as (handler, trace_id, root):
        callbacks = [handler] if handler else []

        # --- Paso 1: parsing multimodal (dos spans) ---
        print("1/4 Leyendo contrato original con GPT-4o Vision...")
        original_text = parse_contract_image(original_image, "contrato original", "parse_original_contract")
        print("2/4 Leyendo enmienda con GPT-4o Vision...")
        amendment_text = parse_contract_image(amendment_image, "enmienda", "parse_amendment_contract")

        # --- Paso 2: Agente 1 (contexto) ---
        print("3/4 Agente 1 (Analista Legal): armando el mapa estructural...")
        context_map = ContextualizationAgent().run(original_text, amendment_text, callbacks)

        # --- Paso 3: Agente 2 (extracción + validación Pydantic) ---
        print("4/4 Agente 2 (Auditor): extrayendo y validando los cambios...")
        output: ContractChangeOutput = ExtractionAgent().run(original_text, amendment_text, context_map, callbacks)

        latency_s = round(time.perf_counter() - started, 1)
        root.update(output=output.model_dump(), metadata={"total_latency_s": latency_s})

    url = tracing.trace_url(trace_id)
    tracing.flush()

    result = {"output": output, "trace_id": trace_id, "trace_url": url, "latency_s": latency_s, "session_id": session_id}
    if verbose:
        result["intermediate"] = {
            "original_text": original_text,
            "amendment_text": amendment_text,
            "context_map": context_map,
        }
    return result


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Compara un contrato original contra su enmienda (imágenes) y lista los cambios.")
    p.add_argument("original", help="Imagen (JPEG/PNG) del contrato original")
    p.add_argument("amendment", help="Imagen (JPEG/PNG) de la enmienda")
    p.add_argument("--out", help="Guardar el JSON resultante en este archivo")
    p.add_argument("--verbose", action="store_true", help="Mostrar también los textos leídos y el mapa del Agente 1")
    return p


def main(argv: Optional[list] = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    if not tracing.langfuse_enabled():
        print("Aviso: no hay claves de Langfuse en .env; el análisis corre SIN trazas.")

    try:
        result = analyze_contracts(args.original, args.amendment, verbose=args.verbose)
    except ImageValidationError as exc:
        print(f"\nERROR de entrada: {exc}", file=sys.stderr)
        return 2
    except (ImageParsingError, ExtractionError) as exc:
        print(f"\nERROR en el análisis: {exc}", file=sys.stderr)
        tracing.flush()
        return 3
    except RuntimeError as exc:  # p. ej. falta la API key
        print(f"\nERROR de configuración: {exc}", file=sys.stderr)
        return 1

    if args.verbose:
        inter = result["intermediate"]
        print("\n=== TEXTO ORIGINAL ===\n" + inter["original_text"])
        print("\n=== TEXTO ENMIENDA ===\n" + inter["amendment_text"])
        print("\n=== MAPA DEL AGENTE 1 ===\n" + inter["context_map"])

    json_out = result["output"].model_dump_json(indent=2)
    print("\n=== CAMBIOS DETECTADOS (JSON validado) ===")
    print(json_out)
    print(f"\nTiempo total: {result['latency_s']} s")
    if result["trace_url"]:
        print(f"Traza en Langfuse: {result['trace_url']}")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json_out, encoding="utf-8")
        print(f"Resultado guardado en {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
