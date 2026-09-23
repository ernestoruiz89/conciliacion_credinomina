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
- **Movimientos contables** como fuente principal de aplicaciones y
  **Transacciones** como respaldo. El depósito se registra directamente con
  referencia, fecha, empresa, moneda e importe; el detalle/soporte puede
  adjuntarse después, sin cambiar la fecha del depósito. No
  hace falta importar el Excel bancario mensual (que mezcla otros depósitos).
  Las importaciones bancarias anteriores permanecen disponibles como legado.
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
`Deducido US$`. Para aplicaciones se importan **Movimientos contables
(principal)** y, si hace falta, **Transacciones del core (fallback)**. El
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

1. **Cobranza.** Cree un período operativo para la empresa y el corte mensual
   o quincenal. Adjunte el archivo y pulse **1. Cargar cobranza**. Solo se
   cargan las cuotas y se crean o enlazan los clientes; todavía no se afirma
   que la empresa haya deducido ni pagado nada. Cada cliente tiene nombre,
   número, cédula y una tabla de nombres alternativos.
2. **Deducción de la empresa.** En ese mismo período, adjunte el archivo
   devuelto con `Deducido C$` y/o `Deducido US$` y pulse **2. Cargar deducción
   de empresa**. Se compara con la cobranza. El nombre es obligatorio; si
   faltan crédito, cédula y número de cliente, se admite un nombre o alias
   único. Se ignora el orden de nombres y apellidos y las tildes. Una falta
   de ortografía solo se acepta si se registró como alias; nombres compartidos
   o no reconocidos quedan pendientes, sin asignación automática.
3. **Aplicación de pago.** Importe **Movimientos contables** y, cuando haga
   falta, **Transacciones** como respaldo. En la tabla de aplicaciones se ven
   nombre, número de cliente, crédito, monto aplicado en US$, asiento contable
   y recibo cuando la fuente los proporciona. La aplicación puede registrarse
   antes o después de que llegue la deducción de la empresa.
4. **Depósito y detalle integrador.** Registre una **Distribución de Remesa**
   por depósito, con empresa, fecha real, moneda, importe y justificación. El
   soporte es opcional al registrarlo. Puede dejarlo pendiente hasta que la
   empresa envíe el detalle días después; la app conserva la fecha real del
   depósito y registra por separado cuándo se importó el detalle.
   Aunque la referencia identifique una sola aplicación, el depósito no se
   asigna automáticamente por cliente sin detalle o distribución manual
   documentada.
   Adjunte el mismo formato de cobranza con deducidos y pulse **4. Cargar
   detalle del depósito**. Un depósito puede cubrir 200 aplicaciones; varias
   remesas pueden cubrir una aplicación. El detalle se compara en US$ y no
   se inventa un reparto cuando hay nombres ambiguos o el total supera el
   depósito. Un saldo restante queda sin distribuir o como saldo a favor
   documentado. Un detalle solo en C$ requiere tasa y fuente documentadas;
   indique la fuente en **Justificación** o adjunte el **Soporte del depósito**.

Para el **histórico de abril de 2025 a agosto de 2026** se omiten los pasos
de cobranza y deducción: se asignan las aplicaciones a períodos históricos y
se concilian contra los depósitos. La fecha del depósito puede estar en el mes
siguiente a la aplicación. Revise excepciones y saldos antes de cerrar un
período; el tablero y los reportes muestran lo pendiente.

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
