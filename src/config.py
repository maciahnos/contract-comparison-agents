"""
config.py — Configuración central del sistema.

Carga las variables de entorno desde .env (sin hardcodear secretos) y define las
constantes compartidas: modelos, timeouts, reintentos y límites de los archivos de entrada.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

# --- Modelos -----------------------------------------------------------------
# GPT-4o: único modelo de OpenAI de esta consigna que "ve" imágenes con buena precisión de OCR.
VISION_MODEL = os.getenv("VISION_MODEL", "gpt-4o")
# Para los agentes de razonamiento también usamos gpt-4o: comparar contratos exige precisión.
AGENT_MODEL = os.getenv("AGENT_MODEL", "gpt-4o")

# --- Robustez de las llamadas a la API ---------------------------------------
REQUEST_TIMEOUT_S = float(os.getenv("REQUEST_TIMEOUT_S", "90"))
MAX_API_RETRIES = int(os.getenv("MAX_API_RETRIES", "3"))
RETRY_BASE_DELAY_S = 2.0          # espera exponencial: 2 s, 4 s, 8 s...
VISION_MAX_OUTPUT_TOKENS = 4096   # un contrato de 1-2 páginas entra de sobra
MAX_VALIDATION_RETRIES = 1        # reintentos del Agente 2 si el JSON no valida

# --- Archivos de entrada -----------------------------------------------------
MAX_IMAGE_MB = 20                 # límite de OpenAI para imágenes
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png"}

# --- Rutas -------------------------------------------------------------------
DATA_DIR = ROOT_DIR / "data"
TEST_CONTRACTS_DIR = DATA_DIR / "test_contracts"
OUTPUTS_DIR = ROOT_DIR / "outputs"


def require_openai_key() -> str:
    """Devuelve la API key de OpenAI o corta con un mensaje claro si falta."""
    key = os.getenv("OPENAI_API_KEY")
    if not key or key.startswith("your-key"):
        raise RuntimeError(
            "Falta OPENAI_API_KEY. Copiá .env.example a .env y completá tu clave real."
        )
    return key
