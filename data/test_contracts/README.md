# Imágenes de prueba

Tres pares de contratos escaneados (contrato original + enmienda), provistos por el curso
(Henry, carpeta "M4_PI → Imágenes de ejemplo"). Los cambios esperados de cada par están en
[`../golden_cases.json`](../golden_cases.json) y se usan para evaluar el sistema con `python run_golden.py`.

| Par | Archivos | Tipo de contrato | Complejidad | Cambios esperados |
|---|---|---|---|---|
| 1 | `documento_1__original.jpg` / `documento_1__enmienda.jpg` | Licencia de software (TechNova ↔ DataBridge) | Compleja | Plazo 12→24 meses; pago USD 12.000→15.000; soporte email→email+chat; terminación 30→60 días; **nueva** cláusula 7 de Protección de Datos |
| 2 | `documento_2__original.jpg` / `documento_2__enmienda.jpg` | Consultoría (Orion ↔ GreenWave) | Compleja | Alcance + análisis regulatorio; duración 6→9 meses; honorarios USD 8.000→9.500; entregables mensuales→quincenales; **nueva** cláusula 7 de Propiedad Intelectual |
| 3 | `documento_3__original.jpg` / `documento_3__enmienda.jpg` | SaaS (CloudMetrics ↔ RetailPulse) | Simple | Precio USD 1.200→1.250; disponibilidad 99,5 %→99,9 %; soporte email→email+tickets |

## Cómo usarlas

```bash
python src/main.py data/test_contracts/documento_3__original.jpg data/test_contracts/documento_3__enmienda.jpg
```

Para la demo en vivo se recomienda el **par 3** (cambios simples, resultado rápido) y el
**par 1 o 2** (cambios complejos, incluye una cláusula nueva).

## Notas

- Son imágenes JPEG de página completa con texto nítido; el sistema valida que cada archivo
  sea realmente un JPEG/PNG **completo** (un archivo cortado por una descarga interrumpida
  se rechaza con un mensaje claro antes de gastar tokens).
- Se pueden agregar más pares con cualquier imagen JPG/PNG: no hay nada específico de estos contratos en el código.
