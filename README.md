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
  El detalle muestra **Equivalente US$**, **Vinculado US$** e **Importe pendiente US$**.
  El pendiente es el importe de la fila menos sus destinos manuales vinculados
  (o los destinos automáticos identificados), sin contarlos dos veces. Un valor
  negativo indica exceso de vinculación; cero no sustituye la validación del
  estado. Los destinos sin vínculo a una fila no se descuentan de ella.
  Una complementaria **sin período** puede cubrir una fila cuando se vincula
  manualmente y coinciden empresa, cliente y crédito. Si tiene un período,
  este debe estar entre los seleccionados; no se ignoran períodos incompatibles.
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
- Para un ajuste contable global, marque **Partida genérica de distribución manual**
  en la complementaria. Registre el importe total una sola vez, deje cliente y
  crédito vacíos y explique su origen. La empresa indicada está incluida; agregue
  otras en **Otras empresas autorizadas para distribuir** solo si el asiento
  realmente las afecta. Esta autorización no cambia qué empresas puede pagar una pagadora.
  Después de confirmar, agregue la partida a **Destinos** de cada depósito.
  En **Vincular detalle y destinos**, indique el **Importe a vincular US$** para
  cada cliente: si vincula US$200 de un destino de US$500, quedan US$300 como
  otro destino sin vincular, conservando la misma partida y el total asignado.
  Puede repetir para otros clientes o usar el saldo disponible en otros depósitos.
  Para una distribución sin fila indique la empresa de destino en el destino genérico.
  Las asignaciones de todas las empresas y depósitos consumen **un único saldo**:
  nunca US$500 por cada empresa. No se hace un reparto automático ni se modifica
  el importe aplicado en el core. El detalle del depósito conserva la empresa,
  cliente y crédito de cada porción; una fila sigue en revisión si sus destinos
  no suman su importe o si alguna asignación supera el saldo disponible.
  No marque como genéricas las partidas ya identificadas a un cliente ni los
  ajustes que reducen aplicaciones: para estos últimos use **Vincular a aplicación**.
- **Partidas complementarias** también permiten documentar un **Saldo a favor
  de la empresa**, desde el mismo modal del depósito. Este concepto se vincula
  directamente al depósito confirmado, sin agregarse a los destinos ni al
  detalle de clientes: no es pago de crédito ni ingreso administrativo. El
  excedente sin explicar sigue sin conciliar.
- **Saldo a favor del cliente**, en Partidas Complementarias, identifica el
  excedente de una persona sin incrementar lo aplicado al crédito. Seleccione
  cliente, depósito confirmado, importe positivo, motivo, responsable y fecha
  compromiso. Si la partida ya viene de contabilidad, reclasifique esa misma
  partida: su evidencia contable original se conserva y no se cuenta dos veces.
  Vincule una fila del detalle **solo si su importe incluye el exceso**: por
  ejemplo, detalle US$110 = aplicación US$100 + saldo US$10. Si el detalle
  muestra únicamente los US$100 aplicados, deje el vínculo de fila vacío.
  Cuando las diferencias quedan explicadas, el depósito muestra **Conciliado
  con saldo a favor del cliente**; el importe se reserva y no puede asignarse
  también a otros destinos. Esto no significa que se haya devuelto.
  Use **Registrar gestión del saldo** para documentar devoluciones o aplicaciones
  futuras realizadas fuera de la herramienta, con fecha, referencia y soporte
  adjunto. Admite gestiones parciales y conserva un historial de importes,
  usuarios y comprobantes. La lista **Saldos de clientes pendientes** permite
  darle seguimiento. Puede continuar la gestión después de cerrar el período,
  sin alterar su conciliación original. No genera pagos ni asientos en el core.
  La aplicación futura es una gestión externa documentada: no vincula ni
  concilia automáticamente otro movimiento contable y no reutiliza el depósito
  original como un nuevo pago. La verificación de asientos importados se
  mantiene en el flujo de excepciones contables.
- En el depósito, la pestaña **Destinos → Distribución completa del depósito**
  reúne pagos a créditos, partidas complementarias y saldos a favor del cliente
  o de la empresa, con registro relacionado, importe y estado de gestión.
  Puede filtrar por concepto o buscar cliente, empresa, crédito y registro.
  Los totales incluyen todos los registros, aunque se aplique un filtro.
  Es una vista de solo lectura: no agrega saldos a favor a `targets`, no genera
  pagos y no duplica importes. Las asignaciones manuales de abajo se reflejan
  en ella solamente después de usar **Conciliar**. **Actualizar distribución**
  vuelve a consultar los datos guardados sin ejecutar una conciliación.
- Al abrir una fila de **Detalle por cliente**, **Crear saldo a favor del
  cliente** abre el mismo modal con cliente, crédito y fila vinculados, e
  importe pendiente sugerido en US$. Está disponible en depósitos confirmados
  con pendiente positivo y permisos para crear y confirmar partidas. Revise el
  importe: una aplicación sin identificar no es automáticamente un saldo a
  favor. Complete justificación, responsable y fecha compromiso; después
  confirme la partida. Se reflejará en la distribución completa del depósito.
  Los registros existentes no se reclasifican automáticamente; las partidas
  con gestiones registradas no pueden cancelarse ni fusionarse.
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
Si la IMF necesita contabilizar uno de estos ajustes, use **Crear excepción**
en la partida: el seguimiento cambia a **Pendiente de registro** hasta verificar
el asiento importado. No se habilita la edición del ajuste automático.
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

## Alcance del botón Conciliar en un depósito

**Conciliar** recalcula solamente la empresa pagadora y las empresas vinculadas
por pagos compartidos. Incluye todos sus meses, porque un depósito puede cubrir
aplicaciones anteriores, y conserva las validaciones de períodos cerrados.
No ejecuta la conciliación global de todas las empresas.

Antes de procesar, se revisan los vínculos entre empresas mediante lecturas por
lotes; solo se cargan como documentos completos las importaciones del grupo.
Las importaciones y los períodos sin cambios no se vuelven a guardar. El formulario
muestra avance por etapas (requiere conexión de tiempo real) y al terminar informa
el estado del depósito, las empresas procesadas y los movimientos conciliados,
pendientes e ignorados. Los contadores corresponden al grupo de empresas, no solo
a las filas del depósito. Si falla la conexión de tiempo real, la respuesta final
sigue disponible. Confirmar y conciliar siguen siendo acciones separadas.

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
2. Deje Empresa vacía para archivos mixtos. Primero se busca el crédito en el
   **Corte de cartera** seleccionado (o el corte aplicable a la fecha si no selecciona
   uno) y se toma su empresa. Si no se obtiene empresa de cartera, se busca el
   nombre de la empresa, su código y finalmente sus alias registrados. Un nombre
   libre sin alias no bloquea una empresa ya identificada en cartera. Una empresa
   predeterminada solo completa filas sin datos de empresa; no reemplaza otra
   empresa identificada. Créditos duplicados e identidades contradictorias
   continúan requiriendo revisión. Si no se encuentra empresa por cartera, nombre
   ni alias, la carga usa **NO IDENTIFICADA** y la crea al confirmar si no existe,
   con el mismo nombre como código. La vista previa muestra cuántos movimientos
   quedarán pendientes de identificar y no crea empresas. El texto original se
   conserva; esta empresa provisional no confirma la pertenencia del cliente.
   Cada aplicación en **NO IDENTIFICADA** crea una importación independiente,
   incluso si varias tienen la misma fecha, para revisar y corregir su empresa
   caso por caso. Las empresas identificadas siguen agrupadas por empresa y fecha.
   La vista previa separa **Empresas identificadas** de **Casos no identificados**,
   con cantidades y total de aplicaciones por grupo. Para los casos no identificados
   muestra fila, fecha, cliente, crédito, empresa original y asiento; las partidas
   complementarias y depósitos también se muestran en su sección correspondiente.
   Pase el mouse por **NO IDENTIFICADA** para ver la descripción completa del asiento
   y use **Cambiar empresa** para asignar una empresa registrada a esa fila. Pulse
   **Nuevo análisis → Analizar archivo**: se conservan las selecciones del mismo
   archivo y se reagrupan por empresa y fecha antes de permitir la importación.
   **Quitar selecciones de empresa** permite volver a la identificación automática.
   Las decisiones no reemplazan identidades contradictorias de cartera ni se
   trasladan a otro archivo. El CSV individual conserva la decisión en
   `CN_EMPRESA_ASIGNADA`, sin reemplazar la empresa ni descripción originales.
   Al terminar, identificados y no identificados también aparecen en tablas separadas.
3. Pulse **Analizar archivo** y revise los grupos por empresa y fecha exacta
   de aplicación y las partidas complementarias que quedarán en revisión;
   las líneas similares del archivo o de cargas previas se advierten como posibles
   duplicados, pero **se importan todas** y participan en los totales y conciliación.
   Solo se evita recrear una misma fila del mismo archivo ya registrado; para
   actualizarla, reprocese su documento existente.
   Las identidades ambiguas o fechas faltantes deben corregirse antes de continuar.
4. Pulse **Crear importaciones**. Se crea un `CN Accounting Import` por grupo,
   con sus filas, importes en US$, trazabilidad y un **CSV individual** en el campo
   Archivo, nombrado como el documento. El archivo masivo se conserva por separado
   como soporte en Historial. Se conserva
   el nombre `CONTA-NombreCorto-Mes-Año-###` (usa el código si no hay nombre corto). No modifica cargas anteriores.
5. Abra los documentos y use **Conciliar esta empresa** cuando corresponda.
   La carga masiva no crea/cierra períodos ni concilia automáticamente.

El procesamiento requiere un worker de la cola `long`. Puede cerrar el modal
y volver a **Carga masiva** para consultar el avance. El plan, las empresas
seleccionadas y los resultados se guardan en la base de datos, sin caducar a las
24 horas. Desde otro navegador, con el mismo usuario, pulse **Recuperar última
carga guardada**. Las selecciones previas al nuevo análisis también se guardan
como borrador; es necesario analizarlas antes de confirmar.

La creación se divide en bloques de hasta **25 documentos o 1,000 filas**,
ejecutados como trabajos independientes. Un grupo empresa/fecha no se divide:
si supera 1,000 filas, ocupa un bloque propio. Cada bloque guarda sus documentos,
CSV y avance en la misma transacción. Si falla, solo se revierte el bloque en
curso; los anteriores permanecen guardados. Pulse **Reanudar carga** una vez
resuelta la causa, sin volver a seleccionar empresas ni recrear los bloques
completados. Si los datos cambiaron antes de confirmar el plan, use **Nuevo
análisis**: conserva las selecciones ligadas al mismo archivo y excluye sus filas
ya importadas. No se permite reanudar mientras el trabajo siga activo/en cola.

El original se comparte mediante referencias de archivo, sin cargar/copiar su
contenido por cada documento. Los CSV individuales se preparan una vez y se
guardan con cada bloque. El historial técnico (`CN Accounting Batch` y
`CN Accounting Batch Block`) no añade pasos al flujo ni permite edición manual.
La ventana muestra hasta 100 grupos de la vista previa y 200 documentos finales;
los contadores incluyen todos y los documentos completos están en sus listas.

Al desplegar esta actualización, ejecute `bench --site credinomina migrate` y
reinicie los procesos web/workers con `bench restart` antes de reanudar cargas.
Los nuevos trabajos usan bloqueos transaccionales de base de datos, liberados
al morir la conexión; no es necesario borrar bloqueos de Redis a mano. No deben
convivir workers con la implementación antigua y la nueva durante una carga.
Las cargas antiguas no tienen bloques persistidos: vuelva a analizarlas con las
selecciones conservadas en su navegador/vista previa mientras estén disponibles.
No se pueden recuperar documentos que la transacción antigua no llegó a guardar.

Use el mismo botón **3. Cargar movimientos contables** para recargar
el CSV individual de un documento sin
mezclarlo con los otros grupos. El CSV conserva las columnas e importes originales
(antes de convertir NIO a USD), incluyendo una columna `CN_FILA_ORIGEN` para rastrear
la fila del archivo masivo. Al reprocesar se conservan las identidades de las filas
que no cambiaron y se valida que todos los movimientos sean de la misma empresa y
fecha; no se permite reprocesar vínculos de períodos cerrados. Para nuevos grupos,
vuelva a usar **Carga masiva**. Límite por archivo: 20 MB y 100,000 movimientos reconocidos.

### Clasificación contable y movimientos pendientes de identificar

La importación individual y masiva clasifica por **TMOV / TDOC**, no solo por el
texto «NOTA AL PRÉSTAMO». La columna `Clasificacion` del Excel se conserva como
evidencia, pero no sustituye las reglas confirmadas:

| TMOV / TDOC | Clasificación | Tratamiento inicial |
| --- | --- | --- |
| 12 / 05, 12 / 19, 12 / 06 | Aplicación de pago | Aplicación si tiene débito positivo, sin crédito ni indicación de reversión. |
| 12 / 16 | ND de Aplicación de pago | Partida complementaria en borrador, para revisión. |
| 01 / 01, 12 / 01, 05 / 01 | Movimiento interno | Partida complementaria en borrador, para revisión. |
| Otros códigos o códigos ausentes | Por revisar | No se consideran automáticamente pagos. |

Los créditos, reversiones y movimientos no identificados conservan empresa y
crédito solo cuando pueden determinarse. No se inventa una empresa ni una referencia
de depósito a partir de `NO_REF`. La evidencia incluye cuenta, asiento, descripción,
fecha, débito/crédito, moneda, tasa, archivo y fila. Cada línea física tiene identidad
propia, aunque sus valores coincidan con otra. Al reprocesar el mismo origen se
reutilizan sus partidas y depósitos; las coincidencias nuevas se conservan en borrador
para revisión. Esto no confirma efectivo automáticamente.

En **Partidas complementarias → Movimientos por revisar**:

1. Identifique la empresa cuando corresponda y revise el movimiento.
2. Si no interviene en conciliaciones, seleccione **No conciliatoria**, documente
   el motivo y guarde. Permanece en borrador, sin efecto financiero.
3. Para una NC/ND que compensa una aplicación, use **Vincular a aplicación**,
   seleccione la importación y su fila, y revise **Importe del ajuste US$**.
   El tratamiento será **Ajuste de aplicación**. Es un importe positivo que reduce
   la aplicación, sin superar el equivalente US$ de la partida ni el aplicado neto.
   Guarde las observaciones y use **Confirmar ajuste** (requiere permiso de confirmar).
   Vincular o guardar el borrador no afecta saldos; confirmar recalcula la empresa.
4. Si realmente corresponde a un depósito, seleccione **Partida de depósito**,
   complete empresa, referencia y concepto, ajuste el importe/signo provisional y
   marque **Importe y signo revisados**. Un supervisor podrá confirmarla para usarla
   en la distribución habitual. Positivo: exceso de depósito; negativo: faltante.

Al cambiar un **borrador** a **Partida de depósito**, se retiran automáticamente
`related_import`, `related_application` y los datos del ajuste provisional.
Si el concepto era **Ajuste de aplicación** o **Compensación entre partidas**, pasa
a **Ajuste de conciliación**; revise el concepto antes de confirmar. Guarde para
registrar el cambio. Se conservan archivo, fila, asiento y evidencia contable original,
y no se modifica la aplicación. No se permite esta conversión en ajustes confirmados
ni en partidas con compensaciones registradas; no se reclasifican masivamente los existentes.

No se reclasifican automáticamente los registros existentes. Al recargar, se impide
reclasificar o quitar aplicaciones que ya tengan vínculos de conciliación. Ejecute
`bench --site <sitio> migrate` y reinicie los procesos después de desplegar estos cambios.

La aplicación conserva **Monto original**, **Ajustes confirmados US$**, **Aplicado
neto US$** y el resultado «Aplicación ajustada parcialmente» o «Aplicación compensada
totalmente». Los períodos, la antigüedad de saldos, los selectores de aplicaciones y
el informe de control utilizan el neto. El ajuste no es efectivo recibido y no
puede asignarse además como destino de un depósito. No modifica el core externo.

Se permite cubrir una aplicación con **depósito + ajuste**: por ejemplo,
US$137.33 originales, US$111.32 depositados y US$26.01 ajustados dejan cero
pendiente. El selector muestra **Depósitos / reservas US$** y **Disponible para
ajuste US$**. Los destinos manuales de depósitos en borrador también reservan
capacidad y no se cuentan dos veces si ya están aplicados. En cobranza compartida
por varias aplicaciones solo se permite reducir el saldo descubierto del conjunto.
Confirmar no puede consumir efectivo asignado o reservado, y se comprueba que
el recálculo conserve las distribuciones existentes. El resultado de la aplicación
se muestra como **Conciliada: depósito + ajuste**, sin llamar depósito al ajuste.
Los períodos cerrados siguen bloqueados. Cancelar la partida revierte
su reducción y recalcula los saldos conservando los depósitos. En modalidad
operativa no cambia lo cobrado ni lo deducido por la empresa: esas diferencias
deben resolverse por su propio proceso.

Cancelar una partida complementaria recalcula únicamente sus depósitos y períodos
vinculados, incluyendo destinos manuales, distribuciones automáticas y partidas
genéricas compartidas entre empresas. Se carga solo la contabilidad relacionada;
los demás depósitos se conservan como evidencia de saldo, sin redistribuirlos.
En un ajuste de aplicación se restaura el neto y se conservan íntegramente las
distribuciones de efectivo. Si un período afectado está cerrado, hay que reabrirlo
primero. Una partida sin vínculos no dispara una conciliación global.

El parche inicializa los importes netos de registros existentes sin cambiar sus
montos originales. Las antiguas «Reversiones identificadas» siguen siendo solo
seguimiento: no se convierten automáticamente en ajustes confirmados.

### Compensar partidas complementarias entre sí

Si un movimiento contable de abril se revierte en agosto, abra una de las partidas
y use **Compensar con otra partida**. Seleccione la contrapartida, revise ambos
saldos, indique el importe US$, la fecha y el motivo, y confirme. Requiere permisos
de escritura y confirmación sobre ambas partidas. No necesita identificar cliente
ni empresa; si las dos empresas están identificadas, deben coincidir.

La confirmación registra un historial en ambas partidas y las clasifica como
**Compensación entre partidas**. Permite compensaciones parciales, totales y varias
contrapartidas, sin superar los saldos disponibles. Conserva los movimientos,
importes, fechas y evidencias originales; no representa un depósito, no reduce
aplicaciones y no genera asientos en el core. En movimientos importados se verifica
el sentido opuesto mediante débito/crédito original; en registros manuales use
importes de signo contrario y este concepto, sin referencia de depósito.

El formulario muestra **Compensado US$**, **Pendiente de compensar US$** y el estado
**Sin compensar / Compensada parcialmente / Compensada totalmente**. La lista incluye
**Compensaciones pendientes**. **Consultar saldo a fecha** usa únicamente las
compensaciones realizadas hasta el corte: antes de agosto el movimiento de abril
sigue pendiente, aunque actualmente esté compensado. Esta consulta corresponde a
la partida; no incorpora estas operaciones como pagos en la antigüedad de créditos.

Se bloquean fechas futuras o anteriores a los movimientos o a compensaciones ya
registradas, empresas/cuentas contables conocidas diferentes, períodos cerrados,
partidas destinadas a depósitos o ajustes de aplicaciones y reutilización de saldos.
Los movimientos ya confirmados como partidas de depósito no pueden reclasificarse
por esta vía. Una compensación confirmada y sus movimientos originales no pueden
editarse, eliminarse ni cancelarse; sí puede completarse el asiento pendiente.
No se compensan automáticamente registros existentes ni se modifican cortes previos.
Para instalar los nuevos campos y la tabla de historial ejecute `migrate` y reinicie.

### Control mensual de movimientos contables

En el workspace, **Control mensual de movimientos contables** permite cotejar
externamente la carga con la balanza. Filtre el mes, cuenta y moneda original;
deje Empresa vacía para incluir también movimientos sin convenio identificado.
El detalle muestra cliente, crédito, empresa, asiento, tipo, estado actual,
importación/partida y **descripción original completa**. Pulse la descripción para
abrirla en un modal legible; el texto del reporte no se sustituye por un resumen.

Incluye débitos, créditos y neto en **C$ originales** para archivos NIO y en
**US$** usando la tasa de importación de cada movimiento. Los archivos USD no
generan importes ficticios en C$. El resumen separa cuenta y moneda original.
Cada lado se convierte y redondea con Decimal antes de sumar; no se usa el saldo
neto de una partida como si fuera su débito o crédito original.

Las partidas complementarias importadas, aunque estén en revisión, compensadas
o sin empresa, forman parte del control. El movimiento representado a la vez en
una fila contable y una partida se cuenta una sola vez. Las posibles repeticiones
entre cargas se advierten, sin excluirlas de los totales;
las filas repetidas dentro de una misma importación también se conservan. Revise los
archivos si coincidencias de identidad corresponden realmente a asientos distintos.
Las partidas creadas manualmente no prueban una carga contable y no se incluyen.

No se carga ni se conecta la balanza externa. Los estados son actuales; no se
reconstruye el estado histórico de conciliación. El control solo incluye registros
guardados y visibles para el usuario: no puede enumerar filas rechazadas antes de
guardarse ni certificar automáticamente la integridad del mes. Se advierten tasas
o evidencias faltantes, sin tratarlas como importes cero.

La migración conserva en el nuevo campo original las descripciones ya almacenadas
y las tasas disponibles, sin reimportar archivos ni alterar montos. Los nuevos
archivos conservan además el texto íntegro de DESCRIPCION, incluidos saltos de línea.

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
Contables). El identificador usa el **Nombre corto** de la empresa, o su código
si está vacío, y el mes de la fecha `event_date` más antigua de sus filas:
`CONTA-INDENICSA-9-2026-001` o `CONTA-5111-9-2026-001`. El consecutivo
es independiente por prefijo de empresa y mes. Antes de completar empresa y
filas con fecha se utiliza un nombre provisional `CONTA-BORRADOR-...`.
Los períodos usan el mismo prefijo: `INDENICSA-9-2026-01`, con el mes y año
de cobranza. No se renombra en masa al completar el nombre corto: los períodos
existentes conservan su nombre salvo cambio de empresa o mes; las importaciones
ajustan su nombre al guardarse, como parte de su renombrado automático habitual.
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
   guardarlo, un supervisor debe pulsar **Confirmar depósito** y luego **Conciliar**:
   mientras siga en borrador no participa en la conciliación. Puede confirmarse
   antes de recibir el detalle por cliente.
   Agregar o importar el detalle después de confirmar conserva los importes y
   distribuciones de la última conciliación. Si cambia el detalle o los destinos,
   el resultado queda **Pendiente** hasta pulsar **Conciliar**; guardar no borra
   los saldos ni ejecuta una conciliación. Agregar solo observaciones no cambia
   el resultado registrado.
   Aunque la referencia identifique una sola aplicación, el depósito no se
   asigna automáticamente por cliente sin detalle o distribución manual
   documentada.
   En **Plantillas → Plantilla de detalle del depósito** descargue el mismo
   formato con deducidos (precargado si eligió **Períodos del detalle**).
   Adjunte el archivo completado y pulse **Cargar detalle del depósito**. Un
   depósito puede cubrir 200 aplicaciones; varias
   depósitos pueden cubrir una aplicación. El detalle se compara en US$ y no
   se inventa un reparto cuando hay nombres ambiguos o el total supera el
   depósito. Un saldo restante queda sin distribuir o como saldo a favor
   documentado. Un detalle solo en C$ requiere tasa C$/US$ para convertirlo;
   las observaciones y el **Soporte del depósito** son opcionales.

En **Distribución de Depósito → Detalle por cliente**, agregue una o varias filas
en **Períodos del detalle**. Cada fila muestra su empresa y aplicado US$; debajo
se muestra el total aplicado de los períodos seleccionados. La conciliación del
detalle busca solamente en esos períodos; sin selección busca en todos los
períodos autorizados de la pagadora. La fecha del depósito sigue siendo independiente.
Por ejemplo, dos aplicaciones del mismo cliente y crédito por US$50.25 y US$60.26
se cubren automáticamente con una fila de detalle por US$110.51 cuando esa es
la suma exacta de todos los destinos pendientes identificados. Si hay otras
aplicaciones posibles, importes distintos o identidad ambigua, se pide revisión
manual: no se adivina un subconjunto ni se paga dos veces una aplicación.
El botón **Conciliar** procesa solo este depósito; no redistribuye los demás.
En **Seleccionar partidas pendientes**, el check **Usar períodos del detalle**
está marcado por defecto cuando esa tabla tiene períodos y limita los resultados
a todos ellos. Puede combinarlo con los filtros de cliente, empresa, tipo y un
período específico. Desmárquelo para buscar en otros períodos o ver partidas
complementarias sin período. Si la tabla está vacía, el check queda deshabilitado.

**Usar aplicaciones como detalle**
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

La página **Control de Credinómina** abre en la pestaña **Calendario**, con la
matriz de empresas por mes. **Trabajo de conciliación** reúne **Qué falta hacer**:
evidencia de empresa, aplicaciones sin período, detalles de depósito por revisar
y saldos sin clasificar. Su contador permite ver las gestiones pendientes desde
el calendario. La pestaña elegida se conserva al actualizar o cambiar los filtros;
alternar entre pestañas conserva las filas cargadas y la posición del calendario.
Los filtros de año y empresa, las cifras de control y la exportación son comunes.
El calendario tiene un botón **Pantalla completa**. Conserva el modo Resumen y
permite abrir los modales del mes, depósito y período por encima del calendario.
Se sale con **Salir de pantalla completa** o Escape; con un modal abierto, Escape
se deja al modal. Si el navegador no admite pantalla completa, se amplía dentro
de la ventana. Al navegar a otro documento se restaura automáticamente la página.
**Ver cifras de control** carga sus indicadores solo al abrirse: aplicado neto,
depósitos recibidos completos, aplicado pendiente (con su parte vencida), dinero
sin asignar, saldos a favor por gestionar y excepciones vencidas. El aplicado y
su pendiente usan el cálculo por aplicación de Antigüedad de Saldos, incluyendo
las aplicaciones sin período y descontando ajustes confirmados, sin compensar
saldos de clientes distintos. Los importes sin conversión y las restricciones de
permisos se indican explícitamente, no se presentan como ceros completos.
El año corresponde al mes de cobranza para aplicaciones vinculadas (fecha de
aplicación para las no vinculadas), fecha de depósito para efectivo, fecha de
partida para saldos a favor y fecha compromiso para excepciones. Se muestran
saldos actuales, no saldos reconstruidos a una fecha pasada. Los saldos de
clientes usan su pendiente de gestión; los de empresa muestran el documentado
vigente, pues aún no tienen seguimiento de devoluciones parciales. Las cifras
de cobranza, deducción y tolerancia se conservan en **Detalle del proceso**.
La exportación tiene sus propias hojas de seguimiento y un resumen por empresa y mes.
Sus bases de fecha se explican en la hoja **Guía**; no se debe restar el efectivo
recibido en un mes de las aplicaciones de ese mes si paga otras cobranzas.
Los importes del tablero son un resumen; abra cada pendiente
antes de interpretar una celda como conciliada. Una aplicación vinculada a una
cuota antes de recibir el detalle de la empresa queda **provisional**: no prueba
que hubo descuento salarial.

El tablero carga primero resúmenes, sin traer todas las filas por cliente.
Los detalles de cada período y la distribución por persona de cada depósito se
consultan al abrir su tarjeta y se reutilizan mientras no se pulse **Actualizar**
ni se cambien los filtros. Las listas largas muestran 100 registros inicialmente
y permiten **Mostrar más**; sus contadores y los KPI incluyen la población completa.
El Excel sigue incluyendo todos los detalles del filtro, no solo las filas visibles.

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
El cierre actualiza las conciliaciones de la empresa y su grupo financiero
relacionado (pagadoras y partidas genéricas compartidas), no las de todas las
empresas del sitio. Revisa datos recientes y depósitos vinculados aunque sean
de otro mes, conserva las validaciones de pendientes y muestra el progreso
durante la operación. Los índices de estas consultas se crean al instalar o
ejecutar `bench --site <sitio> migrate` tras actualizar la app.

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
El dinero sin asignar de un depósito no convierte el período en **Con excedente**.
Se muestra aparte en **Depósitos vinculados: saldo sin asignar US$** y debe
revisarse en el depósito; puede pertenecer a otros períodos o conceptos.
El estado del período refleja su conciliación y conserva **Parcial** cuando
solo está cubierto en parte. La migración corrige los antiguos estados
**Con excedente** de períodos abiertos y completa los importes informativos,
sin cambiar asignaciones, importes financieros ni reabrir períodos cerrados.

En **Control de Credinómina**, el botón **Exportar Excel** descarga el año y,
si se seleccionó, la empresa filtrada. Incluye estas hojas:

- **Resumen mensual:** una fila por empresa y mes, agrupando todos sus períodos.
  Separa aplicado, asignado a créditos y pendiente por mes de cobranza de los
  depósitos completos por fecha de recepción. Incluye meses con depósitos aunque
  no tengan períodos. Aplicaciones sin período se muestran aparte por fecha de aplicación.
- **Períodos:** resultados, importes, observaciones y cierre. Estar cerrado no
  significa estar conciliado. Cobranza y deducción no se inventan en histórico.
- **Detalle cliente** y **Cruces:** saldos por cliente/crédito y vínculos reales
  con los depósitos, con referencia de importación y fila para aplicaciones históricas.
- **Depósitos:** efectivo confirmado, moneda e importe original, tasa, cuenta
  bancaria, distribución y resultado. Cada depósito aparece una sola vez.
- **Distribución depósitos:** destinos por empresa, período, cliente o partida,
  incluyendo saldos a favor y efectivo sin clasificar. No repite un total de
  destino además de sus clientes. Permite revisar pagos de varios períodos o empresas.
- **Aplicaciones sin período:** ambas modalidades, incluso al filtrar una empresa.
- **Partidas y excepciones:** alertas detectadas y excepciones documentadas. Sus
  importes no son sumables: una excepción puede explicar una alerta ya mostrada.
  Las excepciones sin período se incluyen por año de creación.
- **Gestiones** y **Guía:** seguimiento y definiciones de fechas, importes y alcance.

Los totales filtrados usan `SUBTOTAL` en Excel. Los datos de conciliación son una
fotografía, no fórmulas que vuelvan a conciliar ni un cierre contable reconstruido.
Conserve ese archivo como evidencia del corte exportado; para ver
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

Desde una **Partida complementaria**, **Crear / Ver excepción** permite dar
seguimiento a **Registrar ajuste en el core**, con responsable y fecha compromiso.
La partida y la excepción tienen vínculos directos. Esta gestión es independiente
del cierre financiero: el período de origen es informativo y puede estar cerrado.
No crea un asiento en el core ni cambia lo distribuido por el depósito.

Una vez registrado el ajuste en el core, importe sus movimientos contables y,
en la excepción, use **Verificar asiento y resolver**. Indique el número de asiento,
seleccione su línea importada y documente la resolución. Se verifica empresa,
signo e importe neto en US$ (con conversión de NIO usando la tasa de la evidencia).
No basta con escribir un asiento o cambiar el estado a Resuelta. El movimiento
debe tener débitos/créditos originales y no estar usado por otra excepción ni
como otra partida conciliatoria. Un asiento genérico debe corresponder a una
empresa autorizada para distribuir la partida.

La verificación conserva fecha, usuario, línea y descripción de la evidencia,
y agrega una gestión al historial. El ajuste manual/interno no se agrega a los
totales del **Control mensual de movimientos contables**: allí se cuenta una
sola vez su registro real importado. Si este llegó como una nueva partida en
revisión, se marca **No conciliatoria** y se vincula a la excepción para evitar
volver a distribuir el mismo importe. La validación es por línea completa;
no suma arbitrariamente líneas de un asiento ni admite verificaciones parciales.
El reporte identifica su registro real como **Registro contable verificado**.
La evidencia verificada se conserva: no se puede borrar al reprocesar su carga,
cambiar de empresa ni volver a usar como partida conciliatoria. Las correcciones
posteriores del core se cargan como movimientos nuevos, sin reescribir el asiento original.

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

## Depósitos incluidos en movimientos contables

La combinación **TMOV 02 / TDOC 12**, con crédito positivo y sin débito ni
indicación de reversión, crea un **Distribución de Depósito** en borrador.
Funciona tanto en la importación individual como en **Carga masiva**; los
depósitos no se confirman ni concilian automáticamente. La fila contable
queda vinculada al depósito, sin aplicar efectivo una segunda vez.

El importe bancario se toma de **Dep. en Banco + Moneda**, o del importe y
moneda explícitos al inicio de la descripción. Si faltan, se usa el crédito
contable con una advertencia para revisar. La moneda seleccionada al importar
es la de los débitos/créditos del archivo, no necesariamente la de la cuenta
bancaria. Se conservan ambos importes; el **Control Mensual de Movimientos
Contables** suma la evidencia contable original en C$ y US$, no el total bancario.
La fecha explícita «EL DIA dd/mm/aaaa» identifica la recepción; de no existir,
se usa Fecha Aplica, conservada también como fecha contable original.

La cuenta se busca por banco, identificador y moneda explícitos. Solo se crea
si esos datos están identificados sin ambigüedad y el usuario tiene permiso
para crear cuentas. Números abreviados se reutilizan únicamente si identifican
una cuenta existente única. Si hay dudas o monedas contradictorias, se deja
vacía y se explica en **Origen contable**. Una empresa desconocida también
puede quedar pendiente en el borrador, pero debe completarse antes de confirmar.

Reprocesar conserva el vínculo por identidad contable, sin duplicar depósitos
ni modificar sus asignaciones. Si coincide con un depósito manual o una partida
complementaria anterior, la carga se detiene con un mensaje para revisar el
registro previo. No se convierte evidencia anterior automáticamente.

### Corrección de vínculos y filas originales del CSV

El Control Mensual solo incorpora los datos de un depósito cuando la fila es
un depósito y coincide su evidencia original: fecha contable, cuenta, moneda,
asiento, débitos/créditos, descripción, TMOV/TDOC y referencia. También valida
la huella del archivo y la fila original cuando están disponibles. Una clave
coincidente por sí sola no vincula un depósito a un cliente ni cambia la empresa
de una aplicación. Los depósitos sin cliente en el origen muestran esos campos
vacíos; sus débitos/créditos contables se muestran en su propia fila.

Los CSV individuales conservan **CN_FILA_ORIGEN**. Al recargarlos se utiliza
esa posición del archivo masivo, no su posición dentro del CSV. No quite ni
modifique la columna: valores vacíos, inválidos o repetidos detienen la carga.
Las líneas contables iguales en posiciones originales distintas se conservan
como movimientos independientes.
Si reemplaza o edita el CSV, se contrasta también con el archivo masivo original
antes de reutilizar identidades o crear clientes: no se puede cambiar la evidencia
contable ni atribuirla a otra fila. Las elecciones de empresa y cambios de formato
pueden conservarse sin alterar los datos contables originales.

Para instalaciones existentes, el parche `repair_accounting_csv_origins`,
ejecutado con `bench --site <sitio> migrate`, restaura las filas originales y
claves incorrectas de las importaciones masivas. Contrasta el CSV sin modificar
con el archivo masivo original, preserva los IDs de filas, los importes,
períodos cerrados, ajustes y distribuciones de depósitos, y registra un comentario
con los valores anteriores y nuevos. No reimporta ni ejecuta una conciliación
global. Repetir la reparación no duplica movimientos ni comentarios.

Haga un respaldo antes de actualizar, ejecute la migración y reinicie los procesos.
Puede revisar primero qué cambiaría con este diagnóstico de solo lectura:

```bash
bench --site <sitio> execute credinomina_reconciliation.accounting_origin_repair.repair_accounting_origins
```

La respuesta incluye importaciones revisadas, importaciones y filas afectadas,
y casos que necesitan revisión. Si falta un archivo o no puede acreditarse la
coincidencia, el parche no modifica esa importación y la registra en **Error Log**
como «Revisar origen de importaciones contables». Tras corregir esos soportes,
puede repetir la reparación con `--kwargs '{"dry_run": false}'` en una ventana de
mantenimiento. El modo predeterminado es diagnóstico, sin cambios; los modos de
reparación bloquean las conciliaciones y cargas concurrentes durante su transacción.

## Una empresa paga por otras empresas

En **Empresa de convenio**, abra la empresa pagadora y agregue las otras
empresas en **Empresas por las que puede pagar**. Por ejemplo, en INDENICSA
agregue CBC. La autorización es directa: no fusiona empresas ni autoriza
automáticamente el pago inverso o por otras empresas relacionadas.

Registre un solo depósito con **Empresa pagadora = INDENICSA**. En
**Seleccionar partidas pendientes** podrá filtrar y seleccionar destinos de
INDENICSA y CBC, combinando períodos e importes parciales. El detalle por cliente
también puede contener personas de ambas empresas; si el nombre es ambiguo,
indique **Empresa beneficiaria** en la fila o en esa columna opcional de la
plantilla. Después guarde, confirme el depósito y use **Conciliar**.

Un depósito de US$1,000 puede asignar US$700 a INDENICSA y US$300 a CBC.
Cada empresa conserva su propia deuda y antigüedad; el efectivo recibido se
cuenta una sola vez bajo la pagadora. El modal de distribución muestra la
empresa de cada destino. Un excedente sin asignación permanece en el depósito
de la pagadora y no se compensa automáticamente contra otras empresas.

Al conciliar una empresa con pagos compartidos se recalculan juntas las empresas
vinculadas, respetando los permisos de las importaciones. La relación no se
puede retirar mientras esté utilizada por un depósito confirmado. Los períodos
cerrados conservan sus bloqueos. Los registros existentes siguen funcionando
igual si no se configura ninguna autorización.

Para actualizar una instalación existente, ejecute `bench --site <sitio> migrate`
después de actualizar el código, compile los recursos y reinicie los procesos.
No se vinculan empresas existentes automáticamente.

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
