"""
models.py — Modelo Pydantic de la salida final del sistema.

`ContractChangeOutput` es el "contrato de calidad" de la respuesta: si el JSON que devuelve
el Agente 2 no cumple exactamente este esquema, se rechaza con un error claro (y se reintenta
una vez pasándole el error al modelo).
"""

from typing import List

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ContractChangeOutput(BaseModel):
    """Resultado estructurado de comparar un contrato original contra su enmienda."""

    # extra="forbid": si el modelo inventa un campo que no pedimos, la validación falla.
    model_config = ConfigDict(extra="forbid")

    sections_changed: List[str] = Field(
        ...,
        description=(
            "Secciones o cláusulas modificadas, agregadas o eliminadas. "
            "Ej.: 'Cláusula 2 - Plazo', 'Cláusula 7 - Protección de Datos (nueva)'."
        ),
    )
    topics_touched: List[str] = Field(
        ...,
        description="Temas jurídicos/comerciales afectados. Ej.: 'plazo', 'precio', 'soporte técnico'.",
    )
    summary_of_the_change: str = Field(
        ...,
        description="Resumen claro de cada cambio, indicando el valor anterior y el nuevo.",
    )

    @field_validator("sections_changed", "topics_touched")
    @classmethod
    def _items_must_be_non_empty_text(cls, items: List[str]) -> List[str]:
        cleaned = [i.strip() for i in items]
        if any(not i for i in cleaned):
            raise ValueError("la lista no puede contener textos vacíos")
        return cleaned

    @field_validator("summary_of_the_change")
    @classmethod
    def _summary_must_have_content(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 10:
            raise ValueError("el resumen es demasiado corto para describir los cambios")
        return value
