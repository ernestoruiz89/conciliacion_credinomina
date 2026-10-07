# Instalacion y uso

Actualizado el 7 de octubre de 2026. Para los pasos completos y ejemplos financieros, consulte [Manual operativo](procedimiento_operativo.md) y su [versión Word](Manual_Operativo_Conciliacion_Credinomina.docx).

## Requisitos

- Frappe Framework 15 o superior.
- Python 3.10 o superior.
- No requiere ERPNext, Loan Manager ni acceso directo al core de creditos.

## Instalacion

Desde el servidor de Frappe:

```bash
bench get-app /ruta/a/credinomina
bench --site sitio.local install-app credinomina_reconciliation
bench --site sitio.local migrate
```

En una instalación ya usada, haga una copia de seguridad antes de actualizar.
La migración cambia el identificador de cada **Empresa Credinómina** del código
al valor de `employer_name`, conserva `employer_code` como campo único y actualiza
los vínculos de Frappe. No fusiona empresas: si hay nombres vacíos o duplicados,
hay que corregirlos antes de volver a ejecutar `bench migrate`. Para modificar
el nombre después de migrar, utilice **Renombrar**; el código se edita por
separado y sigue sirviendo para reconocer fuentes históricas.

La instalación crea los roles `Operador Credinomina`, `Supervisor Credinomina` y `Eliminar Mov. del Core`.
Asigne `Operador Credinomina` o `Supervisor Credinomina` según las funciones del usuario y configure su idioma como Español.
Verifique también la zona horaria de **System Settings** antes de operar; las
fechas y horas de importación, corte y seguimiento usan la zona del sitio. Para
la operación en Nicaragua corresponde `America/Managua`.
El Workspace **Conciliación Credinómina** aparece en el escritorio para esos
roles y para `System Manager` después de instalar la app o ejecutar
`bench --site sitio.local migrate`. También puede abrirse en
`/app/conciliacion-credinomina`; contiene accesos al tablero, períodos,
importaciones, distribuciones, excepciones, movimientos internos y reportes.

El rol `Eliminar Mov. del Core` es adicional: asígnelo explícitamente solo a quienes deban cancelar o eliminar `CN Accounting Import`, `CN Complementary Item` y `CN Remittance Allocation` importados del archivo contable del core. También Administrator requiere esa asignación. El rol no concede por sí solo permisos normales de cancelación/eliminación ni evita controles de vínculos o períodos cerrados. No cambia el flujo de `CN Accounting Import`, que no es confirmable.

## Actualizar una instalación existente

Desde el directorio de bench, respalde base de datos y archivos, actualice el repositorio de la app y ejecute:

```bash
bench --site sitio.local backup --with-files
git -C apps/credinomina_reconciliation pull --ff-only
bench --site sitio.local migrate
bench build --app credinomina_reconciliation
bench --site sitio.local clear-cache
bench restart
```

Use el nombre real del sitio y la ruta instalada de la app. `bench restart` corresponde a instalaciones administradas por bench; en contenedores reinicie los servicios mediante su despliegue habitual. Recargue el navegador después de actualizar. La migración incorpora los campos nuevos del detalle, la disponibilidad de cartera y el rol de eliminación; no sustituya este paso por limpiar la caché.

Compruebe en un sitio de ensayo la carga masiva con corte obligatorio, la desactivación de cartera y los botones de saldos a favor por fila antes de operar. En producción, conserve los respaldos y verifique los resultados con los permisos de cada usuario.

## Administrar cortes de cartera

Cree el corte, adjunte el archivo y pulse **Importar / actualizar corte**. En un corte existente, **Guardar** actualiza únicamente los campos de cabecera permitidos; no procesa el archivo ni reescribe los créditos o sus contadores. Para aplicar un archivo reemplazado es necesario volver a usar el botón de importación.

El menú **Renombrar** conserva referencias y admite nombres personalizados que se respetan al reimportar. **Es Desactivar** retira el corte de nuevas selecciones y búsquedas automáticas sin borrar filas ni referencias históricas. En un documento existente, la casilla guarda automáticamente su cambio y se puede actualizar incluso si el documento está confirmado. Los cambios de otros campos siguen pendientes de guardar. Desmarcarla habilita nuevamente el corte; no permite duplicar el corte importado de un mismo mes.

En **Carga masiva** de movimientos contables es obligatorio elegir un **Corte de cartera** importado y habilitado antes de analizar, confirmar o reanudar. Las empresas identificadas deben estar presentes en el corte; los casos NO IDENTIFICADA conservan su revisión separada. Las cargas antiguas preparadas sin corte deben analizarse nuevamente. En una importación individual, la selección sigue siendo opcional y, si falta, se busca el corte habilitado del mismo mes o de un mes anterior.

La carga masiva mantiene referencias al archivo original compartido y genera un CSV específico para cada importación. Su aparición en múltiples listas de adjuntos no implica múltiples copias físicas del original. No elimine referencias a archivos como procedimiento de limpieza sin comprobar sus vínculos.

## Orden de uso

Los períodos nuevos se nombran con `nombre corto de empresa-mes-año-consecutivo`, por
ejemplo `HAL-4-2025-01`. El mes no lleva cero inicial y el año corresponde al mes
de cobranza. Si **Nombre corto** está vacío se usa el código de empresa.
El consecutivo comienza en `01` por prefijo, mes y año. Al guardar
un cambio permitido de empresa o mes, el período toma el siguiente consecutivo
de su nuevo grupo y sus vínculos se actualizan. Los nombres anteriores se
conservan mientras no cambien esos campos; no se renombra el histórico en masa.
Se mantienen las restricciones de edición de períodos cerrados o con cobranza
y aplicaciones vinculadas.

### Carga histórica: abril 2025 a agosto 2026

1. Cree un **Período de Conciliación** por empresa y mes de cobranza que vaya a reconstruir; seleccione modalidad **Histórica**. En **Tipo de período histórico**, use **Mensual** para conservar el esquema anterior, **Fecha exacta** para un corte de aplicaciones del core (15, 30 o cualquier otro día), o **Rango de fechas** para agrupar varios días, ambos extremos incluidos. Puede tener varios cortes fechados en un mismo mes de cobranza, siempre que sus fechas no se solapen. Un período mensual puede coexistir con cortes fechados durante una transición; cada aplicación pertenece a un solo período explícito. La fecha de aplicación puede ser posterior al mes de cobranza. No adjunte cobranza ni detalle de deducción.
2. Importe únicamente **Movimientos contables** para las aplicaciones. Antes de cargar, seleccione la **Empresa** y la **Moneda reportada en el archivo**. Para NIO, indique el tipo de cambio manual en C$ por US$; el sistema concilia en US$ y conserva el monto original en C$. Cada importación debe tener una sola empresa y moneda; separe archivos mixtos. Marque **Carga histórica de aplicaciones**. Si un archivo corresponde a un solo mes y corte, indique el período histórico predeterminado. Si mezcla cortes, asigne el período a cada fila de aplicación y use **Conciliar esta empresa**. Este botón guarda los cambios y recalcula las importaciones, períodos, depósitos, excedentes y ajustes de la empresa seleccionada. Al finalizar muestra filas procesadas, conciliadas, pendientes e ignoradas, con motivos y enlaces a las importaciones pendientes. Si una importación anterior tiene vínculos con la empresa pero no tiene la empresa asignada correctamente, debe corregirse antes de recalcular. En la tabla por cliente se muestran nombre, número de cliente, crédito, importe, asiento y recibo cuando la fuente los contiene. Las filas sin período permanecen pendientes.
3. Registre una **Distribución de Depósito** por depósito de convenio, con fecha real, importe, moneda y referencia. Si es NIO, indique la tasa C$/US$; las observaciones y el soporte son opcionales. Un supervisor usa **Confirmar depósito** y después **Conciliar**: son acciones separadas y el borrador no participa en la conciliación. Los destinos manuales se agregan en la tabla **Destinos del depósito**; cada fila puede indicar su propio período. No importe como movimiento contable la planilla bancaria mensual con depósitos de otras empresas. Puede confirmar el depósito sin detalle por cliente; cuando llegue días después, adjunte el detalle e impórtelo en ese mismo depósito. La app registra cuándo se importó. No cambie la fecha de aplicación ni la del depósito para forzar coincidencias.
4. Revise en **Control de Credinómina** las aplicaciones sin depósito. El detalle del depósito puede repartir un depósito entre muchas aplicaciones o varios depósitos sobre una aplicación. Si faltan crédito, cédula y número de cliente en una fila, el nombre/alias debe identificar un único cliente. Los casos ambiguos se asignan manualmente con **Destinos del depósito** y soporte.
5. Documente partidas administrativas y saldos a favor con **Partida Complementaria**, eligiendo su categoría. Desde el depósito puede crear saldo a favor de empresa o cliente; no aumentan ficticiamente lo aplicado al crédito. Cierre el período histórico cuando sus aplicaciones queden cubiertas por depósitos o compensaciones confirmadas vinculadas, y no tenga revisiones o excepciones abiertas.

El saldo histórico **aplicación sin depósito o compensación vinculada** sí forma parte de la CxC de la empresa en esta herramienta, con la misma regla que en operativo. No acredita por sí solo un faltante del trabajador: no se reconstruyó la primera conciliación ni el saldo contractual de su crédito. Conserve los archivos originales y soportes de distribuciones manuales. Una aplicación histórica de agosto de 2026 registrada en septiembre puede seguir vinculada al mes de cobranza agosto y al corte de aplicación de septiembre que corresponda. No cambie las fechas de un corte con aplicaciones asignadas: primero reasígnelas.

### Qué significa «Importado con excepciones»

Este estado no significa necesariamente que falló la lectura del archivo. También
incluye aplicaciones todavía sin depósito conciliado, coincidencias ambiguas y
depósitos con saldo sin distribuir. En **Movimientos contables**, el aviso y el
botón **Ver excepciones** muestran las filas de esa carga, cliente, crédito,
etapa y motivo registrado. Una fila con varios motivos cuenta una sola vez.
Consultar este detalle no guarda ni concilia; después de corregir los datos,
use **Conciliar esta empresa** para actualizar el resultado. Si el estado guardado
ya no coincide con las filas actuales, el aviso lo indica expresamente.

### Pendientes detectados en el período

En **CN Reconciliation Period → Excepciones del período**, la sección
**Pendientes detectados** muestra los resultados guardados de aplicaciones,
cobranzas y depósitos vinculados que requieren atención. Incluye documento de
origen, fila, cliente, crédito e importes en US$, con búsqueda, filtro por tipo
y páginas de 50 registros. Está disponible también en períodos cerrados y solo
consulta datos; no crea excepciones ni recalcula la conciliación.

En los depósitos, el pendiente corresponde al saldo sin distribuir del depósito
completo y puede pertenecer a otros períodos: no debe sumarse al pendiente de las
aplicaciones. **Aplicado neto US$** en esa fila muestra lo efectivamente asignado
desde el depósito a créditos de todos sus períodos, excluyendo partidas
complementarias y ajustes de conciliación; no el importe total original de las
aplicaciones vinculadas. Las **Excepciones registradas** se muestran aparte, debajo, para
dar seguimiento a casos documentados. Ambas secciones respetan los permisos del
usuario y pueden mostrar cantidades diferentes.

### Operación desde septiembre 2026

El botón **Conciliar** de **Distribución de Depósito** procesa únicamente el
depósito abierto: su detalle, destinos y ajustes por tolerancia. Actualiza los
saldos de los períodos afectados considerando las asignaciones ya guardadas de
otros depósitos. Las aplicaciones deben estar vinculadas previamente a sus
períodos desde **CN Accounting Import → Conciliar esta empresa**. En operativo
pueden asignarse directamente con **Período predeterminado de aplicaciones** o
**Período asignado de aplicación** por fila, incluso sin cobranza ni detalle de
empresa. Esa asignación habilita la conciliación 2 y conserva pendiente el control
de la conciliación 1 hasta recibir su base.
El resultado muestra las filas del detalle y los períodos afectados por ese
depósito.

Ambas conciliaciones pueden realizarse en cualquier orden. En el período,
**Conciliación 1: validar aplicaciones** compara contra la base elegida y conserva
los pagos existentes. Cargar o actualizar cobranza y detalle de empresa tampoco
redistribuye depósitos automáticamente. **Cerrar período** exige ambas
conciliaciones completas: no basta con que el depósito esté conciliado o con que
la aplicación coincida con la base. Al llegar la base después del depósito, el
pago directo se conserva y se refleja en su fila identificada sin duplicar cobertura.

1. Configure la empresa y cree un período **Operativo** mensual o quincenal. Descargue **Plantillas → Plantilla de cobranza** desde el período, complete las filas, adjunte el archivo y pulse **1. Cargar cobranza**. Se crean los clientes no registrados, vinculados a esa empresa; la tabla guarda nombre, cédula, número de cliente, número de empleado (si viene), crédito y cuota. El número de empleado es interno de la empresa y puede repetirse en otra. En este paso no hay conciliación ni deducción confirmada.
2. Cuando la empresa responda, adjunte en el mismo período el archivo con `Deducido C$` y/o `Deducido US$`, indique la fecha de esa evidencia y pulse **2. Cargar deducción de empresa**. Puede descargar **Plantilla de detalle empresa** con la cobranza y `Fila ID` precargadas. El nombre del cliente es obligatorio. El número de empleado identifica dentro de esa empresa; si faltan identificadores, solo un nombre normalizado o alias único de la misma empresa permite vincular la fila. Un error ortográfico requiere agregar un alias verificado en **Clientes y alias** y volver a importar. La `Fila ID` exportada también permite identificar con precisión una cuota.
3. Importe las aplicaciones del core con **3. Cargar movimientos contables** en **Movimientos contables** (`CN Accounting Import`). Seleccione la moneda del archivo; si está en NIO, indique la tasa C$/US$ para normalizar las filas a US$. Cada importación individual debe ser de una sola empresa y moneda. **Carga masiva** está en la lista y permite separar por empresa y fecha un archivo de varios meses, conservando un CSV individual por documento. Revise la clasificación: no toda línea contable es una aplicación; también puede ser depósito o partida complementaria. El archivo puede llegar antes o después de la respuesta de la empresa. El core puede agrupar las dos quincenas en una aplicación.
4. Registre el depósito en **Distribución de Depósito** cuando llegue, aun el mes siguiente, con fecha, referencia, moneda e importe. Si está en C$, indique la tasa C$/US$; las observaciones y el soporte son opcionales. Guarde y use **Confirmar depósito** y luego **Conciliar**: mientras sea borrador no participa en la conciliación. En **Períodos del detalle** agregue uno o varios períodos para limitar la búsqueda, independientemente de la fecha del depósito. Descargue **Plantillas → Plantilla de detalle del depósito**, complete los deducidos, adjúntelo y pulse **Cargar detalle del depósito**. También puede generar un detalle desde las aplicaciones pendientes de esos períodos, seleccionando las filas que corresponden. Una fila puede cubrir varias aplicaciones del mismo cliente y crédito si la suma pendiente coincide exactamente; las coincidencias ambiguas se revisan manualmente. Si el detalle llega días después, se carga en el mismo depósito. **Conciliar** procesa solo ese depósito, sin redistribuir los demás.
5. Use **Partida Complementaria** para importes administrativos, ajustes y saldos a favor de empresa o cliente, distinguiendo su categoría. Revise primero **Qué falta hacer** en el tablero, el estado de cuenta y las excepciones. Una aplicación enlazada antes del detalle de la empresa se indica como provisional; no representa una deducción confirmada. Si al cierre del mes falta evidencia o dinero, use **Registrar corte de control** y anote la siguiente gestión. El corte conserva una foto fechada sin impedir cargas tardías. Reserve **Cerrar período** para la liquidación completa.

Al pulsar **Cerrar período**, el formulario queda de solo lectura y no admite
guardados ni nuevas importaciones. Solo un **Supervisor Credinómina** o
**System Manager** puede usar **Reabrir período**; debe indicar el motivo.
La app registra quién lo cerró y reabrió, cuándo y por qué, y restaura el
estado anterior al cierre. Revise la conciliación nuevamente antes de volver a
cerrar.

El reporte **Antigüedad de Saldos** abre en **CxC total**, para ambas modalidades:
histórica y operativa. Incluye **Aplicado pendiente de depósito** y **CxC por
ajustes**. Para las aplicaciones calcula el saldo actual en US$ como
**aplicado neto + diferencias complementarias vinculadas − depósito asignado al crédito**.
Incluye aplicaciones sin detalle de deducción y aplicaciones aún no vinculadas
a una cobranza o período, señalándolas para revisión. No vuelve a sumar una
aplicación repartida entre dos quincenas ni descuenta dos veces un depósito
cuando varias aplicaciones corresponden a una cuota.

El vencimiento usa la **fecha de aplicación del movimiento contable**, no la
fecha de carga ni el mes de cobranza: `grace_days = 10` en **CN Employer** significa
que las aplicaciones de abril vencen el **10 de mayo**. Hasta ese día se muestran
como **No vencido**; el 11 de mayo tienen un día de atraso. La app conserva el
vencimiento y el origen del plazo en la aplicación: cambiar la configuración de
la empresa no altera retrospectivamente un vencimiento ya guardado. En registros
migrados se identifica explícitamente que se usó el plazo vigente al migrar;
no se inventa cuál era el plazo histórico. Las CxC por ajuste usan su fecha
compromiso y quedan **Sin fecha** cuando no existe.
La fecha elegida mide la antigüedad de los saldos actuales; no reconstruye un
saldo histórico a una fecha pasada.

Los depósitos sin asignar no reducen el saldo de un cliente. Las diferencias
cambiarias en revisión siguen pendientes; los ajustes internos por tolerancia
sí se consideran. Si una cuota agrupa aplicaciones con vencimientos distintos
y un pago parcial no identifica cuál cubre, se muestra el saldo en **Sin fecha**
con una observación, sin inventar una distribución. Las filas sin conversión
documentada se muestran para revisión sin atribuirles un importe en US$.

En **Tipo de saldo** se conservan como consultas separadas las cuotas no
deducidas, las deducciones sin depósito asignado y el detalle aún no recibido.
No se suman con la CxC total. Un depósito recibido pero aún sin
detalle puede cubrir saldos pendientes de asignación; estos no prueban por sí
solos una deuda impagada. Las cuotas no deducidas requieren cotejo con el core
antes de calcular provisiones. El reporte no registra asientos.

### Tolerancia automática en US$

En la ficha de cada **Empresa Credinómina**, configure **Tolerancia de conciliación US$**. El valor inicial es **0** (sin ajuste automático) y el máximo admitido es **US$0.10**. La comparación es simétrica: con tolerancia de US$0.01, una aplicación de US$46.52 frente a un depósito de US$46.53 genera **+US$0.01**, y una aplicación de US$46.53 frente a un depósito de US$46.52 genera **−US$0.01**. El signo siempre significa *depósito menos aplicación del core*.

La app crea una **Partida Complementaria** de categoría **Diferencia por tolerancia**, visible en el tablero, las importaciones y los reportes. Es automática y de solo lectura: no se agrega manualmente a Destinos. No altera la aplicación del préstamo, no genera asiento contable y conserva por separado el efectivo realmente depositado. Si el depósito es mayor, solo el centavo físico sobrante que corresponda se clasifica con la partida; cualquier otro excedente sigue sin distribuir y requiere explicación. Si el depósito es menor, no se inventa efectivo. Si necesita registrarla en el core, use **Crear / Ver excepción** y verifique el asiento contra la importación.

El ajuste automático exige un solo depósito registrado en US$, una sola aplicación y un destino inequívoco ya distribuido. Si también existe un movimiento contable de depósito, se coteja con el depósito. No cubre diferencias de tasa C$/US$, referencias ambiguas, repartos de varios depósitos o aplicaciones, ni partidas administrativas. Esos casos permanecen para revisión o distribución manual. Si se cambia la tolerancia o desaparece la coincidencia, la app revierte el movimiento vigente al recalcular; un período cerrado no se altera automáticamente. Revise estos movimientos antes del cierre.

Para la primera quincena el cierre de ciclo es el día 15; para la segunda, el último día del mes. En una empresa mensual la fecha límite de depósito sigue la regla del mes siguiente configurada en la empresa. En una empresa quincenal **registre la fecha límite pactada en cada período**; la app no presupone que ambas quincenas se pagan en la misma fecha. El tablero suma las cifras del mes, pero conserva botones separados para revisar cada quincena. El estado de cuenta y el resumen indican el ciclo de cada movimiento.

Una aplicación del core puede cubrir las dos quincenas. Si el importe en US$ coincide exactamente con los saldos disponibles de Q1 y Q2 del mismo crédito, empresa y mes, la app registra el reparto en la fila de origen y actualiza ambas conciliaciones. Si falta el dato que identifique el reparto o existen varias combinaciones válidas, queda pendiente de revisión.

Cuando la empresa confirme la deducción completa, use **Más opciones → Reconocer cobranza como detalle de la empresa**, indique la fecha de evidencia y confirme. No registra un depósito y no sobrescribe deducciones existentes. En el depósito, **Usar aplicaciones pendientes como detalle** genera las filas seleccionadas de sus períodos; puede editar **Importe del detalle US$** en la vista previa, conservando el pendiente original como referencia y el ajuste en comentarios. Conserve el origen generado y no lo presente como evidencia remitida por la empresa.

### Resolver diferencias en las filas del depósito

Al importar el detalle devuelto por la empresa, los números de crédito de menos de seis dígitos reciben exactamente el prefijo `00`, sin contar el sufijo `-1`: `1807` pasa a `001807-1` y `12230` a `0012230-1`. La regla no afecta al detalle generado desde aplicaciones pendientes. Se muestran **Estado del crédito** y **Fecha de corte de cartera** según el corte del período seleccionado más reciente; si no aparece el crédito, se consulta solo el corte habilitado inmediatamente anterior. Sin coincidencia se muestra **No Identificado**.

Con **Aplicar por antigüedad (FIFO)**, una fila de US$13.05 y aplicaciones disponibles de US$9.62 permite distribuir US$9.62, dejando US$3.43 por resolver. Para destinos manuales fuera de los períodos seleccionados, active **Permitir vínculos manuales con períodos distintos a los seleccionados**, guarde y concilie. Esto no amplía la búsqueda automática ni FIFO.

Desde una fila de un depósito confirmado, **Crear saldo a favor de la empresa** registra el excedente de la pagadora y lo vincula a esa fila. Al confirmar, recalcula su conciliación y muestra el importe reservado por separado del saldo a favor del cliente. Una reserva parcial conserva el resto pendiente. No crea otra aplicación al préstamo ni se agrega a Destinos. Por ejemplo, si una aplicación ya se pagó con el primer depósito y la empresa la remitió otra vez, la segunda fila puede resolverse con este saldo, previa comprobación de quién es el beneficiario del exceso.

Para varios excedentes de clientes con motivo y comentario comunes, seleccione las filas y use **Crear saldos a favor seleccionados**. Se crea una partida independiente por fila. Ambos tipos de saldo a favor comparten los límites de efectivo y de la fila; no se reserva dos veces el mismo dinero.

**Crear partida complementaria → Cuenta por Cobrar a la Empresa** reconoce un faltante con signo negativo y seguimiento de cobro. No equivale al saldo a favor de la empresa, que reserva un excedente positivo.

Para retirar la distribución vigente, use **Conciliación → Desconciliar**, indique el motivo y confirme la advertencia. El depósito permanece confirmado con su evidencia contable. Revise los destinos y concilie nuevamente; los períodos cerrados y las reservas vigentes siguen protegidos.

Cada reejecución de la conciliación recalcula los enlaces a partir de las fuentes efectivas y de las partidas complementarias confirmadas. No genera asientos, recibos, pagos ni modificaciones en el core. Las aplicaciones y los saldos de préstamo se expresan en US$; los depósitos en C$ se convierten con la tasa ingresada. Para cobrar un faltante trasladado a una partida, use **Aplicar cobro / Compensar CxC** desde su origen: solo el depósito realmente distribuido o la compensación vinculada reduce la deuda. Consulte el procedimiento operativo para ejemplos, correcciones y seguimiento.
