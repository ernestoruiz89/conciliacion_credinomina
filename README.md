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
| **Histórico: abril 2025 – agosto 2026** | Aplicaciones del core contra depósitos contables y bancarios. | No se reconstruye la cobranza ni la deducción de planilla; una aplicación sin depósito no se presenta como deuda del trabajador o de la empresa. |
| **Operativo: desde septiembre 2026** | Cobranza, detalle de deducción y aplicación; después, aplicación contra depósito. | Una deducción no se convierte en pago aplicado al crédito hasta que el core lo confirme. |

La cobranza puede ser **mensual o quincenal** por empresa. Cada quincena tiene
su propio período y fecha límite de remesa. Si el core agrupa ambas quincenas
en una aplicación, la app la reparte solo cuando crédito, empresa, mes e
importes permiten un cruce único.

## Qué permite conciliar

- Pagos parciales, un depósito para varias cobranzas y varios depósitos para
  una cobranza. Los repartos ambiguos requieren una **Distribución de Remesa**
  confirmada; no se asignan por similitud del nombre del cliente.
- **Movimientos contables** como fuente principal de aplicaciones y
  **Transacciones** como respaldo. El **Detalle de depósitos** proporciona la
  evidencia bancaria que se empareja con el depósito contable.
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
contable y bancario que cubra **exactamente toda la cobranza** puede sustentar
un reconocimiento provisional y justificado. Se muestra como **deducción
inferida por depósito**, nunca como descuento individual confirmado por la
empresa, y puede revertirse o sustituirse cuando llegue el detalle real.

## Archivos de entrada

Se admiten archivos `.xlsx`, `.xls` y `.csv`. El archivo de cobranza que se
envía a la empresa contiene estas columnas:

`Nro. Cliente`, `Nombre y Apellidos del Cliente`, `Nro Cédula`,
`Nro. Crédito`, `Nro. cuota`, `Nro. de cuotas totales`,
`Monto de la cuota en US$`, `Monto de la cuota en C$`, `Comentarios`,
`Referencia de Aplicación` y `Comentario de Aplicación`.

La exportación agrega `Fila ID` para enlazar la respuesta sin depender del
nombre. La empresa devuelve el mismo archivo con `Deducido C$` y/o
`Deducido US$`. Las otras fuentes se importan por separado como
**Movimientos contables (principal)**, **Transacciones del core (fallback)**
y **Detalle de depósitos**.

## Instalación

Requiere **Frappe Framework 15 o superior** y **Python 3.10 o superior**.
Desde un Bench que ya tenga un sitio creado:

```bash
bench get-app credinomina_reconciliation git@github.com:ernestoruiz89/conciliacion_credinomina.git
bench --site sitio.local install-app credinomina_reconciliation
bench --site sitio.local migrate
```

La instalación crea los roles `Operador Credinomina` y
`Supervisor Credinomina`. Asigne el rol correspondiente y, si se desea,
configure el idioma del usuario como español. El Workspace
**Conciliación Credinómina** está en `/app/conciliacion-credinomina` y reúne
el tablero, la operación, las excepciones y los reportes. También está
disponible la página `/app/control-credinomina`.

## Primer uso

1. Cree las **Empresas Credinómina** y defina frecuencia de cobranza,
   plazo de remesa y, si procede, tolerancia en US$.
2. Cree los **Períodos de Conciliación**. Para el histórico, uno por empresa
   y mes; para la operación, uno mensual o dos quincenales según el convenio.
3. En períodos operativos, importe y exporte la cobranza; cuando llegue la
   respuesta de la empresa, cargue el detalle de deducción en el período de
   planilla original, aunque llegue al mes siguiente.
4. Importe por separado los movimientos contables, las transacciones de
   respaldo y el detalle bancario. En el histórico, asigne explícitamente el
   período a cada aplicación cuando un archivo mezcle empresas o meses.
5. Revise el **Control de Credinómina**. Documente distribuciones ambiguas,
   partidas administrativas y excedentes; revise las diferencias cambiarias
   y los movimientos de tolerancia antes de cerrar.
6. Consulte **Estado de Cuenta Operativo** y **Resumen de Conciliación** para
   explicar aplicaciones, depósitos, pendientes y excepciones.

El estado de cuenta de esta app explica los **movimientos en tránsito**;
acompañe el estado oficial del core cuando el cliente necesite el saldo
contractual de capital, intereses y préstamo.

## Desarrollo y documentación

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
