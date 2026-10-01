# Conciliación Credinómina

App independiente para **Frappe Framework** que concilia la cobranza de créditos
por deducción de planilla con las aplicaciones del core y los depósitos de las
empresas. Está diseñada para una IMF con core de créditos propio: **no requiere
ERPNext ni Loan Manager, no contabiliza asientos y no modifica los préstamos**.

Cuando una empresa descuenta una cuota en abril y remite el dinero en mayo,
la app conserva separados el mes de planilla, la deducción al trabajador,
el depósito y la aplicación en el core. Así se puede distinguir una cuota
no deducida de una deducción ya realizada pero aún no remitida o aplicada.

## Modalidades de trabajo

| Período | Qué se concilia | Qué no se presume |
| --- | --- | --- |
| **Histórico: abril 2025 – agosto 2026** | Aplicaciones del core contra depósitos registrados con soporte. | No se reconstruye la cobranza ni la deducción de planilla; una aplicación sin depósito no se presenta como deuda del trabajador o de la empresa. |
| **Operativo: desde septiembre 2026** | Cobranza, detalle de deducción y aplicación; después, aplicación contra depósito. | Una deducción no se convierte en pago aplicado al crédito hasta que el core lo confirme. |

La cobranza puede ser **mensual o quincenal** por empresa. Cada quincena tiene
su propio período y fecha límite de depósito. Si el core agrupa ambas quincenas
en una aplicación, la app la reparte solo cuando crédito, empresa, mes e
importes permiten un cruce único.

## Qué permite conciliar

- Pagos parciales, un depósito para varias cobranzas y varios depósitos para
  una cobranza. Cada **Distribución de Depósito** representa un depósito. Su
  detalle por cliente identifica automáticamente los destinos; la tabla de
  destinos manuales queda disponible para excepciones. Los repartos ambiguos
  no se adivinan.
- En **Distribución de Depósito → Destinos**, use **Vincular detalle y destinos**
  para resolver una fila con las partidas seleccionadas manualmente. Elija la
  fila, marque uno o varios destinos, guarde y pulse **Conciliar** después de
  confirmar el depósito. La suma vinculada debe coincidir con el importe de la
  fila en US$ y los destinos deben corresponder a su cliente y empresa. El
  resultado reutiliza las asignaciones existentes y deja constancia del vínculo
  manual. Al volver a importar el archivo se eliminan los vínculos del detalle;
  los destinos se conservan para revisarlos y vincularlos de nuevo.
- **Movimientos contables** como única fuente de aplicaciones. El depósito se registra directamente con
  referencia, fecha, empresa, moneda e importe; el detalle/soporte puede
  adjuntarse después, sin cambiar la fecha del depósito. No
  hace falta importar el Excel bancario mensual (que mezcla otros depósitos).
- Aplicaciones y saldos de crédito en **US$**. Un depósito en **C$** se convierte
  para la conciliación con la tasa indicada en el depósito. Las
  diferencias cambiarias quedan para revisión de cada caso.
- **Partidas Complementarias** para importes depositados y contabilizados en
  otro asiento, sin registrarlos ficticiamente como pago del préstamo.
  En **Distribución de Depósito → Destinos → Crear partida complementaria**, un
  modal permite crear, confirmar y agregar la partida al depósito en una sola
  operación (requiere permisos de creación y confirmación de partidas).
  Use **+10** si se aplicaron US$90 y se depositaron US$100; use **−10** si se
  aplicaron US$100 y se depositaron US$90. Las partidas negativas se distribuyen
  explícitamente y el importe neto debe respetar el depósito y los saldos.
  Agregue primero el ajuste negativo y después seleccione las aplicaciones.
  El asiento es opcional: la partida queda **Pendiente de registro** hasta
  completar el campo **Asiento contable**, editable incluso después de confirmar.
  La lista de partidas incluye el filtro **Pendientes de registro** para dar
  seguimiento; conciliar el depósito no da por registrado el asiento en el core.
  La cobranza administrativa se agrega en **Destinos**, no como una fila ficticia
  en **Detalle por cliente**. Por ejemplo, US$103.16 de clientes y US$16.84 de
  partida complementaria cubren un depósito de US$120.00. Solo se reconoce esta
  cobertura cuando la partida está confirmada y el destino queda **Aplicada**;
  el total del detalle sigue siendo US$103.16. El período podrá cerrarse cuando
  también estén cubiertas sus demás aplicaciones y no existan bloqueos pendientes.
- **Partidas complementarias** también permiten documentar un **Saldo a favor
  de la empresa**, desde el mismo modal del depósito. Este concepto se vincula
  directamente al depósito confirmado, sin agregarse a los destinos ni al
  detalle de clientes: no es pago de crédito ni ingreso administrativo. El
  excedente sin explicar sigue sin conciliar.
- Excepciones detectadas antes del depósito, con traslado de comentarios al
  detalle del depósito cuando el faltante corresponde al mismo caso.

Los antiguos registros de `CN Deposit Surplus` se migran a Partidas Complementarias
al ejecutar `bench --site <sitio> migrate` (haga un respaldo antes de actualizar).
Se conservan sus identificadores, importes, estados, vínculos y soportes sin
recalcular períodos cerrados. Se retira el DocType anterior; su tabla SQL queda
como archivo de recuperación y cada partida migrada conserva una copia del registro
original. La cobranza administrativa sigue siendo una partida distribuible en
**Destinos**, fuera de `detail_rows`, que es exclusivamente el detalle de pagos por cliente.

La empresa puede configurar una **tolerancia automática en US$** entre 0 y
US$0.10. Es simétrica: con US$0.01, depósito de US$46.53 frente a aplicación
de US$46.52 produce un movimiento **+US$0.01**; en el caso inverso produce
**−US$0.01**. El signo significa *depósito menos aplicación*. Es un movimiento
interno y trazable, no un asiento contable ni un cambio en el core. Solo se
crea con un depósito y una aplicación inequívocos en US$; no resuelve
diferencias cambiarias ni repartos múltiples.

Estos ajustes se consultan en **Partidas complementarias**, categoría
**Diferencia por tolerancia**. Se generan y revierten automáticamente,
son de solo lectura y muestran **No requiere registro** contable.
No deben agregarse manualmente a los destinos del depósito: su efecto ya
está incluido en la conciliación. Los ajustes manuales siguen usando
**Ajuste de conciliación** y conservan su seguimiento contable.

Al actualizar y ejecutar `bench --site <sitio> migrate`, los antiguos
movimientos de conciliación se trasladan a esta categoría conservando sus
identificadores, importes, estados y referencias de seguimiento, sin recalcular
períodos cerrados. El DocType anterior se retira y su tabla SQL se conserva
como respaldo de recuperación.

Si la empresa no devuelve a tiempo el detalle de planilla, un depósito
registrado con soporte que cubra **exactamente toda la cobranza** puede sustentar
un reconocimiento provisional y justificado. Se muestra como **deducción
inferida por depósito**, nunca como descuento individual confirmado por la
empresa, y puede revertirse o sustituirse cuando llegue el detalle real.

## Archivos de entrada

Se admiten archivos `.xlsx`, `.xls` y `.csv`. El archivo de cobranza que se
envía a la empresa contiene estas columnas:

`Nro. Cliente`, `Nro. Empleado` (opcional), `Nombre y Apellidos del Cliente`, `Nro Cédula`,
`Nro. Crédito`, `Nro. cuota`, `Nro. de cuotas totales`,
`Monto de la cuota en US$`, `Monto de la cuota en C$`, `Comentarios`,
`Referencia de Aplicación` y `Comentario de Aplicación`.

La exportación agrega `Fila ID` para enlazar la respuesta sin depender del
nombre. La empresa devuelve el mismo archivo con `Deducido C$` y/o
`Deducido US$`. Para aplicaciones nuevas se importan solo **Movimientos contables**. El
detalle del pago de la empresa se adjunta e importa **dentro de cada depósito**,
no como fuente bancaria separada. Si contiene ambos deducidos, US$ es el
importe de conciliación y C$ es informativo: no se suman. Si solo contiene C$,
indique la tasa documentada en el depósito para convertirlo a US$.
Antes de cargar **Movimientos contables**, seleccione la **Moneda reportada en
el archivo**. Si es NIO, indique el tipo de cambio manual en C$ por US$ y su
evidencia; las filas se normalizan a US$ para conciliar y conservan el monto
original en C$. Una importación debe contener una sola moneda; separe archivos
que mezclen monedas.
Cada cliente pertenece a una empresa de convenio. El número de empleado es
su identificador interno en esa empresa, distinto del número de cliente; puede
repetirse en otra empresa. Los nombres y alias se buscan solo dentro de la
empresa correspondiente.

### Carga masiva de movimientos contables

Desde la lista o el formulario de **Importación contable**, use **Carga masiva**
para subir un archivo con aplicaciones de varios meses o empresas:

1. Adjunte el archivo y seleccione USD o NIO. Para NIO, indique una tasa para
   toda la carga; si las fechas requieren tasas distintas, divida los archivos.
2. Deje Empresa vacía para detectar cada convenio por nombre, código, alias o
   cartera. Una empresa predeterminada solo completa filas sin empresa identificada;
   no reemplaza una empresa distinta informada en el movimiento o la cartera.
3. Pulse **Analizar archivo** y revise los grupos por empresa y fecha exacta
   de aplicación. Los movimientos contables que no son aplicaciones se excluyen;
   los duplicados del archivo o de cargas previas se muestran como omitidos.
   Las identidades ambiguas o fechas faltantes deben corregirse antes de continuar.
4. Pulse **Crear importaciones**. Se crea un `CN Accounting Import` por grupo,
   con sus filas, importes en US$, trazabilidad y un **CSV individual** en el campo
   Archivo, nombrado como el documento. El archivo masivo se conserva por separado
   como soporte en Historial. Se conserva
   el nombre `CONTA-CódigoEmpresa-Mes-Año-###`. No modifica cargas anteriores.
5. Abra los documentos y use **Conciliar esta empresa** cuando corresponda.
   La carga masiva no crea/cierra períodos ni concilia automáticamente.

El procesamiento requiere un worker de la cola `long`. Puede cerrar el modal
y volver a **Carga masiva** desde el mismo navegador/usuario para consultar el
resultado durante 24 horas. La creación es transaccional: un error revierte
todo el lote. Use el mismo botón **3. Cargar movimientos contables** para recargar
el CSV individual de un documento sin
mezclarlo con los otros grupos. El CSV conserva las columnas e importes originales
(antes de convertir NIO a USD), incluyendo una columna `CN_FILA_ORIGEN` para rastrear
la fila del archivo masivo. Al reprocesar se conservan las identidades de las filas
que no cambiaron y se valida que todos los movimientos sean de la misma empresa y
fecha; no se permite reprocesar vínculos de períodos cerrados. Para nuevos grupos,
vuelva a usar **Carga masiva**. Límite por archivo: 20 MB y 20,000 movimientos reconocidos.

## Instalación

Requiere **Frappe Framework 15 o superior** y **Python 3.10 o superior**.
Desde un Bench que ya tenga un sitio creado:

```bash
bench get-app credinomina_reconciliation git@github.com:ernestoruiz89/conciliacion_credinomina.git
bench --site sitio.local install-app credinomina_reconciliation
bench --site sitio.local migrate
```

En un sitio recién creado, complete además el asistente inicial de Frappe
antes de entrar al escritorio. Puede configurar **Español (Nicaragua)**,
**America/Managua** y moneda base **NIO**: los campos de la app muestran
separadamente US$ y C$, y la conciliación se calcula en US$.

Al actualizar una instalación existente, `bench migrate` ejecuta un parche que
renombra cada **Empresa Credinómina** desde su código al valor de **Empresa**
(`employer_name`). El **Código** (`employer_code`) se conserva como campo único
y como alias para reconocer archivos anteriores; los vínculos entre documentos
se actualizan mediante el renombrado de Frappe. Haga una copia de seguridad
antes de migrar. Si existen nombres vacíos o duplicados, el parche se detiene
para que se corrijan sin fusionar empresas.
Otro parche crea el catálogo de clientes a partir de las cobranzas existentes y
enlaza las filas que se identifican sin conflicto; las identidades ambiguas
quedan para revisión. No cambia aplicaciones ni saldos del core.
Un parche adicional incorpora el enlace del reporte de antigüedad al Workspace
existente sin borrar los demás accesos que haya configurado el sitio.

La instalación crea los roles `Operador Credinomina` y
`Supervisor Credinomina`. Asigne el rol correspondiente y, si se desea,
configure el idioma del usuario como español. El Workspace
**Conciliación Credinómina** está en `/app/conciliacion-credinomina` y reúne
el tablero, la operación, las excepciones y los reportes. También está
disponible la página `/app/control-credinomina`.
Los accesos del workspace siguen seis etapas: preparación, cobranza y
deducciones, aplicaciones del core, depósitos, diferencias y seguimiento,
y control y reportes. Los cuatro accesos rápidos permiten cargar cartera,
crear período, cargar aplicaciones y registrar depósito. Cobranza y detalle
de deducción se gestionan dentro del mismo período; en histórico se omiten
esas dos cargas. Al migrar se reorganizan los accesos de la app conservando
los enlaces y bloques adicionales del sitio, sin modificar roles ni saldos.

## Primer uso

Las aplicaciones se cargan en `CN Accounting Import` (Importación de Movimientos
Contables). El identificador usa el código de empresa y el mes de la fecha
`event_date` más antigua de sus filas: `CONTA-5111-9-2026-001`. El consecutivo
es independiente por código de empresa y mes. Antes de completar empresa y
filas con fecha se utiliza un nombre provisional `CONTA-BORRADOR-...`.
La migración renombra el DocType anterior y sus documentos conservando filas,
adjuntos, permisos y referencias; no vuelve a conciliar los movimientos.

Los cortes de cartera importados se identifican como `CARTERA-mes-año`, por
ejemplo `CARTERA-9-2026`, a partir de `FECHA_REPORTE` del archivo. Los borradores
reciben un identificador provisional hasta importar el archivo. La migración
renombra los cortes existentes y actualiza sus referencias.

Los depósitos se nombran `DEP-mes-año-####` según su fecha real, por ejemplo
`DEP-9-2026-0001`, con consecutivo por mes y año. El identificador asignado
se conserva al editar posteriormente la fecha. Al migrar, un parche renombra
los depósitos anteriores y conserva sus vínculos, adjuntos y ajustes internos,
sin recalcular importes ni reabrir períodos. Si alguno no tiene fecha, complete
ese dato antes de volver a migrar. Realice una copia de seguridad antes de migrar.

1. **Corte de cartera (opcional, recomendado).** En **Cortes mensuales de
   cartera**, cree un registro, adjunte el reporte mensual del core y pulse
   **Importar / actualizar corte**. El sistema detecta `FECHA_REPORTE`, conserva
   las 100 columnas del reporte en campos individuales de cada fila de
   `CN Credit Portfolio Row`; así están disponibles para reportes de consulta y
   tableros personalizados. También conserva un respaldo JSON de los valores
   originales. Muestra créditos, clientes y empresas identificados o por revisar.
   `Corriente` y `Vencido` se consideran
   activos; `Saneado` se conserva como estado distinto y no se presume
   cancelado. Si `EMPRESA_DE_CONVENIO` no está vacía ni es `N/A`, la carga
   crea la empresa faltante con el mismo nombre como código y crea el cliente
   faltante usando su número SIAF, nombre, cédula y empresa. Reutiliza los
   registros y alias existentes; no reasigna clientes de otras empresas.
   Los datos incompletos o ambiguos quedan por revisar, sin inventar números
   de cliente. Se puede volver a importar el mismo archivo para completar
   clientes o empresas que antes no estaban identificados.
   Cada importación deja en la actividad una constancia compacta con archivo,
   usuario, fecha, cantidad de créditos y huellas SHA-256 anterior y nueva.
   Se conservan todas las filas sin duplicarlas en el historial de versiones;
   las ediciones normales mantienen su historial. Al finalizar, el formulario
   muestra el resultado de la carga o un mensaje explícito si falla.
2. **Cobranza.** Cree un período operativo para la empresa y el corte mensual
   o quincenal. En **Plantillas → Plantilla de cobranza** descargue el XLSX
   con las columnas esperadas; complételo, adjúntelo y pulse **1. Cargar cobranza**. Solo se
   cargan las cuotas y se crean o enlazan los clientes; todavía no se afirma
   que la empresa haya deducido ni pagado nada. Cada cliente tiene nombre,
   número, cédula y una tabla de nombres alternativos.
3. **Deducción de la empresa.** En ese mismo período, adjunte el archivo
   devuelto con `Deducido C$` y/o `Deducido US$` y pulse **2. Cargar deducción
   de empresa**. Se compara con la cobranza. El nombre es obligatorio; si
   faltan crédito, cédula y número de cliente, se admite un nombre o alias
   único. Se ignora el orden de nombres y apellidos y las tildes. Una falta
   de ortografía solo se acepta si se registró como alias; nombres compartidos
   o no reconocidos quedan pendientes, sin asignación automática.
   **Plantilla de detalle empresa** descarga esas columnas y, si la cobranza
   ya se cargó, conserva sus clientes y `Fila ID` para que la empresa complete
   los importes deducidos.
4. **Aplicación de pago.** Importe **Movimientos contables**. Al encontrar un
   número de crédito, la app toma el corte del mismo mes (aunque el archivo esté
   fechado al cierre); si ese mes no tiene corte, usa el corte importado más
   reciente de un mes anterior. También completa el nombre e identidad
   desde la cartera cuando faltan en el asiento y muestra si el crédito, el
   cliente o la empresa requieren revisión. Puede seleccionar un corte concreto
   en **Corte de cartera para validar** al reprocesar un archivo histórico. Sin
   corte aplicable, el movimiento se importa normalmente y queda indicado para
   revisión; la cartera de meses futuros nunca se usa por defecto.
   En la tabla de aplicaciones se ven
   nombre, número de cliente, crédito, monto aplicado en US$, asiento contable
   y recibo cuando la fuente los proporciona. La aplicación puede registrarse
   antes o después de que llegue la deducción de la empresa.
5. **Depósito y detalle integrador.** Registre una **Distribución de Depósito**
   por depósito, con empresa, fecha real, moneda, importe y justificación. El
   soporte es opcional al registrarlo. Puede dejarlo pendiente hasta que la
   empresa envíe el detalle días después; la app conserva la fecha real del
   depósito y registra por separado cuándo se importó el detalle. Después de
   guardarlo, un supervisor debe pulsar **Confirmar depósito y conciliar**:
   mientras siga en borrador no participa en la conciliación. Puede confirmarse
   antes de recibir el detalle por cliente.
   Aunque la referencia identifique una sola aplicación, el depósito no se
   asigna automáticamente por cliente sin detalle o distribución manual
   documentada.
   En **Plantillas → Plantilla de detalle del depósito** descargue el mismo
   formato con deducidos (precargado si eligió **Período del detalle**).
   Adjunte el archivo completado y pulse **Cargar detalle del depósito**. Un
   depósito puede cubrir 200 aplicaciones; varias
   depósitos pueden cubrir una aplicación. El detalle se compara en US$ y no
   se inventa un reparto cuando hay nombres ambiguos o el total supera el
   depósito. Un saldo restante queda sin distribuir o como saldo a favor
   documentado. Un detalle solo en C$ requiere tasa y fuente documentadas;
   escriba la fuente y fecha de la tasa en **Justificación**, aunque adjunte el
   **Soporte del depósito**. El adjunto por sí solo no acredita la tasa usada.

En **Distribución de Depósito → Detalle por cliente**, seleccionar **Período del
detalle** muestra **Aplicado US$ del período**. **Usar aplicaciones como detalle**
previsualiza únicamente los importes aplicados pendientes, descontando lo cubierto
por otros depósitos confirmados y los ajustes vigentes por faltantes de centavos.
Todas las filas están seleccionadas inicialmente. Use las casillas de cada
movimiento o **Todos** para importar solo los que correspondan a este depósito;
el contador, el total seleccionado y la advertencia de diferencia se actualizan
al cambiar la selección. Se copia el importe pendiente completo de cada fila
marcada; las demás quedan disponibles para otros depósitos.
Las asignaciones del depósito actual se conservan para poder completar su detalle.
Funciona en períodos históricos y operativos; no copia la cobranza ni supone que
la empresa confirmó las deducciones. Si el total no coincide con el depósito, se
advierte la diferencia sin repartir ni reducir los importes automáticamente.
La acción genera un archivo privado, registra su origen y pide confirmación antes
de reemplazar filas existentes (conserva archivos y destinos, pero retira los
vínculos al detalle anterior). Después revise el detalle y use **Conciliar**;
generarlo no confirma ni concilia el depósito.

**Antigüedad de Saldos por Empresa**, disponible en **Control y consultas** del
workspace, agrupa el reporte de antigüedad sin mostrar clientes, créditos ni
períodos individuales. Conserva los mismos filtros, permisos, rangos y resumen;
suma los saldos actuales de cada empresa en US$, sin recalcular su vencimiento.
Identifica las aplicaciones sin conversión para no presentar sus saldos como cero.

La página **Control de Credinómina** abre con **Qué falta hacer**: evidencia de
empresa, aplicaciones sin período, detalles de depósito por revisar y saldos
sin clasificar. Los importes del tablero son un resumen; abra cada pendiente
antes de interpretar una celda como conciliada. Una aplicación vinculada a una
cuota antes de recibir el detalle de la empresa queda **provisional**: no prueba
que hubo descuento salarial.

En **Empresas por mes de conciliación**, **Resumen** está seleccionado por defecto:
cada celda reúne todos los períodos de la empresa en ese mes (quincenas, fechas
exactas y rangos históricos). Pulse la celda para ver los períodos como tarjetas
con su ciclo, estado e importes; al seleccionar una se abre el mismo modal de
detalle que en la vista desglosada. Desmarque **Resumen** para mostrar el desglose dentro del grid.
Los pendientes no se compensan con excedentes de otros períodos; un mes con
períodos pendientes no se muestra conciliado. La opción cambia solo la vista,
no los registros ni el Excel exportado.

Al final del mes, si aún falta evidencia o dinero, use **Registrar corte de
control** en el período. Guarda fecha, responsable, saldos y siguiente gestión
sin bloquear archivos que lleguen después. **Cerrar período** es distinto:
requiere las cuotas aplicadas y remitidas, sin saldos del empleado ni
excepciones abiertas; después solo un supervisor puede reabrirlo con motivo.

Para el **histórico de abril de 2025 a agosto de 2026** se omiten los pasos
de cobranza y deducción: se asignan las aplicaciones a períodos históricos y
se concilian contra los depósitos. La fecha del depósito puede estar en el mes
siguiente a la aplicación. Las excepciones abiertas bloquean el cierre también
en histórico; revise los saldos antes de cerrar un período. El tablero y los
reportes muestran lo pendiente.

Los períodos históricos y operativos comparten los estados **Borrador** (gris),
**Pendiente** (naranja), **Parcial** (naranja), **Con excedente** (rojo),
**Conciliado** (verde) y **Cerrado** (morado). La modalidad se identifica en su
propio campo. Cargar cobranza o confirmar la deducción no marca el período como
conciliado: deben completarse las validaciones de su modalidad y los pagos.
Un excedente sin clasificar relacionado con las aplicaciones tiene prioridad
sobre el estado conciliado. La migración traduce los estados existentes y el
estado previo al cierre, sin cambiar importes ni reabrir períodos.

En **Control de Credinómina**, el botón **Exportar Excel** descarga el año y,
si se seleccionó, la empresa filtrada. El libro separa el resumen de períodos,
el detalle de clientes y aplicaciones, los cruces con depósitos y las partidas
pendientes. Conserve ese archivo como evidencia del corte exportado; para ver
el estado actualizado vuelva a descargarlo. En histórico, cobranza y deducción
no se infieren: se muestra la aplicación frente al depósito.
El Excel usa los mismos nombres de estados y columnas para ambas modalidades;
**Modalidad** es el identificador. El resumen no separa totales históricos y
operativos. **Aplicado pendiente de depósito USD** suma los saldos pendientes
por partida de ambas modalidades, sin compensar excedentes de otros clientes
ni usar pagos de partidas complementarias para cubrir créditos. Se conserva
por separado **Deducido sin depósito asignado USD**, porque no mide lo mismo.
**N/D** indica un dato no disponible o no aplicable, no un cero; los totales
de cobranza, deducción y CxC a empleados suman únicamente los datos disponibles.

Las **excepciones** permiten registrar una causa clasificada, responsable,
próxima gestión, fecha compromiso, referencia y soporte. Al pasar a **En
revisión** se exige responsable, gestión y fecha; al resolver se exige causa
confirmada y resolución. El historial de gestiones conserva quién registró
cada acción y cuándo; las entradas ya guardadas no se editan ni eliminan.
El formulario separa **Caso relacionado**, **Seguimiento y resolución** e
**Historial de gestiones**. En una excepción manual, **Seleccionar caso
relacionado** busca cobranzas o aplicaciones por empresa y, opcionalmente,
período, nombre, número de cliente o crédito (también asiento, recibo y referencia
en aplicaciones). El selector completa los vínculos y la identidad del cliente,
sin sustituir el importe reclamado ni guardar automáticamente. Una aplicación
distribuida entre varios períodos aparece por cada período; seleccione el que
corresponde al caso. Los períodos cerrados y documentos sin permiso de lectura
no están disponibles. Las excepciones automáticas conservan su vínculo de origen.

El estado de cuenta de esta app explica los **movimientos en tránsito**;
acompañe el estado oficial del core cuando el cliente necesite el saldo
contractual de capital, intereses y préstamo.

El reporte **Antigüedad de Saldos** separa por empresa y cliente las cuotas
no deducidas, las deducciones sin depósito asignado y las filas sin detalle de
empresa. Distribuye cada importe en bandas de 1–30, 31–60, 61–90 y más de
90 días desde su fecha de referencia. Es un control operativo de saldos
actuales, no un cálculo de provisión ni una reconstrucción histórica; para
provisionar hay que cotejar el saldo y la mora del crédito en el core y aplicar
la política vigente de la IMF. Un depósito recibido sin detalle puede estar
cubriendo deducciones aún no asignadas por cliente: no sume ambos importes ni
interprete la deducción sin depósito asignado como CxC confirmada.

El tablero muestra **CxC a empleados (no deducido)** por año, período y cliente:
es la cuota enviada menos lo efectivamente deducido, únicamente cuando se
recibió el detalle de la empresa. Una cuota sin detalle permanece en **Detalle
pendiente**, no se presume deuda del empleado. La cifra es un control operativo
en US$ y debe cotejarse con el saldo oficial del crédito en el core. En
períodos históricos no se infiere CxC a empleados ni a empresas.

## Desarrollo y documentación

La evidencia de la simulación y los comandos de auditoría están en
[Pruebas en WSL](docs/pruebas_wsl.md).

Las reglas de importación y conciliación tienen pruebas unitarias que pueden
ejecutarse sin un sitio Frappe:

```bash
python -m unittest discover -s tests -v
```

La ejecución de esas pruebas **no sustituye** la instalación y validación
funcional en un sitio Frappe. Los archivos contienen cédulas e información
salarial: mantenga los adjuntos privados y restrinja el acceso a los roles
autorizados.

Para instrucciones detalladas, consulte la [guía de instalación y uso](docs/instalacion_y_uso.md)
y el [procedimiento operativo](docs/procedimiento_operativo.md).
