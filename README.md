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
su propio período y fecha límite de remesa. Si el core agrupa ambas quincenas
en una aplicación, la app la reparte solo cuando crédito, empresa, mes e
importes permiten un cruce único.

## Qué permite conciliar

- Pagos parciales, un depósito para varias cobranzas y varios depósitos para
  una cobranza. Cada **Distribución de Remesa** representa un depósito. Su
  detalle por cliente identifica automáticamente los destinos; la tabla de
  destinos manuales queda disponible para excepciones. Los repartos ambiguos
  no se adivinan.
- **Movimientos contables** como única fuente de aplicaciones. El depósito se registra directamente con
  referencia, fecha, empresa, moneda e importe; el detalle/soporte puede
  adjuntarse después, sin cambiar la fecha del depósito. No
  hace falta importar el Excel bancario mensual (que mezcla otros depósitos).
- Aplicaciones y saldos de crédito en **US$**. Una remesa en **C$** se convierte
  para la conciliación solo con una tasa y evidencia documentadas. Las
  diferencias cambiarias quedan para revisión de cada caso.
- **Partidas Complementarias** para importes depositados y contabilizados en
  otro asiento, sin registrarlos ficticiamente como pago del préstamo.
- **Excedentes de Depósito** separados como saldo a favor documentado de la
  empresa; el excedente sin explicar sigue sin conciliar.
- Excepciones detectadas antes del depósito, con traslado de comentarios al
  detalle de la remesa cuando el faltante corresponde al mismo caso.

La empresa puede configurar una **tolerancia automática en US$** entre 0 y
US$0.10. Es simétrica: con US$0.01, depósito de US$46.53 frente a aplicación
de US$46.52 produce un movimiento **+US$0.01**; en el caso inverso produce
**−US$0.01**. El signo significa *depósito menos aplicación*. Es un movimiento
interno y trazable, no un asiento contable ni un cambio en el core. Solo se
crea con un depósito y una aplicación inequívocos en US$; no resuelve
diferencias cambiarias ni repartos múltiples.

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
detalle del pago de la empresa se adjunta e importa **dentro de cada remesa**,
no como fuente bancaria separada. Si contiene ambos deducidos, US$ es el
importe de conciliación y C$ es informativo: no se suman. Si solo contiene C$,
indique la tasa documentada en la remesa para convertirlo a US$.
Cada cliente pertenece a una empresa de convenio. El número de empleado es
su identificador interno en esa empresa, distinto del número de cliente; puede
repetirse en otra empresa. Los nombres y alias se buscan solo dentro de la
empresa correspondiente.

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

## Primer uso

1. **Corte de cartera (opcional, recomendado).** En **Cortes mensuales de
   cartera**, cree un registro, adjunte el reporte mensual del core y pulse
   **Importar / actualizar corte**. El sistema detecta `FECHA_REPORTE`, conserva
   todas las columnas originales de cada fila y muestra créditos, clientes y
   empresas identificados o por revisar. `Corriente` y `Vencido` se consideran
   activos; `Saneado` se conserva como estado distinto y no se presume
   cancelado. La carga no crea clientes ni bloquea movimientos por una alerta.
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
5. **Depósito y detalle integrador.** Registre una **Distribución de Remesa**
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
   remesas pueden cubrir una aplicación. El detalle se compara en US$ y no
   se inventa un reparto cuando hay nombres ambiguos o el total supera el
   depósito. Un saldo restante queda sin distribuir o como saldo a favor
   documentado. Un detalle solo en C$ requiere tasa y fuente documentadas;
   escriba la fuente y fecha de la tasa en **Justificación**, aunque adjunte el
   **Soporte del depósito**. El adjunto por sí solo no acredita la tasa usada.

La página **Control de Credinómina** abre con **Qué falta hacer**: evidencia de
empresa, aplicaciones sin período, detalles de depósito por revisar y saldos
sin clasificar. Los importes del tablero son un resumen; abra cada pendiente
antes de interpretar una celda como conciliada. Una aplicación vinculada a una
cuota antes de recibir el detalle de la empresa queda **provisional**: no prueba
que hubo descuento salarial.

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

En **Control de Credinómina**, el botón **Exportar Excel** descarga el año y,
si se seleccionó, la empresa filtrada. El libro separa el resumen de períodos,
el detalle de clientes y aplicaciones, los cruces con depósitos y las partidas
pendientes. Conserve ese archivo como evidencia del corte exportado; para ver
el estado actualizado vuelva a descargarlo. En histórico, cobranza y deducción
no se infieren: se muestra la aplicación frente al depósito.

Las **excepciones** permiten registrar una causa clasificada, responsable,
próxima gestión, fecha compromiso, referencia y soporte. Al pasar a **En
revisión** se exige responsable, gestión y fecha; al resolver se exige causa
confirmada y resolución. El historial de gestiones conserva quién registró
cada acción y cuándo; las entradas ya guardadas no se editan ni eliminan.

El estado de cuenta de esta app explica los **movimientos en tránsito**;
acompañe el estado oficial del core cuando el cliente necesite el saldo
contractual de capital, intereses y préstamo.

El reporte **Antigüedad de Saldos** separa por empresa y cliente las cuotas
no deducidas, las deducciones sin remesa asignada y las filas sin detalle de
empresa. Distribuye cada importe en bandas de 1–30, 31–60, 61–90 y más de
90 días desde su fecha de referencia. Es un control operativo de saldos
actuales, no un cálculo de provisión ni una reconstrucción histórica; para
provisionar hay que cotejar el saldo y la mora del crédito en el core y aplicar
la política vigente de la IMF. Un depósito recibido sin detalle puede estar
cubriendo deducciones aún no asignadas por cliente: no sume ambos importes ni
interprete la deducción sin remesa asignada como CxC confirmada.

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
