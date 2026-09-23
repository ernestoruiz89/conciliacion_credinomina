# Instalacion y uso

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

La instalacion crea los roles `Operador Credinomina` y `Supervisor Credinomina`.
Asigne uno de ellos a cada usuario y configure el idioma del usuario como Espanol.
El Workspace **Conciliación Credinómina** aparece en el escritorio para esos
roles y para `System Manager` después de instalar la app o ejecutar
`bench --site sitio.local migrate`. También puede abrirse en
`/app/conciliacion-credinomina`; contiene accesos al tablero, períodos,
importaciones, distribuciones, excepciones, movimientos internos y reportes.

## Orden de uso

### Carga histórica: abril 2025 a agosto 2026

1. Cree un **Período de Conciliación** por empresa y mes de cobranza que vaya a reconstruir; seleccione modalidad **Histórica**. En **Tipo de período histórico**, use **Mensual** para conservar el esquema anterior, **Fecha exacta** para un corte de aplicaciones del core (15, 30 o cualquier otro día), o **Rango de fechas** para agrupar varios días, ambos extremos incluidos. Puede tener varios cortes fechados en un mismo mes de cobranza, siempre que sus fechas no se solapen. Un período mensual puede coexistir con cortes fechados durante una transición; cada aplicación pertenece a un solo período explícito. La fecha de aplicación puede ser posterior al mes de cobranza. No adjunte cobranza ni detalle de deducción.
2. Importe **Movimientos contables** como principal y **Transacciones** como respaldo de las aplicaciones. Marque **Carga histórica de aplicaciones**. Si un archivo corresponde a una sola empresa, mes y corte, indique el período histórico predeterminado. Si mezcla cortes, asigne el período a cada fila de aplicación y use **Actualizar conciliaciones**. En la tabla por cliente se muestran nombre, número de cliente, crédito, importe, asiento y recibo cuando la fuente los contiene. Las filas sin período permanecen pendientes.
3. Registre una **Distribución de Remesa** por depósito de convenio, con fecha real, importe, moneda, referencia y justificación. El soporte puede adjuntarse después. No importe como fuente nueva la planilla bancaria mensual con depósitos de otras empresas. Si el detalle del depósito todavía no llegó, conserve la remesa pendiente; cuando llegue días después, adjunte el detalle por cliente e impórtelo en esa misma remesa. La app registra cuándo se importó. No cambie la fecha de aplicación ni la del depósito para forzar coincidencias.
4. Revise en **Control de Credinómina** las aplicaciones sin depósito. El detalle del depósito puede repartir una remesa entre muchas aplicaciones o varias remesas sobre una aplicación. Si faltan crédito, cédula y número de cliente en una fila, el nombre/alias debe identificar un único cliente. Los casos ambiguos se asignan manualmente con **Destinos del depósito** y soporte.
5. Documente partidas administrativas con **Partida Complementaria** y excedentes con **Excedente de Depósito**; nunca aumentan ficticiamente lo aplicado al crédito. Cierre el período histórico solo cuando todas sus aplicaciones queden cubiertas por depósitos.

El saldo histórico **aplicación sin depósito** no es una cuenta por cobrar a la empresa ni un faltante del trabajador: no se reconstruyó la primera conciliación. Conserve los archivos originales y soportes de distribuciones manuales. Una aplicación histórica de agosto de 2026 registrada en septiembre puede seguir vinculada al mes de cobranza agosto y al corte de aplicación de septiembre que corresponda. No cambie las fechas de un corte con aplicaciones asignadas: primero reasígnelas.

### Operación desde septiembre 2026

1. Configure la empresa y cree un período **Operativo** mensual o quincenal. Adjunte la cobranza y pulse **1. Cargar cobranza**. Se crean los clientes no registrados; la tabla guarda nombre, cédula, número de cliente, crédito y cuota. En este paso no hay conciliación ni deducción confirmada.
2. Cuando la empresa responda, adjunte en el mismo período el archivo con `Deducido C$` y/o `Deducido US$`, indique la fecha de esa evidencia y pulse **2. Cargar deducción de empresa**. El nombre del cliente es obligatorio. Si faltan identificadores, solo un nombre normalizado o alias único permite vincular la fila. Un error ortográfico requiere agregar un alias verificado en **Clientes y alias** y volver a importar. La `Fila ID` exportada también permite identificar con precisión una cuota.
3. Importe las aplicaciones del core con **3. Cargar aplicaciones del core** en **Importación de Fuente**. Use Movimientos contables como fuente principal y Transacciones como respaldo. El archivo puede llegar antes o después de la respuesta de la empresa. El core puede agrupar las dos quincenas en una aplicación.
4. Registre el depósito en **Distribución de Remesa** cuando llegue, aun el mes siguiente, con fecha, referencia, moneda, importe y justificación. Puede quedar sin soporte o detalle por cliente. Aunque haya una sola aplicación con la misma referencia, no se asigna automáticamente sin detalle o distribución manual documentada. Si el detalle integrador llega días después, adjúntelo al mismo depósito y pulse **4. Cargar detalle del depósito**. No se requiere que las cuatro fechas coincidan; cada nueva evidencia recalcula los vínculos disponibles y lo no identificado sigue pendiente. No cierre el período mientras haya aplicaciones, remesas o detalles pendientes.
5. Use **Partida Complementaria** para importes administrativos y **Excedente de Depósito** para saldos a favor documentados. Revise el tablero, el estado de cuenta y las excepciones antes de cerrar.

El reporte **Antigüedad de Saldos** muestra por empresa y cliente las cuotas
no deducidas, las deducciones sin remesa asignada y el detalle aún no recibido, en
bandas de días. La fecha elegida sirve para medir la antigüedad de los saldos
actuales; no reconstruye un saldo histórico. Las cuotas no deducidas se
señalan para cotejo con el core antes de calcular cualquier provisión de
cartera. Un depósito ya recibido pero sin detalle por cliente puede cubrir una
deducción aún sin remesa asignada; estos dos importes no se suman ni prueban
por sí solos una CxC a la empresa. El reporte no define tasas de provisión ni
registra asientos.

### Tolerancia automática en US$

En la ficha de cada **Empresa Credinómina**, configure **Tolerancia de conciliación US$**. El valor inicial es **0** (sin ajuste automático) y el máximo admitido es **US$0.10**. La comparación es simétrica: con tolerancia de US$0.01, una aplicación de US$46.52 frente a un depósito de US$46.53 genera **+US$0.01**, y una aplicación de US$46.53 frente a un depósito de US$46.52 genera **−US$0.01**. El signo siempre significa *depósito menos aplicación del core*.

La app crea un **Movimiento de Conciliación** interno, visible en el tablero, las fuentes y los reportes. No altera la aplicación del préstamo, no genera asiento contable y conserva por separado el efectivo realmente depositado. Si el depósito es mayor, solo el centavo físico sobrante que corresponda se clasifica con el movimiento; cualquier otro excedente sigue sin distribuir y requiere justificación. Si el depósito es menor, no se inventa efectivo.

El ajuste automático exige un solo depósito emparejado en banco y contabilidad, en US$, una sola aplicación y un destino inequívoco ya distribuido. No cubre diferencias de tasa C$/US$, referencias ambiguas, repartos de varios depósitos o aplicaciones, ni partidas administrativas. Esos casos permanecen para revisión o distribución manual. Si se cambia la tolerancia o desaparece la coincidencia, la app revierte el movimiento vigente al recalcular; un período cerrado no se altera automáticamente. Revise estos movimientos antes del cierre.

Para la primera quincena el cierre de ciclo es el día 15; para la segunda, el último día del mes. En una empresa mensual la fecha límite de remesa sigue la regla del mes siguiente configurada en la empresa. En una empresa quincenal **registre la fecha límite pactada en cada período**; la app no presupone que ambas quincenas se pagan en la misma fecha. El tablero suma las cifras del mes, pero conserva botones separados para revisar cada quincena. El estado de cuenta y el resumen indican el ciclo de cada movimiento.

Una aplicación del core puede cubrir las dos quincenas. Si el importe en US$ coincide exactamente con los saldos disponibles de Q1 y Q2 del mismo crédito, empresa y mes, la app registra el reparto en la fila de origen y actualiza ambas conciliaciones. Si falta el dato que identifique el reparto o existen varias combinaciones válidas, queda pendiente de revisión.

Cuando falte el detalle de la empresa y un depósito libre cubra exactamente toda la cobranza del período, use **Más opciones → Reconocer cobranza por depósito** y documente la razón. El estado queda **Inferida por depósito**, no como deducción confirmada por planilla. Si llega el detalle real, impórtelo para sustituir la inferencia. Un depósito parcial no habilita esta opción.

Cada reejecucion de la conciliacion recalcula los enlaces a partir de las fuentes efectivas y de las partidas complementarias confirmadas. No genera asientos, recibos, pagos ni modificaciones en el core. Las aplicaciones y los saldos de préstamo se expresan en US$; los depósitos en C$ se convierten solo con una tasa documentada.
