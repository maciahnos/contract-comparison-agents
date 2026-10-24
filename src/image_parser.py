"""
image_parser.py — Utilidades de imagen: validación, codificación base64 y llamada multimodal.

Función principal: `parse_contract_image(path, span_name)` -> texto completo del contrato.

Qué hace, paso a paso:
  1. VALIDA el archivo (existe, extensión permitida, tamaño, y que sus primeros bytes
     correspondan de verdad a un JPEG/PNG: no nos fiamos solo de la extensión).
  2. CODIFICA la imagen en base64 y arma el mensaje multimodal (texto + imagen).
  3. LLAMA a GPT-4o Vision con reintentos y espera exponencial ante errores transitorios
     (rate limit, timeout, caída de red, 5xx). Los errores permanentes (400) no se reintentan.
  4. REGISTRA todo como un span de Langfuse (input, output, latencia, tokens).
"""

import base64
import time
from pathlib import Path
from typing import Optional, Tuple

import openai

from src import config
from src.tracing import span

# --- Prompts -----------------------------------------------------------------
VISION_SYSTEM_PROMPT = """Sos un sistema de transcripción (OCR jurídico) de altísima fidelidad.
Tu única tarea es transcribir el texto de la imagen de un contrato o de una enmienda.

Reglas:
1. Transcribí TODO el texto, palabra por palabra, sin resumir, sin interpretar y sin corregir.
2. Respetá la jerarquía del documento: título, encabezado de partes, y cada cláusula/sección
   con su numeración y su título (ej.: "1. Otorgamiento de Licencia"), cada una en su propio bloque.
3. Conservá exactamente las cifras, monedas, fechas, plazos y nombres propios.
4. Si una palabra es ilegible escribí [ilegible]; nunca inventes texto.
5. No agregues comentarios, introducciones ni formato markdown: devolvé solo el texto del documento.
6. Si la imagen no contiene texto de un documento, respondé únicamente: SIN_TEXTO"""

VISION_USER_PROMPT = "Transcribí el texto completo de este documento ({label}) respetando sus cláusulas."

# Errores de OpenAI que vale la pena reintentar (son transitorios).
_RETRYABLE = (
    openai.RateLimitError,
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.InternalServerError,
)


class ImageValidationError(ValueError):
    """El archivo de entrada no es una imagen válida (falta, formato, tamaño o contenido)."""


class ImageParsingError(RuntimeError):
    """La API no pudo transcribir la imagen (agotó los reintentos o devolvió algo inutilizable)."""


# --- 1. Validación -----------------------------------------------------------
_MAGIC_BYTES = {
    ".jpg": b"\xff\xd8\xff",
    ".jpeg": b"\xff\xd8\xff",
    ".png": b"\x89PNG\r\n\x1a\n",
}
_END_MARKERS = {
    ".jpg": b"\xff\xd9",
    ".jpeg": b"\xff\xd9",
    ".png": b"IEND\xaeB`\x82",
}
_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


def validate_image(image_path: str) -> Path:
    """Valida el archivo de entrada y devuelve su Path. Lanza ImageValidationError si falla."""
    path = Path(image_path).expanduser()
    if not path.is_file():
        raise ImageValidationError(f"No existe el archivo: {path}")

    ext = path.suffix.lower()
    if ext not in config.ALLOWED_EXTENSIONS:
        raise ImageValidationError(
            f"Formato '{ext or 'sin extensión'}' no soportado en {path.name}. "
            f"Usá: {', '.join(sorted(config.ALLOWED_EXTENSIONS))}"
        )

    size_bytes = path.stat().st_size
    size_mb = size_bytes / (1024 * 1024)
    if size_mb == 0:
        raise ImageValidationError(f"El archivo está vacío: {path.name}")
    if size_mb > config.MAX_IMAGE_MB:
        raise ImageValidationError(
            f"{path.name} pesa {size_mb:.1f} MB y el límite es {config.MAX_IMAGE_MB} MB."
        )

    with path.open("rb") as fh:
        header = fh.read(8)
        fh.seek(-min(size_bytes, 32), 2)
        tail = fh.read().rstrip(b"\x00")
    if not header.startswith(_MAGIC_BYTES[ext]):
        raise ImageValidationError(
            f"{path.name} tiene extensión {ext} pero su contenido no es una imagen {ext[1:].upper()} real."
        )
    # Detecta archivos CORTADOS (descarga interrumpida): un JPEG termina en FFD9, un PNG en IEND.
    # Sin este chequeo, el modelo leería solo la mitad del contrato y no avisaría.
    if not tail.endswith(_END_MARKERS[ext]):
        raise ImageValidationError(
            f"{path.name} parece estar incompleto o dañado (el archivo está cortado). "
            f"Volvé a descargarlo o exportarlo."
        )
    return path


# --- 2. Codificación ---------------------------------------------------------
def encode_image_base64(path: Path) -> Tuple[str, str]:
    """Devuelve (base64, mime_type) de una imagen ya validada."""
    data = path.read_bytes()
    return base64.b64encode(data).decode("utf-8"), _MIME[path.suffix.lower()]


# --- 3. Llamada multimodal ---------------------------------------------------
def _call_vision_api(client: openai.OpenAI, data_url: str, label: str):
    """Una llamada a GPT-4o Vision. Se separa para poder reintentarla limpiamente."""
    return client.chat.completions.create(
        model=config.VISION_MODEL,
        temperature=0,
        max_tokens=config.VISION_MAX_OUTPUT_TOKENS,
        messages=[
            {"role": "system", "content": VISION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": VISION_USER_PROMPT.format(label=label)},
                    # detail="high": necesario para leer letra chica y cifras con precisión.
                    {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
                ],
            },
        ],
    )


def parse_contract_image(
    image_path: str,
    label: str = "contrato",
    span_name: str = "parse_contract",
    client: Optional[openai.OpenAI] = None,
) -> str:
    """
    Transcribe una imagen de contrato a texto con GPT-4o Vision.

    Args:
        image_path: ruta a un JPEG/PNG.
        label: "contrato original" / "enmienda", para guiar al modelo y para las trazas.
        span_name: nombre del span en Langfuse (parse_original_contract / parse_amendment_contract).
        client: cliente de OpenAI (se crea uno si no se pasa; útil para tests).

    Raises:
        ImageValidationError: archivo inválido (antes de gastar un solo token).
        ImageParsingError: la API falló de forma permanente o devolvió un texto inutilizable.
    """
    path = validate_image(image_path)
    b64, mime = encode_image_base64(path)
    data_url = f"data:{mime};base64,{b64}"

    if client is None:
        config.require_openai_key()
        # max_retries=0: los reintentos los hacemos nosotros, con logs y backoff visibles.
        client = openai.OpenAI(timeout=config.REQUEST_TIMEOUT_S, max_retries=0)

    # En Langfuse guardamos el nombre y tamaño del archivo, NO el base64 (pesaría MBs por traza).
    span_input = {"image": path.name, "size_kb": round(path.stat().st_size / 1024, 1), "label": label}

    with span(span_name, as_type="generation", input_data=span_input, model=config.VISION_MODEL) as obs:
        started = time.perf_counter()
        response = None
        last_error: Optional[Exception] = None

        for attempt in range(1, config.MAX_API_RETRIES + 1):
            try:
                response = _call_vision_api(client, data_url, label)
                break
            except _RETRYABLE as exc:
                last_error = exc
                if attempt < config.MAX_API_RETRIES:
                    wait = config.RETRY_BASE_DELAY_S * (2 ** (attempt - 1))
                    print(f"  [reintento {attempt}/{config.MAX_API_RETRIES - 1}] "
                          f"{type(exc).__name__} al leer {path.name}; espero {wait:.0f}s...")
                    time.sleep(wait)
            except openai.BadRequestError as exc:
                # Error permanente (p. ej. imagen rechazada por la API): no sirve reintentar.
                raise ImageParsingError(f"La API rechazó {path.name}: {exc}") from exc
            except openai.AuthenticationError as exc:
                raise ImageParsingError("OPENAI_API_KEY inválida o sin permisos.") from exc

        if response is None:
            raise ImageParsingError(
                f"No se pudo leer {path.name} tras {config.MAX_API_RETRIES} intentos: "
                f"{type(last_error).__name__}: {last_error}"
            ) from last_error

        choice = response.choices[0]
        text = (choice.message.content or "").strip()
        latency_ms = round((time.perf_counter() - started) * 1000)

        usage = response.usage
        obs.update(
            output=text,
            usage_details={
                "input": usage.prompt_tokens,
                "output": usage.completion_tokens,
                "total": usage.total_tokens,
            },
            metadata={
                "finish_reason": choice.finish_reason,
                "latency_ms": latency_ms,
                "chars_extracted": len(text),
                "detail": "high",
            },
        )

        if choice.finish_reason == "length":
            raise ImageParsingError(
                f"La transcripción de {path.name} se cortó por límite de tokens "
                f"({config.VISION_MAX_OUTPUT_TOKENS}); el texto estaría incompleto."
            )
        if getattr(choice.message, "refusal", None):
            raise ImageParsingError(f"El modelo se negó a leer {path.name}: {choice.message.refusal}")
        if not text or text.upper().startswith("SIN_TEXTO") or len(text) < 40:
            raise ImageParsingError(
                f"No se encontró texto de contrato en {path.name} (¿imagen en blanco o borrosa?)."
            )
        return text
