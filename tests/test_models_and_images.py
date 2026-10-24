"""
Tests que NO llaman a ninguna API (corren gratis y sin internet):
  * el modelo Pydantic acepta lo correcto y rechaza lo incorrecto
  * la validación de imágenes de entrada detecta archivos inexistentes, formatos y contenidos falsos

Ejecutar:  python -m pytest -q
"""

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.image_parser import ImageValidationError, validate_image  # noqa: E402
from src.models import ContractChangeOutput  # noqa: E402

VALID = {
    "sections_changed": ["Cláusula 2 - Plazo"],
    "topics_touched": ["plazo"],
    "summary_of_the_change": "El plazo pasa de 12 a 24 meses.",
}


def test_valid_output_is_accepted():
    out = ContractChangeOutput.model_validate(VALID)
    assert out.topics_touched == ["plazo"]


def test_missing_field_is_rejected():
    bad = {k: v for k, v in VALID.items() if k != "summary_of_the_change"}
    with pytest.raises(ValidationError):
        ContractChangeOutput.model_validate(bad)


def test_extra_field_is_rejected():
    with pytest.raises(ValidationError):
        ContractChangeOutput.model_validate({**VALID, "confidence": 0.9})


def test_wrong_type_is_rejected():
    with pytest.raises(ValidationError):
        ContractChangeOutput.model_validate({**VALID, "sections_changed": "Cláusula 2"})


def test_blank_items_and_short_summary_are_rejected():
    with pytest.raises(ValidationError):
        ContractChangeOutput.model_validate({**VALID, "topics_touched": ["  "]})
    with pytest.raises(ValidationError):
        ContractChangeOutput.model_validate({**VALID, "summary_of_the_change": "ok"})


def test_missing_file_is_rejected(tmp_path):
    with pytest.raises(ImageValidationError, match="No existe"):
        validate_image(str(tmp_path / "no_existe.jpg"))


def test_unsupported_extension_is_rejected(tmp_path):
    f = tmp_path / "contrato.pdf"
    f.write_bytes(b"%PDF-1.4")
    with pytest.raises(ImageValidationError, match="no soportado"):
        validate_image(str(f))


def test_fake_jpg_is_rejected(tmp_path):
    f = tmp_path / "falso.jpg"
    f.write_text("esto es texto, no una imagen")
    with pytest.raises(ImageValidationError, match="no es una imagen"):
        validate_image(str(f))


def test_empty_file_is_rejected(tmp_path):
    f = tmp_path / "vacio.png"
    f.write_bytes(b"")
    with pytest.raises(ImageValidationError, match="vacío"):
        validate_image(str(f))


def test_truncated_jpg_is_rejected(tmp_path):
    f = tmp_path / "cortado.jpg"
    f.write_bytes(b"\xff\xd8\xff\xe0" + b"\x01" * 500)  # empieza como JPEG pero no termina en FFD9
    with pytest.raises(ImageValidationError, match="incompleto"):
        validate_image(str(f))


def test_complete_minimal_jpg_is_accepted(tmp_path):
    f = tmp_path / "ok.jpg"
    f.write_bytes(b"\xff\xd8\xff\xe0" + b"\x01" * 500 + b"\xff\xd9")
    assert validate_image(str(f)).name == "ok.jpg"


def test_real_sample_images_are_accepted():
    samples = Path(__file__).resolve().parent.parent / "data" / "test_contracts"
    for img in samples.glob("documento_*.jpg"):
        assert validate_image(str(img)).name == img.name
