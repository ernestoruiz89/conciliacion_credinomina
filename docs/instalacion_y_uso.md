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

La instalacion crea los roles `Operador Credinomina` y `Supervisor Credinomina`.
Asigne uno de ellos a cada usuario y configure el idioma del usuario como Espanol.

## Orden de uso

### Carga histórica: abril 2025 a agosto 2026

1. Cree un **Período de Conciliación** por empresa y mes que vaya a reconstruir; seleccione modalidad **Histórica**. No adjunte cobranza ni detalle de deducción.
2. Importe **Movimientos contables** como principal y **Transacciones** como respaldo de las aplicaciones. Marque **Carga histórica de aplicaciones**. Si un archivo corresponde a una sola empresa/mes, indique el período histórico predeterminado. Si mezcla empresas o meses, deje ese campo vacío, importe y asigne el **Período histórico** a cada fila de aplicación antes de pulsar **Reconciliar todas las fuentes**; las filas sin asignar quedarán pendientes aunque su fecha sea septiembre de 2026 o posterior. Si el mismo archivo incluye aplicaciones operativas de septiembre, marque esas filas con **Ruta de aplicación = Operativa**. La fecha de aplicación histórica puede ser posterior al mes histórico; el período se asigna explícitamente.
3. Importe el **Detalle de depósitos** por separado, sin período histórico predeterminado. La pareja banco–contabilidad exige referencia, moneda/importe o conversión documentada; una referencia repetida se desambigua automáticamente solo cuando la fecha exacta identifica un único par. **Transacciones** se omite frente a un movimiento contable de la misma aplicación únicamente cuando también coinciden importe, moneda y fecha; una referencia reutilizada en otro mes no se descarta.
4. Revise en **Control de Credinómina** las aplicaciones sin depósito. Una referencia única permite asignación automática en US$; una referencia reutilizada entre meses, compartida con cobranza operativa o con varios depósitos y aplicaciones exige una **Distribución de Remesa** con el **ID de aplicación histórica** visible en la fila importada. Puede distribuir parcialmente, usar varios depósitos para una aplicación o un depósito para varias aplicaciones.
5. Documente partidas administrativas con **Partida Complementaria** y excedentes con **Excedente de Depósito**; nunca aumentan ficticiamente lo aplicado al crédito. Cierre el período histórico solo cuando todas sus aplicaciones queden cubiertas por depósitos.

El saldo histórico **aplicación sin depósito** no es una cuenta por cobrar a la empresa ni un faltante del trabajador: no se reconstruyó la primera conciliación. Conserve los archivos originales y soportes de distribuciones manuales. Una aplicación histórica de agosto de 2026 cobrada en septiembre puede seguir vinculada a agosto mediante su período explícito.

### Operación desde septiembre 2026

1. Crear las empresas e indicar su **Frecuencia de cobranza**: Mensual o Quincenal.
2. Crear un período **Operativo** por empresa y mes. Para una empresa mensual seleccione **Mensual**; para una quincenal cree **Primera quincena** y **Segunda quincena** por separado, cada una con su propio archivo, respuesta y conciliación. El sistema impide duplicar una quincena o mezclar un período mensual con quincenas en el mismo mes.
3. Importar la cobranza, exportarla y cargar la respuesta de la empresa.
4. Crear una Importacion de Fuente por cada archivo contable, de transacciones o de depositos.
5. Ejecutar `Importar y conciliar`.
6. Si un depósito incluye cobranza administrativa u otro ingreso contabilizado en un asiento distinto, crear una **Partida Complementaria** con la referencia bancaria y el comprobante. El supervisor la confirma y se recalcula la conciliación.
7. Para repartos ambiguos, crear una **Distribución de Remesa** por depósito y cobranza (o partida complementaria), con importe en US$. Usar la `Fila ID` exportada para identificar la cobranza. El supervisor la confirma; los importes que exceden los saldos no se aplican.
8. Si el depósito supera las obligaciones identificadas, documentar el saldo no distribuido en **Excedente de Depósito**. El saldo a favor de la empresa queda separado y no se aplica al crédito.
9. Revisar la página **Control de Credinómina** desde el espacio de trabajo o la ruta `/app/control-credinomina`, además de Estado de Cuenta Operativo, Resumen de Conciliacion y Excepciones.

### Tolerancia automática en US$

En la ficha de cada **Empresa Credinómina**, configure **Tolerancia de conciliación US$**. El valor inicial es **0** (sin ajuste automático) y el máximo admitido es **US$0.10**. La comparación es simétrica: con tolerancia de US$0.01, una aplicación de US$46.52 frente a un depósito de US$46.53 genera **+US$0.01**, y una aplicación de US$46.53 frente a un depósito de US$46.52 genera **−US$0.01**. El signo siempre significa *depósito menos aplicación del core*.

La app crea un **Movimiento de Conciliación** interno, visible en el tablero, las fuentes y los reportes. No altera la aplicación del préstamo, no genera asiento contable y conserva por separado el efectivo realmente depositado. Si el depósito es mayor, solo el centavo físico sobrante que corresponda se clasifica con el movimiento; cualquier otro excedente sigue sin distribuir y requiere justificación. Si el depósito es menor, no se inventa efectivo.

El ajuste automático exige un solo depósito emparejado en banco y contabilidad, en US$, una sola aplicación y un destino inequívoco ya distribuido. No cubre diferencias de tasa C$/US$, referencias ambiguas, repartos de varios depósitos o aplicaciones, ni partidas administrativas. Esos casos permanecen para revisión o distribución manual. Si se cambia la tolerancia o desaparece la coincidencia, la app revierte el movimiento vigente al recalcular; un período cerrado no se altera automáticamente. Revise estos movimientos antes del cierre.

Para la primera quincena el cierre de ciclo es el día 15; para la segunda, el último día del mes. En una empresa mensual la fecha límite de remesa sigue la regla del mes siguiente configurada en la empresa. En una empresa quincenal **registre la fecha límite pactada en cada período**; la app no presupone que ambas quincenas se pagan en la misma fecha. El tablero suma las cifras del mes, pero conserva botones separados para revisar cada quincena. El estado de cuenta y el resumen indican el ciclo de cada movimiento.

Una aplicación del core puede cubrir las dos quincenas. Si el importe en US$ coincide exactamente con los saldos disponibles de Q1 y Q2 del mismo crédito, empresa y mes, la app registra el reparto en la fila de origen y actualiza ambas conciliaciones. Si falta el dato que identifique el reparto o existen varias combinaciones válidas, queda pendiente de revisión.

Cuando falte el detalle de la empresa y un depósito contable/bancario libre cubra exactamente toda la cobranza del período, abra el período y use **Conciliación 1 → Reconocer cobranza por depósito**. Seleccione el depósito y documente la razón. El estado queda **Inferida por depósito**, no como deducción confirmada por planilla; se muestra así en el tablero y el estado de cuenta. Si llega el detalle real, impórtelo para sustituir la inferencia, o revierta el reconocimiento antes del cierre si se eligió el depósito incorrecto. Un depósito parcial o con diferencia cambiaria no comprobada no habilita esta opción.

Cada reejecucion de la conciliacion recalcula los enlaces a partir de las fuentes efectivas y de las partidas complementarias confirmadas. No genera asientos, recibos, pagos ni modificaciones en el core. Las aplicaciones y los saldos de préstamo se expresan en US$; los depósitos en C$ se convierten solo con una tasa documentada.
