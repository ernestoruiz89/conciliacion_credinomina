# Procedimiento operativo de Credinomina

## Principio de control

La aplicacion separa tres hechos que no deben confundirse:

1. **Cuota enviada a cobro:** obligacion esperada del trabajador en la planilla.
2. **Deduccion confirmada:** la empresa demuestra que retuvo el dinero al trabajador. Desde esa fecha el importe confirmado deja de ser un pendiente operativo del trabajador y pasa a ser una cuenta por cobrar a la empresa.
3. **Remesa y aplicacion:** la empresa deposita el dinero y el core registra la aplicacion al credito.

El atraso de la empresa en remitir el dinero no debe presentarse al cliente como mora por el importe que ya fue deducido y documentado. El estado de cuenta operativo muestra por separado el saldo del trabajador, la cuenta por cobrar a la empresa y el pago pendiente de aplicar en el core.

## Calendario mensual

Ejemplo para una cuota deducida en abril:

- En abril se crea el periodo de cobranza y se envia el archivo a la empresa.
- La empresa devuelve el detalle definitivo de lo efectivamente deducido.
- La deduccion confirmada se reconoce con fecha de la evidencia de planilla.
- La empresa remite el dinero en mayo, dentro del plazo definido en su ficha (por defecto, hasta el dia 10).
- En mayo se importan movimientos contables, transacciones de respaldo y detalle bancario. La segunda conciliacion vincula aplicacion, deposito contable y deposito bancario.

El periodo siempre se identifica por el **mes de la planilla**, no por el mes en que llega el deposito.

## Conciliacion 1: cobranza contra deduccion de la empresa

1. Crear una Empresa Credinomina y definir sus dias limite de remesa.
2. Crear el Periodo de Conciliacion con el primer dia del mes de planilla.
3. Adjuntar el archivo de cobranza e importar. La aplicacion reconoce encabezados repetidos y las columnas acordadas.
4. Exportar el archivo para la empresa. Incluye las columnas requeridas y una `Fila ID` tecnica para garantizar la coincidencia exacta.
5. La empresa llena `Deducido C$` o `Deducido US$` y devuelve el mismo archivo.
6. Registrar la fecha de la planilla o constancia que sirve como evidencia de la deduccion.
7. Adjuntar el detalle devuelto e importarlo.

Resultados posibles:

- **Deduccion total:** trabajador cubierto; se abre cuenta por cobrar a la empresa.
- **Deduccion parcial:** solo el importe confirmado pasa a la empresa; la diferencia sigue a cargo del trabajador.
- **No deducido:** no se reconoce pago ni cuenta por cobrar a la empresa. Debe documentarse el motivo y gestionarse recuperacion o reprogramacion.
- **Deduccion en exceso:** no se aplica automaticamente; queda como excepcion.
- **Pendiente de detalle:** la empresa aun no envio evidencia. No equivale a pago ni a falta de pago definitiva.
- **Ambigua o sin coincidencia:** revision humana obligatoria; nunca se asigna por similitud de nombre.

## Implementación histórica y corte de septiembre de 2026

De **abril de 2025 a agosto de 2026**, usar períodos de modalidad **Histórica**. No se importa la cobranza ni se ejecuta la conciliación de deducciones. Se enlazan aplicaciones reales del core en US$ con depósitos contables y bancarios (en US$ o C$ con tasa documentada). Cada aplicación histórica tiene empresa/mes y corte asignados explícitamente: mensual, fecha exacta de aplicación o rango inclusivo de fechas. Las fechas de aplicación pueden ser el 15, el 30 o cualquier otro día, incluso de un mes posterior al de cobranza. El depósito puede cubrir varias aplicaciones, una parte de una aplicación o combinarse con otros depósitos. Los repartos ambiguos requieren Distribución de Remesa confirmada.

Este saldo **no** prueba que la empresa deba dinero ni que el trabajador no haya pagado: son conclusiones que exigirían la cobranza y la deducción, ausentes en el histórico. Tampoco se registra un pago nuevo en el core. Una aplicación de agosto depositada en septiembre permanece en agosto por su período asignado. Desde **septiembre de 2026**, los períodos nuevos son **Operativos** y usan ambas conciliaciones descritas abajo. Véase la secuencia de carga en `docs/instalacion_y_uso.md`.

## Cobranza mensual y quincenal

La **Frecuencia de cobranza** se configura por empresa. Una empresa mensual tiene un solo período operativo por mes; una empresa quincenal tiene dos: primera quincena (cierre día 15) y segunda quincena (cierre el último día del mes). Cada período conserva su archivo de cobranza, respuesta de la empresa, deducción, aplicaciones, depósitos y excepciones. La misma cuota puede aparecer en dos envíos, pero el período y la `Fila ID` distinguen los registros; nunca se suman dos quincenas como si fueran un solo descuento.

La carga **histórica** sigue agrupada por empresa y mes de cobranza asignado, pero puede separarse además por fecha exacta o rango de aplicación. No se presume que un corte sea una primera o segunda quincena: sus fechas se registran según la evidencia real.

Normalmente el core registra una aplicación por quincena. Si registra **una sola aplicación en US$ para ambas**, la conciliación la reparte automáticamente entre Q1 y Q2 únicamente cuando corresponden al mismo crédito, empresa y mes, pasan los controles de cliente, cuota y referencia, y el importe es exactamente la suma de los saldos disponibles de ambas. El reparto queda visible en **Distribución de la aplicación (JSON)** y alimenta los saldos y las referencias de depósito de cada quincena. Si hay varias combinaciones posibles o la aplicación es parcial, queda como excepción para revisión; el sistema no adivina el reparto.

El tablero agrega importes para la vista del mes y permite abrir cada quincena. En el estado de cuenta y los reportes se indica el ciclo para explicar cuándo se envió y dedujo cada importe. La fecha límite mensual se calcula con los días pactados para el mes siguiente. Para empresas quincenales se registra la fecha límite acordada en cada período, sin asumir que la primera y la segunda quincena se pagan juntas o el mes siguiente.

### Reconocimiento provisional por depósito coincidente

Si la empresa no remite el detalle de planilla pero el **depósito contable y el detalle bancario** forman una pareja inequívoca, libre de distribuciones, y el total coincide con la cobranza completa del período, el operador puede elegir **Conciliación 1 → Reconocer cobranza por depósito**. La app compara en US$; si la cobranza y el depósito también están expresados en C$, exige coincidencia en C$ y una tasa documentada para el equivalente en US$. No se ofrece esta opción para depósitos parciales, ya usados, anteriores al cierre del ciclo o atribuibles a otra empresa identificada.

El operador selecciona el depósito y escribe una justificación. La app registra quién lo reconoció, cuándo y qué depósito se usó; asigna el depósito a las filas de cobranza y muestra **Inferida por depósito** en lugar de presentar la deducción como confirmada por la empresa. El estado de cuenta y el tablero conservan esa advertencia. Este reconocimiento **no es prueba individual de descuento salarial** ni sustituye el detalle de planilla para investigar reclamaciones de trabajadores. Si llega el detalle real, se importa y reemplaza la inferencia; si fue un error, use **Revertir reconocimiento por depósito** antes del cierre.

## Subsidio, suspension e ingreso insuficiente

- Registrar el monto efectivamente deducido, incluso si es cero.
- Usar la excepcion para documentar `subsidio`, `suspension`, `ingreso insuficiente` u otra causa.
- Mantener la diferencia como pendiente del trabajador hasta que exista un nuevo acuerdo, reprogramacion o recuperacion autorizada.
- No trasladar a la empresa un importe que no retuvo, salvo que el convenio le asigne expresamente esa responsabilidad.
- No capitalizar, condonar ni reprogramar automaticamente desde esta aplicacion; esas decisiones pertenecen al core y a las politicas de credito.

## Cuando el detalle llega tarde

- El periodo permanece como `Pendiente de detalle` y se reporta en excepciones.
- No se simula una deduccion para cuadrar el deposito.
- Cuando llegue la evidencia, se importa al periodo original aunque el archivo llegue a finales de mayo.
- Si el deposito llega antes que el detalle, puede quedar conciliado con el banco pero sin asignar a una cobranza hasta completar la primera conciliacion.
- El cierre mensual puede hacerse con una lista explicita de partidas pendientes, pero el periodo no se marca `Cerrado` mientras existan excepciones.
- Un período `Cerrado` queda en solo lectura. Si se descubre una corrección necesaria, un supervisor o administrador debe usar **Reabrir período** y documentar el motivo; después revisa nuevamente los saldos y ejecuta el cierre otra vez.

## Conciliacion 2: aplicaciones contra deposito

1. Importar **Movimientos contables** como fuente principal de aplicaciones.
2. Importar **Transacciones** como respaldo. Una aplicacion equivalente se ignora si ya existe en la fuente principal. Las `DISPENSAS` no se tratan como efectivo.
3. Registrar cada depósito de convenio en **Distribución de Remesa**, con su fecha real, empresa, referencia, importe, moneda y soporte. No cargar el Excel bancario mensual en **Importación de Fuente**: puede mezclar depósitos de otras empresas, clientes sin convenio y movimientos operativos. Cuando la empresa entregue el detalle por cliente, adjuntarlo a esa misma remesa e importarlo allí. Las importaciones bancarias anteriores se conservan para consulta y conciliación, pero no admiten nuevas cargas.
4. La aplicación del core se conserva en US$ y se enlaza a una deducción por crédito, importe y referencia cuando la empresa la informó. Si la deducción fue en C$, se usa únicamente una tasa documentada para comparar en US$.
5. El depósito bancario se concilia con el depósito contable por referencia e importe en su moneda original o equivalente documentado.
6. La remesa se distribuye en US$ entre cobranzas y partidas complementarias. La conciliación banco-contabilidad puede quedar completa aunque la aplicación en el core o la distribución a la planilla sigan parciales; son controles separados.

Un depósito puede cubrir parte de una cobranza, varias cobranzas o combinarse con otros depósitos para cubrir una misma cobranza. Cada depósito conserva su importe distribuido, su saldo sin distribuir y el detalle de destinos; cada fila de cobranza muestra los depósitos que la financiaron, lo remitido y lo pendiente de la empresa. Una aplicación parcial del core tampoco se confunde con el pago completo de la cuota.

### Traslado de excepciones entre conciliaciones

El operador puede escribir la **Excepción de cobranza vs aplicación** en la cuota tan pronto se detecte una aplicación parcial, sin esperar el depósito. Una aplicación del core puede enlazarse a la cobranza antes de que llegue el detalle de deducción, siempre que crédito, cuota y demás identificadores dejen un único destino; esto no confirma que la empresa haya descontado ni habilita distribuir una remesa sin evidencia. También se conserva el comentario del archivo de la empresa y la descripción/resolución de la excepción de deducción. Al recibir y distribuir la remesa, el sistema compara por la misma fila de cobranza los faltantes en **US$**: cobranza menos aplicación (descontando partidas complementarias atribuibles a esa fila), cobranza menos deducción y cobranza menos importe remitido. Si el faltante del depósito coincide con alguno de los faltantes ya comentados, muestra ese comentario como **antecedente trasladado** en la cuota y en el reparto del depósito, con la referencia de la excepción cuando exista. No crea otra excepción por el mismo comentario.

Si todavía no existe depósito, no hay comentario trasladado. Si el faltante del pago es distinto, permanece para revisión por separado. Trasladar el antecedente no registra dinero, no resuelve la excepción original, no convierte un depósito parcial en completo y no altera el saldo pendiente. Editar posteriormente el comentario o la resolución recalcula el antecedente mostrado.

La asignación automática de un depósito registrado requiere su detalle por cliente importado. Una referencia única sin ese detalle no basta; alternativamente, el supervisor puede documentar una distribución manual por depósito y destino, indicando referencia bancaria, comprobante contable si hace falta distinguir depósitos, período, `Fila ID` e importe en US$. También puede destinar una distribución a una partida complementaria. El sistema rechaza importes que excedan el saldo del depósito o de la cobranza. No prorratea ni usa FIFO.

Cuando la tasa de la planilla difiere de la tasa del depósito, la diferencia en US$ se muestra por separado y requiere revisión de cada caso antes del cierre.

### Ajustes menores por tolerancia

El supervisor puede definir por empresa una tolerancia de **0 a US$0.10**, inicialmente cero. Tras la distribución de una remesa en US$, si el depósito bancario/contable y una única aplicación del core se enlazan sin ambigüedad, una diferencia absoluta no mayor que la tolerancia crea un **Movimiento de Conciliación** interno. La diferencia firmada es `depósito − aplicación`: US$46.53 depositados contra US$46.52 aplicados producen **+US$0.01**; US$46.52 depositados contra US$46.53 aplicados producen **−US$0.01**. Ambos se muestran en el período y el tablero sin cambiar el importe aplicado al préstamo ni el dinero recibido.

Este movimiento no es un asiento contable y no se exporta al core. El signo positivo puede clasificar únicamente el efectivo sobrante correspondiente; el negativo no representa un depósito ficticio. La tolerancia no resuelve diferencias cambiarias, varias asignaciones posibles, pagos parciales ni partidas administrativas. Si el origen cambia, el movimiento se revierte de forma trazable; no se recalcula un período cerrado para modificarlo.

## Depósitos mayores que la cobranza

El depósito bancario y contable se registra por su importe total, aunque supere las cobranzas informadas. Solo la porción identificada se distribuye a las cobranzas o partidas complementarias. El excedente nunca se aplica automáticamente a un crédito.

- Si la empresa pagó de más por error o remitió una partida no informada, el supervisor crea un **Excedente de Depósito** con período, referencia, comprobante cuando sea necesario, importe en US$, motivo y explicación de su tratamiento.
- El sistema valida que el excedente documentado no supere el saldo sin distribuir del depósito. Esa porción se muestra como **saldo a favor documentado de la empresa**, separado de las cobranzas. No se considera conciliada con un crédito.
- Lo que no tenga justificación queda como **sin distribuir ni justificar** y continúa siendo excepción. Una partida que posteriormente se identifique se debe conciliar mediante la distribución o partida complementaria correspondiente, corrigiendo/cancelando antes la clasificación de excedente para evitar doble uso.
- Esta app no crea automáticamente el pasivo, devolución ni compensación contable en el core; esos movimientos requieren el procedimiento contable autorizado de la IMF.

## Página de control

**Control de Credinómina** muestra una matriz por empresa y mes de planilla, inspirada en el tablero facilitado. Cada celda presenta remitido frente a deducido, estado y cuenta por cobrar; al abrirla se ven las cuotas, excepciones y saldos a favor del período. La parte inferior lista depósitos pendientes de distribuir con su porción documentada y sin clasificar. Usa datos reales de esta app; no reproduce cifras ficticias ni envía datos a Gemini u otro servicio externo.

El ejemplo HTML divide cada mes en dos quincenas. Esta versión conserva una celda mensual por empresa que suma los períodos disponibles y muestra cada quincena como acceso separado al detalle; no muestra una quincena inexistente como si ya estuviera conciliada.

## Partidas complementarias con asiento separado

Si la empresa deposita US$100, el core aplica US$90 al crédito y los US$10 restantes son una cobranza administrativa asentada en otro comprobante:

1. Crear una **Partida Complementaria** con la misma referencia del depósito, el concepto, número y línea del asiento, fecha, importe y justificación. Si el asiento está en C$, registrar la tasa C$/US$ y su evidencia.
2. El supervisor revisa y confirma la partida. Solo las partidas confirmadas participan en la conciliación. No se genera ni se modifica el asiento contable desde esta app.
3. Si esos US$10 corresponden a una cuota concreta, indicar crédito y, de ser necesario, empresa, período, cliente y número de cuota. Solo se atribuyen a la fila de cobranza cuando el enlace es único. Sin ese enlace, cuadran la remesa pero no reducen un saldo individual.
4. Al recalcular, la segunda conciliación puede asignar **US$90 a la cobranza del crédito + US$10 a la partida complementaria = US$100 depositados**. El estado de cuenta mantiene visibles los US$90 de aplicación y los US$10 administrativos por separado; no registra ficticiamente US$100 como pago del préstamo. Si el reparto no es único, debe documentarse con Distribuciones de Remesa.

La referencia sola no prueba que una partida pertenezca a un cliente. Deben conservarse el comprobante y la justificación; si falta cualquiera de ellos, el caso permanece para revisión humana.

## Estado de cuenta al cliente

El reporte **Estado de Cuenta Operativo** debe acompanarse del estado oficial del core cuando se requiera capital, intereses y saldo contractual. El reporte de esta app explica las partidas en transito:

- cobrado en planilla;
- deducido al trabajador;
- pendiente del trabajador;
- deducido pero pendiente de aplicar en el core;
- cuenta por cobrar a la empresa;
- aplicado y remitido.
- partida complementaria y diferencia cambiaria, cuando existan, separadas del pago al crédito.

Ante una consulta, el saldo operativo del trabajador excluye cualquier deduccion ya confirmada. Si el core aun no refleja la aplicacion, el estado debe decir expresamente `Deducido por la empresa; pendiente de aplicar en el core` y mostrar la referencia disponible.

## Cierre y controles

- Resolver o justificar todas las excepciones.
- Verificar que las aplicaciones conciliadas tengan evidencia de deposito.
- Cerrar el periodo solo cuando todas las filas con deduccion esten aplicadas y remitidas.
- Conservar los archivos originales, su hash, usuario y fecha de importacion.
- Restringir acceso porque los archivos contienen cedulas e informacion salarial.
