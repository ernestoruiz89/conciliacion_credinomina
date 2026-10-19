# Auditoría de vínculos a aplicación — 19 de octubre de 2026

Consulta de solo lectura en `credinomina.appnica.com`, aproximadamente a las 15:24 (America/Managua). Se revisaron las partidas `CN Complementary Item` con categoría **Ajuste de aplicación**, sus vínculos principales (`related_application`) y los adicionales de `application_adjustment_history`. El sentido contable se determinó con `source_debit` y `source_credit` de la partida y de la fila vinculada en `CN Accounting Import`.

## Resultado

- 249 partidas vinculadas: 247 confirmadas y 2 en borrador. Una de las partidas en borrador se creó mientras se hacía la consulta.
- 264 vínculos: 249 principales y 15 adicionales en el historial.
- 260 vínculos con débito/crédito de origen disponible en ambos lados: ninguno une débito con débito ni crédito con crédito.
- Tres vínculos corresponden a partidas manuales sin débito/crédito de origen. No se pueden clasificar con esta evidencia; sus aplicaciones vinculadas son débitos.
- Una aplicación vinculada no se pudo inspeccionar porque el detalle de su importación no terminó de cargar. Su partida complementaria es crédito; sigue pendiente descartar crédito con crédito.

## Registros para revisión

| Partida | Aplicación | Estado | Revisión pendiente |
| --- | --- | --- | --- |
| [CN-COMP-2026-00041](https://credinomina.appnica.com/desk/cn-complementary-item/CN-COMP-2026-00041) | `7o3hdeg1vd` | Confirmada | Partida crédito (`source_credit` 8643.33, `source_debit` 0), ajuste US$236.00. Verificar el sentido de la aplicación en `CONTA-IMMSA-4-2025-014`. |
| [CN-COMP-2026-00685](https://credinomina.appnica.com/desk/cn-complementary-item/CN-COMP-2026-00685) | `6pghd8oade` | Confirmada | Partida manual de US$0.01 sin débito/crédito de origen; aplicación débito. |
| [CN-COMP-2026-00716](https://credinomina.appnica.com/desk/cn-complementary-item/CN-COMP-2026-00716) | `6qasbkfi9e` | Confirmada | Partida manual de US$0.01 sin débito/crédito de origen; aplicación débito. |
| [CN-COMP-2026-02673](https://credinomina.appnica.com/desk/cn-complementary-item/CN-COMP-2026-02673) | `07s2qlkkds` | Confirmada | Partida manual de US$0.01 sin débito/crédito de origen; aplicación débito. |

La ausencia de débito/crédito en las partidas manuales no demuestra un vínculo incorrecto. Esta revisión refleja el estado observado durante la consulta; no se alteraron registros de producción.
