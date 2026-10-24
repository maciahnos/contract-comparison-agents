"""
run_golden.py — Evalúa el pipeline contra los "golden cases" (el equivalente a unit tests
para pipelines multimodales, tal como se vio en la Lección 1).

Para cada caso corre el pipeline completo y mide:
  * parse_ok       -> el texto leído de la imagen contiene los datos clave esperados (fidelidad del OCR)
  * schema_valid   -> la salida cumple ContractChangeOutput (si no, el pipeline lanza error)
  * completeness   -> % de cambios esperados que el sistema detectó
  * latencia total del caso

Uso:  python run_golden.py            (todos los casos)
      python run_golden.py caso_3_saas (uno solo)
Guarda el detalle en outputs/golden_results.json
"""

import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src import config, tracing  # noqa: E402
from src.main import analyze_contracts  # noqa: E402


def normalize(text: str) -> str:
    """minúsculas, sin tildes y sin puntos/comas: '12.000' -> '12000', '99,9' -> '999'."""
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"[.,]", "", text)


def main() -> int:
    cases = json.loads((config.DATA_DIR / "golden_cases.json").read_text(encoding="utf-8"))["cases"]
    only = sys.argv[1:]
    if only:
        cases = [c for c in cases if c["id"] in only]

    results = []
    for case in cases:
        print(f"\n=== {case['id']} ({case['tipo']}) ===")
        original = str(config.TEST_CONTRACTS_DIR / case["original"])
        amendment = str(config.TEST_CONTRACTS_DIR / case["amendment"])
        try:
            res = analyze_contracts(original, amendment, verbose=True, tags=["golden", case["id"]])
        except Exception as exc:  # noqa: BLE001 — un caso fallido no corta la evaluación
            print(f"  FALLÓ: {type(exc).__name__}: {exc}")
            results.append({"id": case["id"], "error": f"{type(exc).__name__}: {exc}"})
            continue

        out = res["output"]
        joined = normalize(" ".join(out.sections_changed + out.topics_touched + [out.summary_of_the_change]))
        parsed = normalize(res["intermediate"]["original_text"])

        parse_hits = {k: normalize(k) in parsed for k in case["parse_must_contain"]}
        change_hits = {
            ch["label"]: all(normalize(k) in joined for k in ch["all_of"]) for ch in case["expected_changes"]
        }
        completeness = sum(change_hits.values()) / len(change_hits)

        print(f"  parse_ok: {all(parse_hits.values())}  {parse_hits}")
        for label, ok in change_hits.items():
            print(f"  [{'OK' if ok else '--'}] {label}")
        print(f"  completeness: {completeness:.0%} | latencia: {res['latency_s']} s | traza: {res['trace_url']}")

        results.append({
            "id": case["id"],
            "parse_ok": all(parse_hits.values()),
            "completeness": completeness,
            "changes": change_hits,
            "latency_s": res["latency_s"],
            "trace_url": res["trace_url"],
            "output": out.model_dump(),
        })

    ok_cases = [r for r in results if "error" not in r]
    print("\n=== RESUMEN ===")
    if ok_cases:
        avg = sum(r["completeness"] for r in ok_cases) / len(ok_cases)
        print(f"casos ejecutados: {len(ok_cases)}/{len(results)} | completeness promedio: {avg:.0%}")
    config.OUTPUTS_DIR.mkdir(exist_ok=True)
    (config.OUTPUTS_DIR / "golden_results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tracing.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
