# Ensayo funcional en WSL

La revisión más reciente está en **Revisión del 4 de octubre de 2026**, al final
de este documento, y en `docs/revision_sistema.md`. Las secciones anteriores
conservan evidencia y pendientes de sus fechas; no describen todos los controles
ni resultados del código actual.

## Revisión de preparación para producción — 3 de octubre de 2026

Los cambios de endurecimiento se probaron sin modificar producción ni roles.
Esto documenta evidencia local, **no una autorización final de salida**.

| Verificación | Evidencia |
| --- | --- |
| Reglas y regresiones | 741 pruebas Python y 384 subpruebas pasan con Frappe 15.117.0 / Python 3.12.3. Son pruebas unitarias sin sitio, no una instalación completa en v15. |
| Interfaz | 43 archivos de pruebas JavaScript pasan; validan acciones, escape de texto, fechas y navegación. No sustituyen revisión visual en el navegador del servidor destino. |
| Navegador local | Filtros de trabajo actualizan el contador; una partida sintética muestra US$20 documentados, US$5 gestionados y US$15 pendientes. Modal de gestión, historial con antes/después y consulta de cortes privados comprobados visualmente en Frappe 16.36.0. No se registraron devoluciones reales. |
| Integración | Sitio descartable `cn-reconciliation-test.local`, Frappe 16.36.0 / Python 3.14.6 / MariaDB. |
| Migración local completa | `migrate --skip-search-index` terminó sin omitir parches ni fixtures. Los parches de seguimiento, vencimientos y barra lateral constan en Patch Log con `skipped=0`. Se repitieron después las pruebas reales de crédito de empresa, cierre y cancelación. |
| Concurrencia real | Dos conexiones intentan asignar dos depósitos de US$100 a una aplicación de US$100. La segunda rechaza la lectura antigua; al reintentar, US$100 quedan asignados y US$100 sin distribuir. No hay doble pago. |
| Créditos de empresa y cliente | Reserva del efectivo, aplicación tardía sin consumir el crédito, devolución parcial y total, separación de pendiente de gestión y efectivo explicado. |
| Cierre y cancelación | Histórico y operativo; depósito tardío, período cerrado protegido, recálculo acotado y conservación de empresas, períodos y depósitos ajenos al caso. |
| Pago mixto | US$137.33 originales = US$111.32 de depósito + US$26.01 de ajuste; cancelación conserva efectivo y restaura el neto. |
| Bitácora y cortes | Distribución antes/después; repetición sin cambios no agrega eventos. Excel/JSON privados conservan el corte aunque llegue una aplicación posterior; se verificó la huella del JSON. |
| Migración de vencimientos | Ejecución repetida sobre 192 filas de ensayo produce los mismos valores. Además, una aplicación antigua sintética del 30/04/2025 con empresa identificada y plazo 12 recibe vencimiento 12/05/2025; repetir el parche después de cambiar el plazo a 20 conserva el día 12. Esta prueba se revierte. No inventa el plazo histórico. |
| Carga reanudable | 60 documentos, tres bloques; interrupción de bloque y fallo de encolado después del commit, sin duplicados ni pérdida de empresas seleccionadas. |
| Volumen | 26,000 filas → 260 importaciones, 26 bloques confirmados; 66.2 s antes de limpieza, RSS pico 438.5 MiB. Cero relecturas del archivo original al crear los bloques. Resultado sintético local, no garantía de tiempo en producción. |

Las pruebas de flujo se revierten. Las de concurrencia y carga reanudable
necesitan commits reales y eliminan únicamente sus documentos sintéticos al
terminar. No se limpiaron colas ni datos ajenos a las pruebas.

La revisión visual usó un servidor temporal sin Socket.IO: se observaron los
avisos esperados de conexión al servicio en tiempo real, por lo que no se
certifica ese componente. La revisión encontró etiquetas antiguas en la barra
lateral generada por v16, aunque el workspace principal ya estaba actualizado.
El parche `refresh_legacy_workspace_sidebar` corrigió los accesos estándar a
movimientos contables, depósitos y partidas complementarias; se comprobó su
persistencia tras la migración, conservando los accesos personalizados. En v15,
sin `Workspace Sidebar`, no hace cambios. Las pantallas revisadas y sus consultas
funcionaron. La disponibilidad
de enlaces de cortes se comprobó en el navegador; el contenido y las huellas se
verificaron en la prueba de integración, no mediante una descarga del navegador.

Antes de migrar se generó un respaldo local del sitio de ensayo con
`backup --with-files`. Quedó en `/tmp/cn-pre-migration-backup.7I93I6` dentro de WSL:
base SQL comprimida de 9.2 MiB, archivos privados de 25.2 MiB y configuración.
Se comprobó la descompresión completa del SQL y la lectura de los archivos TAR
(1,173 entradas en el privado). Este respaldo es temporal y contiene configuración
confidencial: no se incluye en Git ni debe publicarse. **No se ha restaurado**;
la lectura de los archivos no sustituye una restauración. La búsqueda web se
omitió durante la migración para no añadir trabajo a la cola local ya saturada;
su reconstrucción y Socket.IO siguen pendientes de validación en el entorno destino.

### Pendientes para autorizar producción

1. Confirmar versión exacta del servidor y ejecutar instalación/migración y los
   flujos en un sitio de ensayo de esa versión. Existe un entorno v15 local,
   pero no se ha instalado la app en un sitio nuevo v15 ni se han modificado
   sitios de otras aplicaciones. La creación de un sitio aislado requiere
   acceso administrativo de base de datos que no está disponible en esta sesión.
2. Respaldar y **restaurar** base, configuración y archivos privados en otro
   sitio aislado; abrir los archivos de origen, cortes y soportes y cotejar
   sus hashes. Conservar Version. Crear un respaldo sin ensayar la restauración
   no cumple este punto.
3. Comparar débitos y créditos originales C$ y convertidos US$ del reporte de
   movimientos con un mes completo de la balanza externa. Documentar partidas
   excluidas y diferencias; no certificar integridad solo porque todo quedó
   conciliado dentro de la app.
4. Validar en navegador destino: filtros de pendientes, balance de una
   complementaria, gestión parcial de empresa, historial de depósito y descarga
   de cortes. Confirmar legibilidad, ausencia de errores y navegación desde
   calendario/modales.

### Repetición de pruebas de esta mejora

Desde el Bench de ensayo, con la migración aplicada, pueden ejecutarse:

```bash
bench --site cn-reconciliation-test.local execute credinomina_reconciliation.testing.smoke_company_credit.run
bench --site cn-reconciliation-test.local execute credinomina_reconciliation.testing.smoke_client_credit.run
bench --site cn-reconciliation-test.local execute credinomina_reconciliation.testing.smoke_concurrent_deposits.run
bench --site cn-reconciliation-test.local execute credinomina_reconciliation.testing.smoke_period_closure.run
bench --site cn-reconciliation-test.local execute credinomina_reconciliation.testing.smoke_complementary_cancellation.run
bench --site cn-reconciliation-test.local execute credinomina_reconciliation.testing.smoke_resumable_accounting.run
bench --site cn-reconciliation-test.local execute credinomina_reconciliation.testing.smoke_accounting_batch_scale.run
```

No ejecutar los generadores en producción. Los ensayos que hacen commits están
limitados por código al nombre del sitio descartable. Compruebe espacio y colas
antes del ensayo de volumen.

## Evidencia de simulaciones anteriores

Sitio aislado: `cn-reconciliation-test.local`, Frappe Framework 16.33.1. Las
únicas aplicaciones instaladas son `frappe` y `credinomina_reconciliation`;
no se usa ERPNext ni Loan Manager. El asistente inicial se completó en
español de Nicaragua, zona `America/Managua` y moneda base NIO. Los datos son
sintéticos y algunas fechas futuras se usan deliberadamente para reproducir
la secuencia de cobranza, aplicación y depósito; no representan operaciones
reales de la IMF.

## Cobertura y resultados

| Escenario | Resultado comprobado |
| --- | --- |
| Tres empresas, 20 clientes cada una, septiembre–noviembre 2026 | 9 períodos, 180 filas de cobranza, 171 aplicaciones y 9 depósitos. |
| Detalles que llegan después de los depósitos | 8 depósitos del mes siguiente conciliados con detalle simulado tres días después y 1 conservado como `Detalle pendiente`, con US$920.50 sin distribuir; no se inventa el cliente destinatario. Una referencia única tampoco libera la asignación automática sin detalle. |
| Archivos XLSX procesados por las cuatro acciones | 20 filas de cobranza, 20 de deducción, 19 aplicaciones y 20 filas de detalle integrador. El depósito termina conciliado por US$920.50; la fila sin deducción permanece visible. |
| Histórico con corte de fecha exacta | Dos aplicaciones por US$83 y un depósito posterior por US$83; el período termina `Historico conciliado` sin reconstruir una deducción antigua. |
| Monedas | Un depósito de C$3,650 con tasa documentada de 36.5 equivale a US$100; sin evidencia de tasa se rechaza. Los formularios y reportes muestran US$ y C$ según cada campo, aunque el sitio tenga NIO como moneda base. |
| Permisos | Operador y supervisor consultan tablero y antigüedad; ambos cargan fuentes. Solo supervisor puede confirmar depósitos. |
| Reportes | El resumen devuelve 9 períodos, el estado de cuenta 180 filas y la antigüedad 37 saldos abiertos para los tres meses. Funcionan los filtros por empresa y cliente. |
| Interfaz | Workspace, tablero, tres reportes y formularios de período, importación y depósito abiertos en navegador de prueba sin errores JavaScript. |

La antigüedad mostró US$805.50 de cuotas no deducidas al trabajador y
US$920.50 de deducciones sin depósito **asignado por cliente**. El último monto
puede estar cubierto por el depósito recibido sin detalle: no se suma al
depósito ni se presenta como CxC confirmada a la empresa. El reporte usa
bandas excluyentes y permite revisar cada cliente y empresa.

## Comandos de verificación

Ejecute estos comandos desde el Bench, solo en el sitio de ensayo ya poblado:

```bash
bench --site cn-reconciliation-test.local migrate
env/bin/python -m unittest discover -s apps/credinomina_reconciliation/tests -q
bench --site cn-reconciliation-test.local execute credinomina_reconciliation.testing.simulate_three_employers.audit
bench --site cn-reconciliation-test.local execute credinomina_reconciliation.testing.import_generated_files.audit
bench --site cn-reconciliation-test.local execute credinomina_reconciliation.testing.simulate_historical.audit
bench --site cn-reconciliation-test.local execute credinomina_reconciliation.testing.smoke_roles.run
bench --site cn-reconciliation-test.local execute credinomina_reconciliation.testing.smoke_currency.run
```

Los generadores `run` de los ensayos crean datos en un sitio descartable;
`simulate_three_employers.run` admite repetición, mientras los otros se
ejecutan una sola vez. Las funciones `audit` son de lectura. Ninguno de
estos comandos debe ejecutarse en producción.

El control de antigüedad es **operativo**, no un cálculo de provisión ni una
fotografía contable histórica. Para registrar provisiones de cartera se debe
contrastar la cuota no deducida con el saldo y los días de mora del crédito en
el core y aplicar la política vigente de la IMF. La app no presume tasas de
provisión ni genera asientos contables.

## Validación previa a producción: octubre 2026

Objetivo de compatibilidad informado: **Frappe 15.111.1**. Se ejecutaron 765
pruebas Python y 408 subcasos cargando el checkout oficial de esa versión
(`8831f757fcd7367bb2345823d7f4f71459742a82`), además de 44 archivos de pruebas JS.
Los siguientes ensayos transaccionales también pasaron en un sitio nuevo con
esa versión exacta, Python 3.12 y MariaDB 10.11.14. Esta última versión genera
una advertencia de compatibilidad del framework que debe contrastarse con
la versión del servidor de producción.

- `smoke_uncertain_deductions.run`: CxC y detalle incierto coinciden entre las
  fórmulas Python y los agregados SQL; no inventa deuda confirmada.
- `smoke_complementary_exceptions.run`: distingue comprobante informado y
  asiento verificado, conserva la evidencia y evita contar dos veces el asiento.
- `smoke_client_credit.run`: reversión de gestiones sin borrar el historial ni
  liberar el depósito, tanto en modalidad histórica como operativa. Verifica
  también la posición del cliente: aplicación cubierta y saldo a favor separado.
- `smoke_complementary_compensation.run`: reversión pareada, reintento
  idempotente, saldo a fecha y protección de los importes originales.
- `smoke_report_pagination.run`: 1001 filas leídas en varias páginas con iguales
  totales en antigüedad y resumen. Las regresiones unitarias cubren además 10001
  períodos y 100001 filas, por encima de los antiguos topes.

Estos ensayos revierten sus datos al terminar. La recarga previa de metadatos
de los doctypes modificados debe hacerse únicamente en el sitio de ensayo.

También pasaron los ensayos de saldo a favor de empresa, cancelación acotada,
corte/reimportación, clasificación contable, líneas contables similares y
concurrencia. Dos conexiones intentaron cubrir la misma aplicación de US$100
con depósitos distintos de US$100: una se aceptó y la otra rechazó el estado
obsoleto. El reintento dejó US$100 asignados y US$100 sin distribuir, sin doble
cobertura. La prueba de concurrencia elimina únicamente sus propios registros
sintéticos al terminar.

### Migración y restauración comprobadas

Se instaló el baseline de la app `0a53748fa0dafe7659a9c148a67b322dc5bb443e`
en `cn-migration-v15.local`, dentro de un bench temporal y con MariaDB y Redis
propios, accesibles únicamente por sockets. Se crearon datos sintéticos antes
de migrar: aplicado US$80, depósito US$100, asignación US$80, saldo a favor US$20,
un asiento informado sin evidencia del core y una compensación pareada US$30.

La migración al código actual conservó los importes, vínculos y compensaciones.
Inicializó el seguimiento del saldo sin inventar responsable ni fecha compromiso,
distinguió el asiento informado y fijó el vencimiento con origen migrado explícito.
Se revirtió la compensación antigua con reintento idempotente, se documentó una
devolución parcial US$5 y se guardó un corte privado JSON/Excel. Una segunda
migración dejó todos los registros de negocio sin cambios.

El respaldo de base, públicos y privados se restauró en otro sitio vacío,
`cn-restored-v15.local`. Se compararon todos los campos de los registros CN,
Version y File: 24 tablas, 16 versiones y seis archivos por SHA-256. Coinciden
antes y después de migrar el destino. Los scripts y resultados detallados del
ensayo se conservan en `tmp/acceptance-v15`, fuera de Git. Este ensayo no equivale
a restaurar un respaldo real de producción.

### Recuperación de carga interrumpida

`smoke_accounting_batch_recovery.run` pasó en Frappe 15.111.1. Genera 56 filas
sintéticas y confirma el primer bloque de 25 documentos. Otro proceso escribe
el segundo bloque y termina abruptamente con código 137 antes del commit.
La base conserva solo el primer bloque y libera el bloqueo transaccional.
Al reanudar se completan exactamente las 56 filas originales, US$560, sin
duplicados; se conservan las empresas elegidas manualmente y los CSV individuales.
La entrega tardía del intento anterior se ignora. La prueba elimina solamente
sus propios registros sintéticos al terminar. No provoca ni certifica un OOM real.

### Navegador con la versión exacta

Se compiló el frontend del checkout Frappe 15.111.1 y se sirvió el sitio restaurado
en `localhost:8003`, independiente del demo v16 en `localhost:8002`. Se verificó:

- Calendario 2026 con ocho gestiones anteriores en la pestaña de trabajo.
- Depósito US$100 = asignación US$80 + saldo a favor de empresa US$20.
  Gestión US$5 y pendiente US$15 se muestran aparte de la distribución del efectivo.
- Navegación mes → depósito/período → volver al mes → salir de pantalla completa.
  Los modales enlazados no animan su apertura/cierre: en Bootstrap 4 los clics
  rápidos durante una animación podían dejar diálogos superpuestos. Se reprodujo
  el fallo y se comprobó el recorrido rápido después del ajuste, además de pruebas JS.
- Formulario de saldo de empresa: estado financiero, contable y de gestión
  separados; reversión exige gestión exacta, fecha y motivo. No se confirmó otra
  reversión desde esta revisión visual.
- Estado de cuenta global con CxC US$0 y saldo a favor pendiente US$15. El filtro
  del cliente muestra su aplicación y depósito US$80, sin atribuirle el saldo de empresa.

La revisión visual es de estos recorridos, no de todos los formularios ni de
todas las resoluciones de pantalla. Socket.IO no está ejecutándose en el bench
temporal; las notificaciones en tiempo real no están certificadas por este ensayo.
Para las pruebas unitarias con el framework real se inicializó el contexto local
de Frappe antes de ejecutar pytest (`tmp/acceptance-v15/run_unit_tests.py`).
Ejecutar unittest directamente sin ese contexto provoca errores de `local.flags`
en los métodos decorados, aunque no se utilice la base de datos.

La prueba con los archivos reales de marzo de 2026 queda restringida a la
cuenta **160209013004**. Los originales se conservan sin modificaciones;
el CSV de prueba conserva la fila original. Se verificó el cuadre del origen
contra la balanza y posteriormente la importación y el reporte real de la app.
Los archivos financieros y resultados detallados se mantienen en `tmp/`,
excluido de Git. No se ha realizado esta carga en producción.

La comprobación adicional `tmp/acceptance-march-2026/preflight_parser.py` compara
las 3262 filas físicas del CSV autorizado con el lector de la app: 3140 aplicaciones,
84 depósitos y 38 ajustes. Conserva fechas, cuenta, importes originales, descripción
completa, comprobante, TMOV y TDOC. Débitos 5706568.32 y créditos 5645378.89
coinciden con el control de origen. Los encabezados y notas revisados no declaran
explícitamente moneda ni tipo de cambio; el usuario confirmó NIO y tasa 36.6243
antes de la importación.

### Cuadre del reporte con el mes real autorizado

`tmp/acceptance-march-2026/import_and_verify.py` ejecutó la carga masiva real en
el sitio temporal Frappe 15.111.1, sin conectar a producción: 131 bloques,
3140 aplicaciones, 38 complementarias y 84 depósitos. Total: **3262 movimientos**.
No se confirmó ni concilió automáticamente ningún depósito o complementaria.

| Control | C$ originales, iguales a balanza | US$ convertidos por fila |
| --- | ---: | ---: |
| Débitos | 5,706,568.32 | 155,813.34 |
| Créditos | 5,645,378.89 | 154,142.96 |

El detalle, resumen y KPI del reporte coinciden. Se compararon todas las líneas
conservando multiplicidad, fechas, comprobantes, descripciones completas y valores
originales y convertidos con Decimal/ROUND_HALF_UP. Se verificaron los 3262 números
de fila originales en base de datos, sin pérdidas ni repeticiones añadidas.
No hay conversiones faltantes ni conflictos de identidad de evidencia. Tres CSV
individuales muestreados conservan sus filas originales. Una nueva ejecución de
verificación utiliza el lote completado y no vuelve a importar. Los SHA-256 de
ambos XLSX originales no cambiaron. La evidencia detallada permanece fuera de Git.

Este ensayo acredita **integridad de carga contable**, no la conciliación financiera
de marzo ni la identificación de los convenios. Sin cartera/catálogo real en ese
sitio, todos los movimientos quedaron como NO IDENTIFICADA. El tipo de movimiento
visible en el reporte no debe confundirse con el tipo de documento que lo conserva.
El total US$ se calcula por fila y puede diferir de convertir un único total C$.
Las llamadas de trabajo se ejecutaron directamente con cola/realtime sustituidos:
no es una certificación de workers de producción ni de notificaciones Socket.IO.

La definición confirmada por el usuario separa cobranza informativa de CxC:
aplicado US$100 menos compensación confirmada US$20 y depósito US$30 deja
CxC US$50. La regresión verifica este resultado en ambas modalidades, aunque
la cobranza sea distinta o su detalle sea inconsistente. No usa complementarias
administrativas ajenas al vínculo para reducir el saldo ni descuenta dos veces
el ajuste ya incluido en el aplicado neto.

## Revisión del 4 de octubre de 2026

Frappe exacto **15.111.1**, Python 3.12, MariaDB 10.11.14 y Redis propios por
sockets, sin modificar producción, permisos ni servicios de otros benches.
Se aprobaron **907 pruebas Python y 540 subcasos**, y **51 archivos JS**.
La revisión visual utilizó el frontend compilado de esa versión en
`localhost:8003`, no el demo v16.

Se añadieron pruebas SQL de protección de evidencia y resultados calculados,
cobro parcial y compensación de CxC por ajustes, cancelación y reintento,
restricción por empresa de partidas genéricas, roles existentes, consultas
acotadas de saldos y páginas reales de aplicaciones. Dos conexiones concurrentes
intentaron cobrar una misma CxC; el segundo intento obsoleto fue rechazado y el
reintento no sobrecobró. La bandeja, los reportes y KPI conservan la CxC por
ajuste después de conciliar el depósito original.

`smoke_accounting_batch_scale` importó 26,000 filas sintéticas en 260 documentos
y 26 bloques, conservando empresas seleccionadas y CSV individuales. Tiempo
108.4 s antes de limpieza; pico RSS 405.7 MiB. No hubo relecturas del original
al crear cada bloque. La prueba hizo commits reales y eliminó únicamente sus
registros identificados por su huella. No se ejecutó mediante workers RQ reales.

La medición de lecturas de 100,000 aplicaciones sintéticas obtuvo calendario
2.1325 s, bandeja 1.5639 s, primera página 0.5429 s y última 0.5919 s. Las páginas
leen solo 100 filas y cuentan las 100,000; antes de corregir la paginación, cada
petición reconstruía el tablero. Con 26,000 filas la página bajó aproximadamente
de 0.38 s a 0.11 s. Estas cifras no incluyen una importación real de 100,000 líneas
ni garantizan tiempos en producción. Depósitos y trabajos todavía reconstruyen
sus conjuntos al paginar; no se afirma que todo el tablero tenga paginación SQL.

El respaldo actual `20261004_210032` de base, públicos y privados se restauró en
`cn-latest-restore.local`, con una nueva base y socket propios. Se comparó todo
el contenido CN más Version/File: **25 tablas**, **272 versiones** y **dos archivos
de evidencia** por SHA-256, idénticos antes y después de migrar; una segunda
migración tampoco cambió los datos comparados. El ejemplo conservó CxC original
US$10, cobro US$3 y pendiente US$7. Es un conjunto sintético con tablas vacías,
no una copia de producción ni una prueba de credenciales cifradas.

Se comprobó visualmente la posición de CxC, el modal de liquidación, filtros de
antigüedad, calendario ampliado, retorno al mes y cifras coherentes con el
ejemplo. Los roles reales de operador y supervisor se probaron en SQL sin editar
DocPerm. El protocolo completo y los límites de autorización de despliegue están
en `docs/revision_sistema.md` y `docs/production_acceptance_plan.json`.
