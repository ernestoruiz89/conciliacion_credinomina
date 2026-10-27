# Diferencias documentadas y revisión de partidas históricas

Vigente desde el cambio de octubre de 2026. Sustituye los procedimientos de creación manual de partidas, saldos a favor y ajustes automáticos por tolerancia.

## Operación

Los movimientos contables nuevos proceden de la importación del core. La cobranza informada por la empresa y el soporte bancario siguen siendo fuentes independientes para detectar diferencias. Una etiqueta no crea dinero, no cubre una aplicación ni modifica el estado de conciliación.

Desde un depósito, su detalle, una aplicación o una fila de cobranza, use **Clasificar / documentar diferencia**. Seleccione **Razón de la diferencia** (`difference_reason`) y describa el motivo y soporte en **Documentación de la diferencia**. Puede adjuntar archivos al documento de origen. Se conserva el antes y después en su historial de comentarios. Requiere permiso de escritura y un documento guardado vigente; puede documentar una diferencia de un período cerrado sin reabrirlo. Si otro usuario modificó el documento, recargue antes de guardar.

Ejemplo: aplicación US$100 y depósito US$90. Documentar “Diferencia de importe” conserva US$10 pendientes. Un exceso de US$0.01 queda visible; no se crea una partida por tolerancia. Cuando se importe una corrección del core, revise sus vínculos y concilie explícitamente.

Los antiguos botones de creación por diferencia y saldo a favor llevan a documentación. Las API antiguas de creación rechazan la operación. Tampoco se generan nuevos ajustes provisionales ni recibos manuales de recuperación de CxC; los registros anteriores y sus historiales continúan disponibles. Para un movimiento importado, permanecen las acciones de clasificación y vinculación con su evidencia.

## Reporte de revisión

Abra **Partida Complementaria → Revisión de Partidas Manuales**. Puede filtrar empresa, partida, fechas e incluir tolerancias históricas. El reporte incluye partidas sin `accounting_source_key`, también borradores y canceladas, para conservar el alcance de auditoría.

Muestra importe original, aplicado y pendiente guardados; cliente y crédito; concepto, referencia, comprobante y documentación. Detalla enlaces directos, destinos y asignaciones registradas, ajustes de aplicaciones, compensaciones, historial de créditos, períodos y su estado, depósitos y sus `accounting_sources`, comprobantes, archivos y filas contables. Cada relación permite abrir el documento relacionado.

Las coincidencias por referencia o comprobante son **Candidato; requiere revisión**. No prueban que una partida manual tenga respaldo y nunca se vinculan automáticamente. Los historiales ilegibles y vínculos no disponibles se señalan. Se respetan los permisos: la vista puede ser parcial, y no encontrar un vínculo visible no demuestra su inexistencia.

Hay una fila por relación: los importes de la partida se repiten y **no deben sumarse**. El importe del vínculo solo se presenta cuando consta expresamente en esa evidencia; puede ser un registro histórico o una reversión, no necesariamente un uso vigente. Consultar o exportar no recalcula saldos.

## Tratamiento del histórico

El parche marca las partidas sin origen contable como **Partida histórica por revisar**. No elimina, cancela, fusiona, convierte, reconcilia ni cambia importes, fechas de modificación, estados o vínculos. No atribuye una razón de diferencia sin revisión humana. Los borradores manuales anteriores tampoco pueden confirmarse como nuevo efecto financiero.

Las tolerancias históricas permanecen como evidencia. La conciliación que utiliza distribuciones guardadas puede leerlas; intentar redistribuir un depósito que conserve una tolerancia vigente se detiene antes de modificarlo. No se generan, reactivan ni revierten tolerancias automáticamente. Esta protección obliga a revisar la evidencia antes de una corrección del histórico; el reporte por sí solo no autoriza ni ejecuta esa corrección.

## Despliegue

1. Conservar el respaldo habitual de base de datos y archivos privados antes de actualizar producción.
2. Instalar el código y ejecutar `bench --site <sitio> migrate`.
3. Recargar Desk para cargar los formularios actualizados.
4. Abrir el reporte y revisar las partidas históricas antes de cualquier reproceso.

Parche: `credinomina_reconciliation.patches.v1_0.document_differences_preserve_manual_items`. Es repetible; solo sincroniza metadatos, catálogo y marca de revisión. No ejecuta la conciliación masiva. No necesita un build adicional para el modal, que se carga mediante `doctype_js`.

## Verificación

`smoke_difference_documentation.run` se ejecuta exclusivamente en `cn-reconciliation-test.local` y revierte sus datos de prueba. Comprueba documentación sin cambios financieros, repetición de la fase de datos del parche, conservación de relaciones e importes, reporte real, rechazo de creación manual, residuales positivos y negativos de un centavo sin tolerancia y protección de distribuciones históricas. Las pruebas unitarias y de JavaScript cubren alcance del documento/fila, vínculos del reporte, payload documental, doble confirmación y reintentos.
