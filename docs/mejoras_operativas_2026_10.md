# Mejoras operativas y verificación

Objetivo: simplificar la operación individual, unificar la interpretación de saldos y estados y hacer revisables los pendientes. No agregar aprobaciones de otros usuarios ni nuevos documentos de registro.

## Alcance y criterios de aceptación

- [x] Clasificar «No conciliatoria» no extingue un saldo ni cuenta como conciliación. Conservar evidencia y permitir resolver por vínculos reales.
- [x] Unificar el estado de partidas complementarias entre formularios y reportes; sobreasignaciones y signos incorrectos deben ser inconsistencias.
- [x] Acción guiada «Clasificar pendiente», con opciones pertinentes, campos mínimos y enlaces a acciones existentes; sin aprobación multiusuario.
- [x] Separar conciliación, registro contable y gestión de saldos a favor.
- [x] Bandeja compacta: importe financiero visible por caso sin sumar acciones duplicadas, filtros de cliente/motivo/importe, orden por importe o antigüedad y tareas expandibles.
- [x] Estado de cuenta: filtros útiles, detalle identificable, navegación desde importes, conteo de pendientes aun con neto cero y estados parciales correctos.
- [x] Depósitos: resumen financiero visible y siguiente acción principal; complementarias: presentación progresiva sin identificadores técnicos prominentes.
- [x] Mejorar rendimiento medido de las consultas de consulta/bandeja sin perder alcance ni permisos; no recalcular todo para cada página.
- [x] Completar la última revisión visual de interfaz en ensayo, además de instrucciones, código, pruebas y migración. No desplegar ni modificar producción durante la verificación.

## Condiciones de conservación

Conservar los cambios previos del flujo de dos conciliaciones, evidencia original, distribuciones registradas, períodos cerrados y cortes. No ejecutar conciliaciones ni alterar registros financieros durante la migración. Los cambios de clasificación de presentación no generan cobros ni compensaciones.

## Evidencia de validación

Validación del 12 de octubre de 2026:

- Suite completa: 1,223 pruebas y 816 subpruebas aprobadas en Frappe 15.117.0 y 16.51.0. Los 61 archivos de pruebas JavaScript pasaron. `git diff --check` sin errores.
- SQL real: `smoke_operational_clarity` compara resumen/detalle por empresa y seis componentes, revisa las tres pestañas de complementarias y reutilización de consulta; `smoke_accounting_control` valida estados, filtros, importes NIO/USD, descripción original, espejo contado una vez y rollback.
- Dos ejecuciones de `migrate` conservaron el contenido completo de las 26 tablas CN comparadas por SHA-256. Respaldo previo: `20261012_000305-cn-reconciliation-test_local-database.sql.gz`.
- Revisión en navegador: bandeja de 14 casos/15 acciones, expansión y búsqueda; clasificación guiada; tres estados independientes; resumen de empresa con saldo neto cero pero 20 partidas pendientes; apertura de sus 19 aplicaciones desde el importe; depósito de US$100 distribuido entre US$80 a créditos y US$20 de saldo a favor, con US$15 de gestión pendiente todavía visibles.
- La nueva revisión detectó y corrigió una incompatibilidad de `finally` con la promesa de `frappe.call`, y cargas antiguas sin empresa en la cabecera que desaparecían al filtrar el detalle por empresa. Ambas tienen cobertura de regresión.
- Rendimiento en el conjunto pequeño de ensayo: primera bandeja 0.553 s, página de la misma consulta 0.003 s; estado de cuenta completo 0.015 s, solo depósitos 0.001 s. No son garantías de tiempo en producción ni una prueba de carga masiva.

La consulta de bandeja tiene vigencia de 180 segundos, está vinculada al usuario y revalida alcance/permisos y cambios de documentos principales. «Actualizar consulta» obtiene cifras recientes. Nunca se usa esta consulta temporal para ejecutar conciliaciones.

## Consistencia entre vistas: segunda revisión

- Los KPI y Transacciones por Empresa incluyen importaciones antiguas sin empresa en la cabecera, resolviendo la empresa por la fila/período. El catálogo de años usa el mismo alcance al filtrar una empresa.
- Calendario, tarjetas, resumen y Excel utilizan los totales financieros del período: aplicado antes de compensaciones, asignado incluyendo compensaciones confirmadas y pendiente por aplicación. Las excepciones y diferencias de cobranza permanecen separadas del resultado financiero; no se descuentan dos veces los ajustes ni se compensan clientes distintos por neto.
- Un depósito sobreasignado se muestra como distribución inconsistente, sin un porcentaje engañoso de 100 %.
- La búsqueda de tareas incluye nombre, número de cliente, crédito y descripción de la excepción.
- Destinos prioriza descripción, período, importe y resultado. Las filas antiguas sin descripción muestran el tipo de vínculo en lugar de un hash; los identificadores originales se conservan.
- La lista de complementarias consulta saldos por lotes con permisos. Distingue conciliación de registro contable y muestra el importe **por gestionar** para saldos a favor, no el cero financiero de una partida ya distribuida. Si falla la consulta muestra «Saldo no disponible». Las vistas rápidas están agrupadas y se retiran las columnas duplicadas de importe original y referencias de la vista inicial, sin eliminar campos.

Verificación adicional: 1,230 pruebas y 816 subpruebas en Frappe 15.117.0 y 16.51.0; 62 archivos de pruebas JavaScript. Las pruebas SQL de claridad operativa y control contable pasaron con rollback. En navegador, Gamma muestra 57 transacciones (38 conciliadas, 19 pendientes), cuyo detalle pendiente suma US$920.50. La partida de ensayo de US$20 distribuida conserva US$15 por gestionar en su lista. El depósito de US$83 conserva dos créditos de US$41 y US$42. No se ejecutaron conciliaciones desde la revisión visual.

## Correcciones de la revisión de pendientes

- Las partidas importadas conservan la magnitud del débito/crédito original convertido con su tasa de importación. Es posible revisar el signo, no reducir ni aumentar el importe; la distribución o compensación parcial conserva el remanente. Si una partida antigua ya difiere de la evidencia, las consultas la marcan para revisión y conservan la magnitud de origen sin reescribir documentos.
- La lista de complementarias separa `Sin distribuir/compensar`, `Por devolver/aplicar` y `Por cobrar`. Una distribución conciliada no oculta la CxC creada por un ajuste. La vista rápida de CxC abre el detalle del Estado de Cuenta por Empresa.
- «Qué falta hacer» agrupa por documento **y empresa**. Las obligaciones independientes de una partida genérica no se sustituyen por un único importe principal de otra empresa.
- El año de las gestiones de registro sigue la fecha de la partida de origen; las excepciones con período utilizan el mes de cobranza, y las independientes su creación. La fecha compromiso controla vencimiento, no el año de origen.
- «Vincular a aplicación» admite cargas antiguas sin empresa en la cabecera cuando el período/fila/cartera legible confirma la empresa. No cambia la cabecera ni permite aplicaciones de otra empresa.
- En los estados de cuenta, «Más filtros» concentra criterios adicionales. Los filtros activos siguen visibles. La lista de complementarias conserva como filtros iniciales empresa, concepto, registro contable y fecha; los demás se encuentran en el filtro avanzado de Frappe.
- «Clasificar pendiente» explica que clasificar no cobra, devuelve ni compensa dinero. No se agregan documentos ni aprobaciones nuevas.

Despliegue: ejecutar `bench --site <sitio> migrate` para sincronizar el formulario y limpiar caché. Estas correcciones no agregan una migración de saldos ni recalculan conciliaciones automáticamente. Los cambios son adicionales a los del flujo de dos conciliaciones pendientes en este mismo árbol de trabajo.

Validación de estas correcciones: 1,245 pruebas y 816 subpruebas aprobadas en Frappe 15.117.0 y 16.51.0; 63 archivos JavaScript aprobados. Las pruebas SQL `smoke_operational_clarity` y `smoke_accounting_control` pasaron con rollback. `git diff --check` sin errores. Se agregaron regresiones para magnitud contable, conversión, remanentes, CxC visible, agrupación por empresa, año de origen, permisos de vínculos antiguos y filtros activos. La comprobación visual nueva no se completó: localhost:8002 rechazó la conexión al navegar desde la pestaña abierta. No se cambió producción ni se hizo commit/push.

## Finalización de hallazgos de estados y navegación

- Un mismo cálculo determina el estado de aplicaciones en ambos estados de cuenta; los pagos y compensaciones parciales no se presentan como totalmente pendientes.
- Las transacciones complementarias usan el importe original validado contra la evidencia, también cuando son borradores con discrepancias antiguas.
- Las inconsistencias con pendiente financiero cero permanecen visibles en el estado por empresa, la consulta operativa y las tareas. El «Importe observado» de una tarea no se agrega a la CxC. Los componentes conservan el caso aunque su importe sea cero.
- El Excel deja de presentar evidencia contable ya verificada como gestión pendiente cuando no tiene otro saldo o gestión sin resolver.
- La selección de aplicaciones obtiene períodos, reservas y distribuciones por lotes. Una vista previa no puede usarse para confirmar: las confirmaciones mantienen lecturas vigentes y bloqueos. Se corrigió además el acceso a la empresa del documento real de importación, que no admite indexación como un diccionario.
- El reporte operativo abre en aplicaciones con nueve columnas visibles; «Más columnas» conserva acceso a la trazabilidad y Tipo permite otras poblaciones. Las consultas de un solo tipo no cargan los otros libros auxiliares.
- Las sugerencias de estado por empresa provienen de sus resultados sin una segunda consulta de saldos.
- Clasificar pendiente muestra cifras financieras guardadas y distingue las gestiones independientes antes de continuar.
- La bandeja permite recorrer los casos cargados, abrir su documento en otra pestaña y volver sin perder filtros. Actualizar consulta vuelve a aplicar la selección existente.

En esta etapa el navegador de ensayo estaba bloqueado por la política de acceso de la herramienta; la comprobación visual se completó posteriormente, como se documenta a continuación. No se eludió ese bloqueo ni se modificó producción.

Validación final de código del 12 de octubre: **1,265 pruebas y 816 subpruebas** aprobadas en Frappe **15.117.0** y **16.51.0**, y **64 archivos de pruebas JavaScript** aprobados. `git diff --check` y comprobaciones de sintaxis JS sin errores. Las pruebas SQL `smoke_application_candidates`, `smoke_operational_clarity` (12 comparaciones por componente) y `smoke_accounting_control` pasaron con rollback. El selector de 200 aplicaciones requirió cinco consultas de lotes y una comprobación de reserva en la medición controlada, sin consultas por fila; no es una medida del tiempo de producción. Las nuevas regresiones cubren discrepancias con saldo cero, estados parciales, estado contable verificado, filtros conservados, navegación de casos, acceso real a documentos y datos obsoletos de vistas previas.

## Límites de la validación final

Se restableció el servicio local del ensayo: WSL estaba detenido y `cn-reconciliation-test.local` conservaba el modo mantenimiento. Se iniciaron los Redis del bench y el servidor local, se desactivó mantenimiento únicamente en ese sitio y se verificó HTTP 200 en `/login` desde Windows. Esto confirma disponibilidad, no aceptación visual ni funcionamiento de una sesión autenticada. La pestaña integrada aún mostraba la pantalla anterior de conexión rechazada; se solicitó recarga manual para continuar sin eludir la restricción de la herramienta.

No se probó esta revisión en la versión exacta 15.111.1 de producción ni con una restauración completa de esa base. El ensayo visual usa Frappe 16.51.0; las pruebas de compatibilidad de código se ejecutaron además en 15.117.0. Antes de producción corresponde restaurar un respaldo en ensayo con la versión exacta y repetir la aceptación. No se hizo commit, push ni despliegue. El procedimiento Markdown está actualizado; no se regeneró el Word previo.

## Cierre de la comprobación visual

El 12 de octubre, tras la recarga e inicio de sesión del usuario en el mismo sitio aislado, la herramienta volvió a permitir la revisión normal. Se comprobó en la interfaz real:

- Búsqueda `Gamma`: cuatro casos, conservados al usar «Actualizar consulta». El modal pasó del caso 1 al 2 sin perder la búsqueda; un conjunto de un solo caso deshabilitó Anterior/Siguiente. No se ejecutaron acciones financieras.
- Estado de Cuenta Operativo: nueve columnas iniciales de aplicaciones. «Más columnas» añadió número de cliente, observación y filas de origen, entre otros campos de trazabilidad, sin cambiar importes.
- Estado de Cuenta por Empresa: Gamma conservó 20 partidas pendientes aunque su saldo neto fuese cero (US$920.50 aplicado pendiente y US$920.50 de depósito sin conciliar). Las sugerencias de Estado correspondieron a resultados existentes: Detalle pendiente, Parcialmente resuelto y Pendiente.
- Clasificar pendiente: partida de US$20 con US$20 documentados, cero pendiente financiero y US$15 de saldo a favor por gestionar. El modal explicó que clasificar no devuelve, cobra ni compensa dinero; no se confirmó ninguna acción.
- Se detectó y corrigió el encabezado que quedaba en «Consultando conciliación»: Frappe reutiliza el indicador de lista al abrir el formulario. Ahora la consulta actualiza un resultado efímero en `__onload`, sin modificar importes ni estados persistidos, y refresca el indicador. Se verificó visualmente «Conciliado», manteniendo «Pendiente de registro» y «Parcialmente resuelto» como estados independientes.

Después de esta corrección pasaron otra vez los **64 archivos de pruebas JavaScript** y `git diff --check`. La regresión verifica éxito, error de consulta sin conservar saldo obsoleto y prioridad de cancelación. No hubo cambios Python posteriores a la suite completa registrada arriba. El cierre de estos hallazgos no equivale a certificar el despliegue en producción: se mantienen los límites de versión y datos indicados.

## Estados, KPI y clasificación de reportes

La revisión posterior encontró una partida documentada presentada como «Conciliado» en el formulario y «Documentado» en el reporte operativo. Se corrigió el uso del estado común de conciliación en ambos estados de cuenta, el control contable y las partidas del core en Excel. El tratamiento original permanece separado; un saldo a favor conciliado conserva su importe por gestionar y su situación contable. Una discrepancia con la evidencia original sigue apareciendo como «Revisar distribución».

Los KPI operativos corresponden al tipo y las filas seleccionados, incluso cuando la selección no tiene resultados. No muestran la CxC de aplicaciones como cero al consultar exclusivamente complementarias. Una magnitud desconocida suprime su total monetario y muestra un conteo de importes sin determinar. Las complementarias pendientes se muestran como obligaciones independientes, sin compensarlas por el signo ni restar los saldos a favor.

La categoría y los remanentes ocupan las primeras columnas de la vista compacta de complementarias. El detalle conserva original, utilizado, gestionado, crédito, subcategoría, motivo, tratamiento y evidencia. Los filtros de clasificación, registro y gestión se pueden combinar y permanecen visibles cuando están activos. La explicación extensa del reporte se despliega desde «Cómo se calcula».

Verificación: suite completa de 1,275 pruebas y 816 subpruebas en Frappe 15.117.0, 64 archivos JavaScript aprobados y comprobación focalizada en Frappe 16.51.0 de 60 pruebas y 59 subpruebas. Tras los últimos ajustes de presentación y evidencia, pasaron otras 70 pruebas y 60 subpruebas focalizadas en 15.117.0. Las pruebas SQL de claridad operativa y control contable conservaron importes NIO/USD y filtros con rollback. `smoke_report_classification` verificó la partida guardada: estado conciliado común, US$0 por conciliar, US$15 por gestionar y un registro pendiente. `git diff --check` y sintaxis JavaScript sin errores.

En el navegador del ensayo se comprobó el filtro «Conciliado», la combinación categoría/registro/gestión y «Más columnas», que conserva «Documentado» como tratamiento. No se registraron distribuciones ni gestiones financieras. Son cambios de consulta: no necesitan renombrar documentos ni una migración de saldos. Para cargar los nuevos scripts y código en un servidor actualizado, limpie la caché del sitio, reinicie los servicios correspondientes y recargue el navegador. No se hizo commit, push ni despliegue en producción en esta etapa.

## Bandeja financiera, KPI y seguimiento de inconsistencias — 12 de octubre

- La tarea financiera usa el pendiente común del período en ambas modalidades. La diferencia de cobranza permanece como control de calidad separado. Al agrupar las acciones, el pendiente financiero es el importe principal y no se suma con la diferencia informativa.
- El KPI de complementarias excluye distribuciones inconsistentes de su total de pendientes válidos y muestra conteo e importe observado por separado. La evidencia negativa de una sobreasignación permanece en la fila para revisión.
- Estado de Cuenta por Empresa conserva los créditos documentados con gestión cero cuando su importe contradice la evidencia del core. El caso tiene saldo cero, estado Revisar distribución y observación identificable; no crea una nueva CxC.
- Las CxC por ajuste conservan Gestión externa al filtrar; el corte histórico recupera la subcategoría antes de aplicar los filtros.
- El control contable interpreta el estado actual del depósito con su distribución y conserva el resultado guardado como auditoría. Formulario, lista y calendario admiten las etiquetas anteriores de saldos a favor sin presentar una distribución completa como parcial. Una revisión de detalle pendiente conserva la advertencia.
- Los importes de tareas explican su propósito: cubrir aplicaciones, devolver/aplicar créditos, registrar/verificar asientos o clasificar dinero. La ayuda del signo depende de la categoría. Se traducen lotes, bloques y cartera y se agrupan los dos primeros en Administración de cargas.

Validación: 1,290 pruebas y 816 subpruebas aprobadas en Frappe 15.117.0; 64 archivos de pruebas JavaScript aprobados. Las pruebas SQL de ensayo en 16.51.0 verificaron el pendiente operativo guardado, 12 comparaciones por componente del estado por empresa y el crédito documentado con US$0 financiero, US$15 por gestionar y un registro pendiente. Las regresiones nuevas cubren sobreasignaciones, filtros de gestión/subcategoría, inconsistencias resueltas con saldo cero y resultados antiguos de depósitos sin alterar evidencia original.

En la interfaz del ensayo se comprobó el pendiente operativo Gamma de US$920.50 y la etiqueta Pendiente de cubrir junto a su control de calidad; el depósito de US$100 conserva créditos US$80 y complementarias US$20 y presenta el resultado anterior en auditoría desplegable. No se confirmaron conciliaciones ni se modificaron saldos en esa comprobación. La actualización es de consulta y presentación: no requiere migración de saldos. Se mantiene pendiente la aceptación del despliegue en la versión exacta 15.111.1 de producción. No se hizo commit, push ni despliegue en producción.

## App del Desk y menú lateral nativo de Frappe 16

Se revisaron los hooks, el Dock y los sidebars de ERPNext instalados en WSL,
así como los esquemas y el sincronizador del tag local oficial de Frappe
`v16.5.0`. El ensayo utiliza actualmente 16.51.0: esa versión importa Sidebar y
Dock; la 16.5 original importa Workspace Sidebar. La app incluye ambos formatos,
con los mismos enlaces y sin importar código de ERPNext.

El registro de Apps tiene logo SVG y ruta al workspace, con permiso condicionado
a los roles operativos existentes y lectura del período. El Desktop Icon nativo
permite también acceder desde la cuadrícula anterior. Los nombres de archivo
cumplen la ruta calculada por el limpiador de registros estándar para que el
icono y el sidebar no se eliminen como huérfanos al migrar.

El menú organiza Conciliación, Seguimiento, Reportes, Catálogos y Administración
de cargas. Inicio y Control aparecen primero; todas las partidas tienen icono.
Las secciones sin sangría permiten que el renderizador nativo muestre los iconos
de sus enlaces. Reportes, Catálogos y Administración se plegarán por defecto,
respetando las preferencias de apertura del usuario.

Validación: suite completa de 1,301 pruebas y 816 subpruebas en Frappe 15.117.0,
y 25 pruebas focalizadas posteriores, incluida protección de rutas estándar.
Los esquemas del tag 16.5.0 aceptan todos los campos y tipos de enlace de los 26
elementos. La verificación SQL del ensayo 16.51.0 confirma el registro de la app,
su ruta y logo, el Dock y los 26 elementos resueltos, además de ocultar la app
para Guest. Se comprobó visualmente el icono en `/desk` y su apertura del
workspace con el nuevo menú. Estos cambios no modifican saldos ni conceden roles.
No se hizo commit, push ni despliegue en producción.

## Cierre de revisión: permisos, cierres y navegación — 12 de octubre

Se corrigieron estos riesgos adicionales:

| Hallazgo | Corrección verificada |
| --- | --- |
| Una distribución o recuperación oculta por permisos podía aparentar saldo disponible. | Las consultas de esas dependencias rechazan el saldo no comprobable con un mensaje genérico, sin divulgar documentos ni importes restringidos. |
| El indicador del depósito y el filtro podían usar estados diferentes. | `financial_status` conserva el estado de presentación consultable; `result` permanece como resultado del motor en auditoría. |
| Una diferencia informativa antigua podía bloquear un período financieramente cubierto. | Ámbito y bloqueo explícitos, reclasificación autorizada por supervisor con motivo e historial; los casos heredados siguen bloqueando hasta su revisión. |
| El seguimiento informativo quedaba inmovilizado al cerrar el período. | Se permite gestionar una excepción ya no bloqueante, manteniendo congelados sus importes, identidad, vínculo y el período. |
| Recuperar CxC obligaba a buscar destinos sin conocer el disponible. | Selector de destinos admisibles y vista previa de disponible y remanentes; confirmación con nueva validación. |
| El detalle por empresa exigía demasiado desplazamiento horizontal. | Vista compacta de seis columnas, con matriz completa disponible y los mismos registros. |
| El calendario abría lejos de los meses útiles. | Anclaje inicial, selector Ir al mes y conservación del desplazamiento. |

Las complementarias conservan separados conciliación, registro contable y gestión. En el ensayo visual, una partida de US$20 documentada y conciliada mantuvo US$15 por gestionar; **Clasificar pendiente** propuso **Documentar devolución o aplicación**. El reporte conservó 20 componentes pendientes para Gamma aunque US$920.50 aplicado y US$920.50 depositado sin vincular produzcan neto cero. El filtro de depósitos por **Conciliado con saldo a favor** encontró el mismo documento identificado por su indicador.

Migración: se respaldó y migró únicamente `cn-reconciliation-test.local`. Se compararon 26 tablas CN antes/después, excluyendo solo los campos nuevos de clasificación y presentación: no cambiaron los datos financieros, archivos ni diarios existentes. Repetir los parches conservó los datos. `backfill_deposit_financial_status` y `classify_exception_closure` no concilian, cobran ni resuelven casos. En un despliegue nuevo deben ejecutarse mediante `migrate` después del respaldo y ensayo habitual.

Validación de código: **1,324 pruebas y 820 subpruebas** aprobadas en Frappe **15.117.0**, y **65 archivos de pruebas JavaScript** aprobados. Las regresiones SQL en el sitio aislado **16.51.0** verificaron cobro parcial, compensación, reversión, permisos, empresas pagadoras, control contable, selección de destinos y seguimiento sin cambiar un período cerrado; utilizan rollback. Se comprobó también la interfaz real del calendario, las listas, la clasificación guiada y la vista compacta.

Límite de aceptación: aún debe repetirse la validación en **15.111.1**, con respaldo restaurado de producción, permisos reales y un mes completo contrastado con la balanza externa. Las pruebas de esta revisión no constituyen esa certificación. No se modificó producción ni se hizo commit o push. El procedimiento Markdown está actualizado; no se regeneró el Word anterior.

## Revisión adicional de saldos y evidencia — 12 de octubre

Se reprodujeron y corrigieron cinco hallazgos:

1. **Corte con evidencia restringida.** Una aplicación visible cuyo depósito estuviera oculto podía reaparecer como deuda. El corte comprueba ahora sus dependencias de depósitos y complementarias antes de reconstruir saldos. Si falta acceso a evidencia relacionada, responde «Saldo no determinable: evidencia restringida», sin revelar documentos ni importes. Los documentos ajenos o posteriores al corte no bloquean por sí solos la consulta.
2. **Sobreasignación presentada como deuda.** Un depósito de US$100 con US$110 distribuidos ya no produce una CxC aparente de US$10. Su componente y el saldo neto son N/D; conserva US$10 observados y «Revisar distribución». Esto se propaga al resumen Excel.
3. **Revisión con saldo cero.** Estado de Cuenta por Empresa mantiene los depósitos totalmente distribuidos cuyo detalle o destinos requieren revisión. Conservan saldo cero y cuentan como casos pendientes, sin duplicar dinero.
4. **Cobranza sin conversión.** Una fila solamente en C$ genera una tarea y advertencia independiente en calendario y tarjetas. No se calcula una diferencia ficticia ni se altera la cobertura financiera. El detalle Excel no presenta su equivalente US$ desconocido como cero.
5. **Selector de recuperación CxC.** Descarta en lotes los depósitos agotados, las reservas, los períodos cerrados y las compensaciones consumidas antes de cargar documentos completos. En la regresión de 1,000 depósitos agotados no se carga ningún documento completo. Los candidatos ofrecidos y la confirmación siguen validando permisos y disponibilidad; no es una medición del tiempo de producción.

Se cambió «Ver excepciones» por **Ver pendientes detectados** en la importación, con explicación de la diferencia entre alertas automáticas y excepciones documentadas. La ayuda del período predeterminado aclara que el cierre financiero no depende de completar un control de calidad conforme. Se actualizaron las instrucciones al nombre vigente **Conciliar período predeterminado**.

Validación final: **1,351 pruebas y 820 subpruebas** aprobadas en Frappe **15.117.0**, **66 archivos de pruebas JavaScript** aprobados y `git diff --check` sin errores. `smoke_review_findings` verificó las consultas SQL de ambos selectores, evidencia restringida, revisión con saldo cero, sobreasignación y conteo de filas sin conversión, con rollback. La comparación exacta de todas las tablas CN confirmó que no cambiaron los registros financieros. El corte que incluye todas las fechas del ensayo coincide con el saldo actual; un corte anterior excluye naturalmente operaciones posteriores.

En el sitio aislado **16.51.0**, después de respaldar y recargar únicamente los metadatos de la importación, se comprobó visualmente el botón, la advertencia, la ayuda y el modal con 19 pendientes, importes y filtros. No se guardaron formularios ni se ejecutaron conciliaciones desde el navegador. El servidor local de revisión no tenía conexión Socket.IO; las consultas HTTP funcionaron, pero no se validaron notificaciones en tiempo real.

Estos cambios no requieren una migración de saldos. Para publicar el conjunto pendiente se mantiene el procedimiento de respaldo, ensayo con la versión exacta de producción, `migrate`, limpieza de caché y reinicio de servicios. No se realizó commit, push ni despliegue en producción; sigue pendiente la aceptación en **15.111.1** con una restauración y la balanza externa.

## Corrección de hallazgos prioritarios de reportes — 12 de octubre

**Partidas del core sobreasignadas.** Un uso mayor al importe original o con signo incorrecto ya no convierte el remanente inválido en crédito o cuenta por cobrar. Estado de Cuenta por Empresa conserva el caso como **Revisar distribución**, muestra el importe observado en sus observaciones y deja el componente y el saldo neto afectados sin determinar. Las empresas con evidencia válida conservan sus cifras. El importe original, sus distribuciones y el signo del asiento permanecen disponibles para auditoría. El resumen Excel conserva N/D y la partida aparece para revisión sin un crédito ficticio.

**Transacciones por Empresa.** El estado, los indicadores, el porcentaje, los filtros y el detalle usan la cobertura monetaria actual de cada aplicación, con depósitos vinculados y ajustes confirmados. El texto guardado en `deposit_match_status` se consulta como **Resultado registrado** y no acredita por sí solo una cobertura completa. Por ejemplo, US$100 aplicados y US$80 asignados muestran **Parcial**, **80%** y **US$20 pendientes**, aunque la etiqueta guardada diga Depósito conciliado. Un ajuste confirmado de US$20 más US$80 de depósito cubre los US$100 originales sin duplicar importes.

La evidencia de cuotas operativas se carga en lotes también para aplicaciones con etiquetas de conciliación completa. Una cuota compartida completamente pagada permite identificar la cobertura completa de sus importes vinculados. Los pagos compartidos parciales conservan sus importes individuales sin determinar cuando no existe atribución suficiente; el agregado del mes se mantiene cuando puede comprobarse. Los vínculos que superan la capacidad de la cuota o la evidencia ilegible se muestran para revisión. Calendario y modal usan el mismo contexto anual de la empresa y después seleccionan el mes, para conservar el diagnóstico de vínculos compartidos entre meses.

Validación: **1,370 pruebas y 820 subpruebas** aprobadas en Frappe **15.117.0** y las dos pruebas JavaScript de Transacciones por Empresa. `smoke_priority_findings` verificó en el sitio aislado **16.51.0** una distribución real parcial con etiqueta deliberadamente desactualizada y una partida del core sobreasignada. Consultó calendario, indicadores, filtros, detalle y saldo por empresa; terminó con rollback y los mismos conteos de registros previos. Estos cambios son de cálculo y presentación de reportes; no ejecutan conciliaciones ni requieren un parche de saldos.

## Consistencia de saldos y seguimiento — 12 de octubre

- **Vínculos de aplicaciones:** la validación compartida revisa vínculos negativos, importes superiores al aplicado neto y vínculos que superan la capacidad de la cuota. Antigüedad, estados de cuenta, resumen por cliente e indicadores conservan el aplicado original y dejan la cobertura y el saldo afectados sin determinar. También se revisa la capacidad compartida entre importaciones. Los vínculos a evidencia fuera de los permisos conservan el alcance de la consulta.
- **Recuperación de CxC por ajuste:** un exceso o una recuperación con signo inválido se muestra como Revisar cobros, con el exceso observado y saldo N/D. El importe no se convierte en saldo a favor ni reduce el saldo de otra deuda. Las consultas, el Excel, el calendario, los indicadores y la partida mantienen visible la revisión. Otra liquidación exige corregir la evidencia inconsistente.
- **Totales incompletos:** Antigüedad distingue Total pendiente de determinar y Subtotal conocido US$. La agrupación por empresa deja el total sin determinar cuando alguna fila no tiene importe válido, conserva un subtotal conocido separado y explica si falta conversión o revisar vínculos. Los indicadores de Control usan la misma distinción.
- **Navegación:** el detalle del estado de cuenta recuerda todos los filtros del resumen; Volver al resumen los restaura, incluso los que estaban vacíos. El resumen compacto coloca Saldo neto junto a Empresa y permite encabezados de dos líneas sin recortarlos.
- **Gestión:** Documentar devolución o aplicación es una acción destacada cuando hay saldo a favor pendiente; Revertir gestión se agrupa en Más acciones. Se mantienen los permisos de escritura y confirmación existentes.
- **Lenguaje:** Observaciones sustituye Remark; Depositado sustituye Remitido en el período y en el estado de cuenta operativo. El detalle mensual de aplicaciones reúne compensaciones y diferencias internas en Partidas complementarias US$, con su total calculado usando Decimal; los datos de origen se conservan.

La suite completa se ejecutó con el código oficial de Frappe **15.111.1**, revisión `8831f757fcd7367bb2345823d7f4f71459742a82`, en una copia temporal y utilizando las dependencias instaladas del entorno de pruebas 15.x: **1,383 pruebas y 820 subpruebas** aprobadas. Esto comprueba el código y las APIs de esa versión; no sustituye una restauración completa de producción ni una prueba de carga de 26 mil movimientos.

`smoke_report_balance_integrity` comprobó consultas SQL reales en **cn-reconciliation-test.local (16.51.0)**, la aplicación 100/vínculos 150, la CxC 10/compensación 15, agrupación de antigüedad y Excel. Terminó con rollback y los mismos conteos previos. Se respaldó el sitio aislado antes de refrescar las etiquetas del período; la comparación de contenido de **26 tablas CN** confirmó que no cambiaron sus registros. La revisión visual verificó el retorno a todas las empresas, los encabezados legibles, la acción de gestión con US$15 pendientes y las etiquetas Observaciones/Depositado.

Los **66 archivos de pruebas JavaScript** finalizaron sin fallos y `git diff --check` no detectó errores. Después de los últimos ajustes de seguimiento se repitieron las **57 pruebas y 5 subpruebas** afectadas, también en **15.111.1**, con resultado aprobado.

Estos cambios no necesitan un parche de saldos. Para publicar las etiquetas use `migrate` y limpie la caché/reinicie los servicios según el despliegue. No se desplegó en producción ni se realizó commit o push en este trabajo.

## Corrección de los seis hallazgos de la revisión — 12 de octubre

1. **Corte histórico:** valida primero los vínculos originales, incluidos importes negativos, exceso contra la aplicación y capacidad compartida. La consulta conserva Revisar distribución y N/D cuando la evidencia es inconsistente. No recorta ni completa vínculos inválidos. Reconstruye ajustes confirmados con fecha; solo modifica virtualmente un vínculo único y completamente atribuible. Las capacidades de cobranza se reconstruyen al corte, sin guardar cambios. Una aplicación ignorada no consume efectivo en la proyección.
2. **Estados financieros:** Control Mensual y Pendientes detectados reutilizan la cobertura monetaria de Transacciones por Empresa. Las etiquetas antiguas quedan como auditoría y no ocultan un saldo pendiente. La consulta incluye el contexto de otros períodos/importaciones para detectar vínculos compartidos, mantiene permisos y deja N/D si la evidencia no está disponible. No se ejecuta conciliación al consultar.
3. **Totales incompletos:** cada total afectado se mantiene sin determinar; los subtotales y conteos disponibles se consultan en Ver subtotales conocidos. Las cifras originales NIO no se pierden por faltar su tasa de conversión. Las columnas de resumen/exportación mantienen valores desconocidos, subtotales y conteos separados.
4. **Identidad histórica:** el detalle y su selector pueden identificar a un cliente en una empresa anterior mediante cortes importados o aplicaciones efectivas en importaciones finalizadas. No cambian su ficha actual ni amplían autorizaciones de la pagadora. Los números de empleado históricos se validan solo en su empresa; el crédito puede determinar la empresa beneficiaria cuando su evidencia es única. Se preservan conflictos entre identificadores y ambigüedades reales.
5. **Carga masiva:** la revisión de casos no identificados se guarda en bloques técnicos de hasta 250 filas y se consulta en páginas de 50, con búsqueda y filtro de tipo. Conserva las empresas elegidas al navegar y exige reanalizar antes de confirmar. El API verifica permiso y propietario de la carga; no relee el archivo ni crea importaciones al cambiar de página. Un reintento del mismo bloque de vista previa no duplica la evidencia. Para activar las páginas en una vista previa anterior, genere un nuevo análisis conservando las selecciones.
6. **Control Mensual:** muestra nueve columnas operativas por defecto, con Ver auditoría para ampliar. Ver origen muestra fila original, fila/ID en la importación, documento, asiento, descripción completa, archivo y huella. Exportar auditoría incluye las columnas ocultas; no se pierde la evidencia por compactar la pantalla. La empresa de aplicaciones antiguas se obtiene del mismo contexto financiero, no de los beneficiarios de un depósito.

Validación final: **1,408 pruebas y 820 subpruebas** aprobadas cargando el código oficial de Frappe **15.111.1**; **67 archivos de pruebas JavaScript** aprobados. Se agregaron pruebas específicas de los seis hallazgos. La prueba SQL `smoke_six_review_findings` en **cn-reconciliation-test.local (16.51.0)** verificó US$100 aplicados / US$80 cubiertos / US$20 pendientes con etiqueta desactualizada, el formulario y selector tras cambiar la empresa actual del cliente, y 603 casos de revisión paginada. Terminó con rollback y los mismos conteos de todas las tablas CN existentes al inicio.

La revisión visual confirmó vista compacta, N/D, subtotales consultables, auditoría con fila/ID y la opción de exportar columnas ocultas. La navegación de páginas y conservación de elecciones se verificó en pruebas JavaScript del modal y en consultas SQL del API. No se cargó un archivo financiero ni se ejecutó conciliación desde el navegador.

No se modifica el saldo de ningún registro ni se necesita un parche financiero para estos cambios. Se reinició y limpió caché únicamente en el ensayo local. No se realizó commit, push ni despliegue en producción.

## Origen del depósito y vencimiento de ajustes por cobrar — 12 de octubre

- **Un depósito, varias líneas contables.** Cuando varias líneas del mismo archivo identifican exactamente el mismo efectivo bancario, se conserva un solo depósito con todos sus orígenes contables de solo lectura. El control mensual mantiene cada débito/crédito físico y su fecha contable; el estado de cuenta cuenta el efectivo una sola vez. La vista previa distingue depósitos de líneas contables. La coincidencia incompleta no autoriza agrupaciones automáticas.
- **Revisión de importaciones anteriores.** El parche `review_financial_origins` conserva la evidencia original y señala borradores con posible efectivo duplicado, sin conciliar ni cambiar importes. Hasta verificar la identidad, su saldo financiero queda N/D. «Revisar origen del depósito» permite documentar si es un efectivo independiente o vincular el origen a un depósito existente, conservando archivos y documentos. No modifica distribuciones ejecutadas. Una línea contable posterior conserva su mes original en el control mensual.
- **Cliente histórico.** El saldo a favor puede documentarse en la empresa de origen comprobada por las aplicaciones/cartera, aunque la ficha actual del cliente tenga otra empresa. No cambia la ficha, no omite conflictos entre identificadores ni amplía las empresas autorizadas de la pagadora. El selector y la validación utilizan la misma evidencia.
- **CxC con responsable y fechas separadas.** Un ajuste por cobrar nuevo necesita responsable, compromiso de gestión y vencimiento financiero antes de confirmarse. El vencimiento se hereda del plazo registrado de sus aplicaciones; sin evidencia completa debe verificarse. Reprogramar la gestión no rejuvenece la deuda. Las partidas anteriores incompletas permanecen visibles en seguimiento y no reciben fechas u operadores inventados.

Validación: **1,418 pruebas y 822 subpruebas** aprobadas contra el código oficial de Frappe **15.111.1**; **68 archivos de pruebas JavaScript** sin fallos. La aceptación SQL `smoke_financial_origins` en **cn-reconciliation-test.local (16.51.0)** comprueba consolidación de evidencia, importación repetida, protección de orígenes confirmados, revisión de duplicados anteriores, línea de otro mes, vencimiento inmutable y saldo a favor histórico. Todas sus escrituras terminan con rollback.

Se respaldó y migró únicamente el sitio aislado. La segunda migración confirmó los campos vigentes y comparó antes/después los importes, distribuciones, estados y huellas de depósitos, períodos y partidas sin diferencias. La revisión visual confirmó el modal de origen y los campos separados de vencimiento financiero/gestión en una partida nueva que **no se guardó**. La pestaña temporal se cerró; la pestaña del usuario permanece abierta. No se cambió producción, no se realizó commit ni push. La prueba SQL usa Frappe 16 y no sustituye la aceptación de un respaldo de producción restaurado en Frappe 15.111.1.
