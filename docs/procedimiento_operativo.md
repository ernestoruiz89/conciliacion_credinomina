# Procedimiento operativo de Credinomina

## Principio de control

La aplicación separa cuatro hechos que no deben confundirse:

1. **Cobranza enviada:** importe solicitado en la planilla; es un dato de control, no una CxC.
2. **Deducción confirmada:** evidencia de lo retenido por la empresa; permite revisar la primera conciliación, pero no genera por sí misma CxC en esta herramienta.
3. **Aplicación en el core:** base de la CxC que se debe conciliar, tanto en modalidad histórica como operativa.
4. **Depósito o compensación vinculada:** cobertura efectiva de la aplicación. Solo reduce su CxC el importe asignado o compensado mediante una partida complementaria confirmada y vinculada.

La CxC es lo aplicado menos lo depositado/compensado de forma vinculada, sin descontar dos veces los ajustes ya incluidos en el aplicado neto. El estado de cuenta operativo separa esa CxC de las diferencias de cobranza y de los saldos a favor. No determina la mora contractual del trabajador; para ello se consulta el core y la evidencia de deducción.

## Calendario mensual

Ejemplo para una cuota deducida en abril:

- En abril se crea el periodo de cobranza y se envia el archivo a la empresa.
- La empresa devuelve el detalle definitivo de lo efectivamente deducido.
- La deduccion confirmada se reconoce con fecha de la evidencia de planilla.
- La empresa remite el dinero en mayo, dentro del plazo definido en su ficha (por defecto, hasta el dia 10).
- Se importan los movimientos contables conservando la fecha real de cada aplicación, aunque sea anterior al depósito. El depósito y su detalle se registran en Distribución de Depósito; la segunda conciliación vincula aplicación y depósito/compensación.

El periodo siempre se identifica por el **mes de la planilla**, no por el mes en que llega el deposito.

## Conciliacion 1: cobranza contra deduccion de la empresa

1. Crear una Empresa Credinomina y definir sus dias limite de depósito.
2. Crear el Periodo de Conciliacion con el primer dia del mes de planilla.
3. Adjuntar el archivo de cobranza e importar. La aplicacion reconoce encabezados repetidos y las columnas acordadas.
4. Exportar el archivo para la empresa. Incluye las columnas requeridas y una `Fila ID` tecnica para garantizar la coincidencia exacta.
5. La empresa llena `Deducido C$` o `Deducido US$` y devuelve el mismo archivo.
6. Registrar la fecha de la planilla o constancia que sirve como evidencia de la deduccion.
7. Adjuntar el detalle devuelto e importarlo.

Resultados posibles:

- **Deducción total:** la deducción coincide con lo solicitado; no crea ni liquida por sí sola CxC.
- **Deducción parcial:** se documenta la diferencia de la primera conciliación; no se convierte automáticamente en deuda del trabajador.
- **No deducido:** se documenta el motivo y la gestión necesaria. La CxC de aplicaciones existentes permanece hasta recibir cobertura vinculada, independientemente de esta clasificación.
- **Deduccion en exceso:** no se aplica automaticamente; queda como excepcion.
- **Pendiente de detalle:** la empresa aun no envio evidencia. No equivale a pago ni a falta de pago definitiva.
- **Ambigua o sin coincidencia:** revision humana obligatoria; nunca se asigna por similitud de nombre.

## Implementación histórica y corte de septiembre de 2026

De **abril de 2025 a agosto de 2026**, usar períodos de modalidad **Histórica**. No se importa la cobranza ni se ejecuta la conciliación de deducciones. Se enlazan aplicaciones reales del core en US$ con depósitos registrados en Distribución de Depósito (en US$ o C$ con tasa documentada). Cada aplicación histórica tiene empresa/mes y corte asignados explícitamente: mensual, fecha exacta de aplicación o rango inclusivo de fechas. Las fechas de aplicación pueden ser el 15, el 30 o cualquier otro día, incluso de un mes posterior al de cobranza. El depósito puede cubrir varias aplicaciones, una parte de una aplicación o combinarse con otros depósitos. Los repartos ambiguos requieren Distribución de Depósito confirmada.

El saldo de aplicaciones sin depósito o compensación vinculada constituye la CxC de esta herramienta, con la misma regla que en operativo. No prueba por sí solo que el trabajador no haya pagado ni registra un pago nuevo en el core. Una aplicación de agosto depositada en septiembre permanece en agosto por su período asignado. Desde **septiembre de 2026**, los períodos nuevos son **Operativos** y usan ambas conciliaciones descritas abajo. Véase la secuencia de carga en `docs/instalacion_y_uso.md`.

## Cobranza mensual y quincenal

La **Frecuencia de cobranza** se configura por empresa. Una empresa mensual tiene un solo período operativo por mes; una empresa quincenal tiene dos: primera quincena (cierre día 15) y segunda quincena (cierre el último día del mes). Cada período conserva su archivo de cobranza, respuesta de la empresa, deducción, aplicaciones, depósitos y excepciones. La misma cuota puede aparecer en dos envíos, pero el período y la `Fila ID` distinguen los registros; nunca se suman dos quincenas como si fueran un solo descuento.

La carga **histórica** sigue agrupada por empresa y mes de cobranza asignado, pero puede separarse además por fecha exacta o rango de aplicación. No se presume que un corte sea una primera o segunda quincena: sus fechas se registran según la evidencia real.

Normalmente el core registra una aplicación por quincena. Si registra **una sola aplicación en US$ para ambas**, la conciliación la reparte automáticamente entre Q1 y Q2 únicamente cuando corresponden al mismo crédito, empresa y mes, pasan los controles de cliente, cuota y referencia, y el importe es exactamente la suma de los saldos disponibles de ambas. El reparto queda visible en **Distribución de la aplicación (JSON)** y alimenta los saldos y las referencias de depósito de cada quincena. Si hay varias combinaciones posibles o la aplicación es parcial, queda como excepción para revisión; el sistema no adivina el reparto.

El tablero agrega importes para la vista del mes y permite abrir cada quincena. En el estado de cuenta y los reportes se indica el ciclo para explicar cuándo se envió y dedujo cada importe. La fecha límite mensual se calcula con los días pactados para el mes siguiente. Para empresas quincenales se registra la fecha límite acordada en cada período, sin asumir que la primera y la segunda quincena se pagan juntas o el mes siguiente.

### Reconocer la cobranza como detalle de la empresa

Si la empresa confirmó la deducción completa, use **Más opciones → Reconocer cobranza como detalle de la empresa** en un período abierto. Indique la fecha de evidencia y confirme la deducción. La app copia los importes de cobranza a `Deducido US$` y `Deducido C$`, conservando quién lo reconoció y cuándo. No sobrescribe un detalle o deducciones ya registrados.

Esta acción no registra ni confirma un depósito y no sustituye la evidencia individual de descuento salarial. No reconozca una deducción solo porque llegó dinero. En el depósito, **Usar aplicaciones pendientes como detalle** permite preparar filas con las aplicaciones seleccionadas de sus períodos; revise los destinatarios e importes antes de conciliar. Un detalle generado por la herramienta no debe presentarse como archivo enviado por la empresa.

## Subsidio, suspension e ingreso insuficiente

- Registrar el monto efectivamente deducido, incluso si es cero.
- Usar la excepcion para documentar `subsidio`, `suspension`, `ingreso insuficiente` u otra causa.
- Mantener la diferencia como pendiente de aclaración de cobranza; no sumarla a la CxC ni presentarla automáticamente como deuda del trabajador.
- Determinar cualquier responsabilidad contractual fuera de este cálculo de conciliación, con el convenio y la evidencia correspondiente.
- No capitalizar, condonar ni reprogramar automaticamente desde esta aplicacion; esas decisiones pertenecen al core y a las politicas de credito.

## Cuando el detalle llega tarde

- El periodo permanece como `Pendiente de detalle` y se reporta en excepciones.
- No se simula una deduccion para cuadrar el deposito.
- Cuando llegue la evidencia, se importa al periodo original aunque el archivo llegue a finales de mayo.
- Si el deposito llega antes que el detalle, puede quedar registrado y confirmado sin asignar todavía a una cobranza. Cuando llegue evidencia posterior, se recalcula la conciliación; una aplicación enlazada antes de comprobar la deducción se muestra como **provisional**, no como descuento salarial confirmado.
- Para dejar constancia del cierre de control mensual con pendientes, use **Registrar corte de control** en el período y anote la siguiente gestión. La foto fechada no bloquea evidencia tardía ni equivale a liquidación. `Cerrar período` exige resolver las revisiones de la primera conciliación y liquidar las aplicaciones, sin excepciones abiertas. Una diferencia de cobranza no es por sí misma deuda del empleado.
- Un período `Cerrado` queda en solo lectura. Si se descubre una corrección necesaria, un supervisor o administrador debe usar **Reabrir período** y documentar el motivo; después revisa nuevamente los saldos y ejecuta el cierre otra vez.

## Conciliacion 2: aplicaciones contra deposito

1. Importar solo **Movimientos contables** para las aplicaciones. Antes de cargar, indicar la moneda reportada; si el archivo está en NIO, ingresar la tasa manual C$/US$. La conciliación se realiza en US$ y el monto original en C$ queda conservado. Cada importación debe tener una sola moneda; separar archivos mixtos. Las `DISPENSAS` no se tratan como efectivo.
2. Registrar cada depósito de convenio en **Distribución de Depósito**, con su fecha real, empresa, referencia, importe y moneda. Un supervisor lo **confirma** para que participe en la conciliación; el borrador no tiene ese efecto. **Confirmar depósito** y **Conciliar** son acciones separadas. No cargar el Excel bancario mensual como archivo de **Movimientos contables**: puede mezclar depósitos de otras empresas, clientes sin convenio y movimientos operativos. La importación contable sí puede crear depósitos desde líneas identificadas como tales; revise esos borradores antes de confirmarlos. Cuando la empresa entregue el detalle por cliente, adjuntarlo a ese mismo depósito e importarlo allí. Si el depósito es en C$, indicar una tasa C$/US$ positiva; la app calcula y redondea el equivalente a dos decimales. No exige una justificación de tasa.
3. La aplicación del core se conserva en US$ y se enlaza a una deducción por crédito, importe y referencia cuando la empresa la informó. Si la deducción fue en C$, se usa únicamente una tasa documentada para comparar en US$.
4. El depósito registrado puede cotejarse con un movimiento contable de depósito por referencia e importe en su moneda original o equivalente documentado.
5. El depósito se distribuye en US$ entre aplicaciones y partidas complementarias. Seleccione los períodos que puede cubrir y use **Seleccionar partidas pendientes** para indicar destinos e importes. Cuando la selección manual corresponde a una fila del detalle, vincule esa fila con sus destinos: cuadrar el total del depósito no demuestra por sí solo que cada cliente esté conciliado. El botón **Conciliar** recalcula ese depósito y su conjunto financiero afectado, no toda la empresa indiscriminadamente.

Un depósito puede cubrir parte de una cobranza, varias cobranzas o combinarse con otros depósitos para cubrir una misma cobranza. Cada depósito conserva su importe distribuido, su saldo sin distribuir y el detalle de destinos; cada fila de cobranza muestra los depósitos que la financiaron, lo remitido y lo pendiente de la empresa. Una aplicación parcial del core tampoco se confunde con el pago completo de la cuota.

### Traslado de excepciones entre conciliaciones

El operador puede escribir la **Excepción de cobranza vs aplicación** en la cuota tan pronto se detecte una aplicación parcial, sin esperar el depósito. Una aplicación del core puede enlazarse a la cobranza antes de que llegue el detalle de deducción, siempre que crédito, cuota y demás identificadores dejen un único destino; esto no confirma que la empresa haya descontado ni habilita distribuir un depósito sin evidencia. También se conserva el comentario del archivo de la empresa y la descripción/resolución de la excepción de deducción. Al recibir y distribuir el depósito, el sistema compara por la misma fila de cobranza los faltantes en **US$**: cobranza menos aplicación (descontando partidas complementarias atribuibles a esa fila), cobranza menos deducción y cobranza menos importe remitido. Si el faltante del depósito coincide con alguno de los faltantes ya comentados, muestra ese comentario como **antecedente trasladado** en la cuota y en el reparto del depósito, con la referencia de la excepción cuando exista. No crea otra excepción por el mismo comentario.

Si todavía no existe depósito, no hay comentario trasladado. Si el faltante del pago es distinto, permanece para revisión por separado. Trasladar el antecedente no registra dinero, no resuelve la excepción original, no convierte un depósito parcial en completo y no altera el saldo pendiente. Editar posteriormente el comentario o la resolución recalcula el antecedente mostrado.

La asignación automática de un depósito registrado requiere su detalle por cliente importado. Una referencia única sin ese detalle no basta; alternativamente, el supervisor puede documentar una distribución manual por depósito y destino, indicando referencia bancaria, comprobante contable si hace falta distinguir depósitos, período, `Fila ID` e importe en US$. También puede destinar una distribución a una partida complementaria. El sistema rechaza importes que excedan el saldo del depósito o de la cobranza. No prorratea ni usa FIFO.

Cuando la tasa de la planilla difiere de la tasa del depósito, la diferencia en US$ se muestra por separado y requiere revisión de cada caso antes del cierre.

### Ajustes menores por tolerancia

El supervisor puede definir por empresa una tolerancia de **0 a US$0.10**, inicialmente cero. Tras la distribución de un depósito en US$, si el depósito registrado y una única aplicación del core se enlazan sin ambigüedad, una diferencia absoluta no mayor que la tolerancia crea una **Partida Complementaria**, categoría **Diferencia por tolerancia**. La diferencia firmada es `depósito − aplicación`: US$46.53 depositados contra US$46.52 aplicados producen **+US$0.01**; US$46.52 depositados contra US$46.53 aplicados producen **−US$0.01**. Ambos se muestran sin cambiar el importe aplicado al préstamo ni el dinero recibido.

La partida automática es interna, de solo lectura y no se exporta al core. No la agregue manualmente a Destinos porque su efecto ya está incluido. Si necesita registrarla contablemente, use **Crear / Ver excepción** para dar seguimiento y verificar el asiento importado. El signo positivo puede clasificar únicamente el efectivo sobrante correspondiente; el negativo no representa un depósito ficticio. La tolerancia no resuelve diferencias cambiarias, varias asignaciones posibles, pagos parciales ni partidas administrativas. Si el origen cambia, la partida se revierte de forma trazable; no se recalcula un período cerrado para modificarlo.

## Depósitos mayores que la cobranza

El depósito se registra en el depósito por su importe total, aunque supere las cobranzas informadas. Solo la porción identificada se distribuye a las cobranzas o partidas complementarias. El excedente nunca se aplica automáticamente a un crédito.

- Si la empresa pagó de más por error o remitió una partida no informada, use **Crear saldo a favor de la empresa** en el depósito. Se crea una **Partida Complementaria**, con importe, motivo, responsable y fecha compromiso. Si el exceso pertenece a una persona, use **Crear saldo a favor del cliente** desde su fila del detalle. Una aplicación sin identificar no es automáticamente un saldo a favor.
- El sistema valida que el excedente documentado no supere el saldo sin distribuir del depósito. Esa porción se muestra como **saldo a favor documentado de la empresa**, separado de las cobranzas. No se considera conciliada con un crédito.
- Lo que no tenga justificación queda como **sin distribuir ni justificar** y continúa siendo excepción. Una partida que posteriormente se identifique se debe conciliar mediante la distribución o partida complementaria correspondiente, corrigiendo/cancelando antes la clasificación de excedente para evitar doble uso.
- Esta app no crea automáticamente el pasivo, devolución ni compensación contable en el core; esos movimientos requieren el procedimiento contable autorizado de la IMF.

## Página de control

**Control de Credinómina** separa **Calendario** y **Trabajo de conciliación** en pestañas. La bandeja reúne detalle de empresa, aplicaciones sin período, depósitos por revisar, complementarias, saldos a favor por gestionar y excepciones aunque no tengan período. Siempre consulta **todos los años**, independientemente del año elegido para el calendario, cifras de control y Excel. Filtre por empresa, tipo de pendiente, responsable o compromiso; el filtro considera todas las páginas. La bandeja se carga al abrir su pestaña; si falla, muestra un aviso y permite reintentar, sin presentar un conjunto parcial como completo. La celda mensual muestra por separado los depósitos completos recibidos ese mes, aunque paguen períodos anteriores. El efectivo no debe sumarse otra vez a sus asignaciones.

En el formulario del depósito, **depositado = asignado + saldo a favor documentado + sin asignar ni justificar**. Un depósito completamente distribuido puede mostrar **Conciliado con saldo a favor** y, simultáneamente, una gestión de devolución o aplicación futura pendiente. El saldo a favor original se conserva aunque su gestión se complete; no se vuelve a liberar ese efectivo para pagar otra aplicación. El estado de conciliación no certifica por sí mismo el registro contable en el core. Si el importe no cuadra o el detalle sigue por revisar, no se muestra en verde aunque el resultado guardado diga «Conciliado».

El ejemplo HTML divide cada mes en dos quincenas. Esta versión conserva una celda mensual por empresa que suma los períodos disponibles y muestra cada quincena como acceso separado al detalle; no muestra una quincena inexistente como si ya estuviera conciliada.

## Partidas complementarias con asiento separado

Si la empresa deposita US$100, el core aplica US$90 al crédito y los US$10 restantes son una cobranza administrativa asentada en otro comprobante:

1. Crear una **Partida Complementaria** con referencia, concepto, fecha, importe y justificación. El asiento puede completarse después; si falta, dé seguimiento con **Crear / Ver excepción → Registrar ajuste en el core** y verifique el registro contra la importación contable. Si el importe está en C$, indique la tasa C$/US$.
2. El supervisor revisa y confirma la partida. Solo las partidas confirmadas participan en la conciliación. No se genera ni se modifica el asiento contable desde esta app.
3. Si esos US$10 corresponden a una cuota concreta, indicar crédito y, de ser necesario, empresa, período, cliente y número de cuota. Solo se atribuyen a la fila de cobranza cuando el enlace es único. Sin ese enlace, cuadran el depósito pero no reducen un saldo individual.
4. Al recalcular, la segunda conciliación puede asignar **US$90 a la cobranza del crédito + US$10 a la partida complementaria = US$100 depositados**. El estado de cuenta mantiene visibles los US$90 de aplicación y los US$10 administrativos por separado; no registra ficticiamente US$100 como pago del préstamo. Si el reparto no es único, debe documentarse con Distribuciones de Depósito.

La referencia sola no prueba que una partida pertenezca a un cliente. Conserve la justificación y documente el comprobante cuando exista. La cabecera de la partida separa importe utilizado, pendiente financiero y situación contable: conciliar no equivale a contabilizar.

## Estado de cuenta al cliente

**Estado de Cuenta Operativo** presenta la posición actual por tipo de fila:

- **Cobranza**: importe solicitado, deducido, cuota no deducida y pendiente de
  aplicación en el core. Son controles informativos, no deuda calculada.
- **Aplicación**: aplicado neto de ajustes confirmados, depósito asignado,
  partidas complementarias y pendiente. Incluye histórico, operativo y aplicaciones sin período;
  si varias aplicaciones comparten una cobranza, su efectivo se cuenta una vez.
- **Partida complementaria**: importe original, utilizado, pendiente financiero,
  situación contable y saldo a favor pendiente de gestión. El documento enlazado
  conserva las gestiones y sus reversiones.
- **CxC por ajuste**: faltante trasladado a una partida, con su pendiente después
  de cobros o compensaciones vinculados. No se confunde con el saldo financiero
  utilizado para cuadrar el depósito.

Use **Tipo de posición** para reducir las columnas cuando revise una etapa.
La cobranza solicitada y lo no deducido son datos informativos de la primera
conciliación: **no generan una cuenta por cobrar**. La CxC de esta herramienta
es el **aplicado en el core menos depósitos asignados y compensaciones confirmadas
vinculadas**. El aplicado neto ya descuenta los ajustes a aplicaciones; no se
restan dos veces. Las diferencias por tolerancia se presentan como partidas
complementarias, sin columnas adicionales de redondeo o efectivo de ajustes.
Por ejemplo, aplicado US$100, ajuste confirmado US$20 y depósito US$30 dejan
CxC US$50, sin importar cuánto se había solicitado cobrar.

La CxC y el saldo a favor pendiente de gestión no se compensan automáticamente.
Las partidas genéricas de
empresa no se atribuyen automáticamente a una persona. Los enlaces de origen
y las filas identificadas permiten revisar las aplicaciones agrupadas.

Los filtros Desde/Hasta usan el mes de cobranza, la fecha de aplicación o la
fecha de la partida según su tipo. No reconstruyen un saldo de fecha pasada.
El resumen y la antigüedad recuperan todas las páginas de datos autorizadas,
sin cortar los totales en 10000 períodos o 100000 filas.

El reporte **Estado de Cuenta Operativo** debe acompanarse del estado oficial del core cuando se requiera capital, intereses y saldo contractual. El reporte de esta app explica las partidas en transito:

- cobrado en planilla;
- deducido al trabajador;
- diferencia de cobranza pendiente de aclarar (informativa);
- deducido pero pendiente de aplicar en el core;
- cuenta por cobrar a la empresa;
- aplicado y depositado/compensado.
- partida complementaria y diferencia cambiaria, cuando existan, separadas del pago al crédito.

Ante una consulta, no se presenta la diferencia de cobranza como saldo contractual del trabajador. Si el core aún no refleja la aplicación, el estado debe indicar `Deducido por la empresa; pendiente de aplicar en el core` y mostrar la referencia disponible, sin generar CxC de aplicación ficticia.

## Cierre y controles

- Resolver o justificar todas las excepciones.
- Verificar que las aplicaciones conciliadas tengan cobertura trazable por depósito asignado o compensación confirmada y vinculada.
- Registrar un corte de control mensual si aún hay partidas abiertas, sin presentarlo como liquidación.
- Cerrar el período después de resolver las revisiones de cobranza y liquidar las aplicaciones; las diferencias de la primera conciliación no se presentan como CxC.
- Conservar los archivos originales, su hash, usuario y fecha de importacion.
- Restringir acceso porque los archivos contienen cedulas e informacion salarial.

## Saldos a favor y rendición

Si se reciben US$1,000, se distribuyen US$800 a créditos, US$100 a cobranza
administrativa y US$100 a saldo a favor, los US$1,000 quedan explicados, pero
pueden quedar US$100 por devolver. Ese pendiente de gestión se presenta por
separado y nunca se convierte en otro pago ni libera el depósito original.

Registre la devolución o aplicación externa, parcial o total, con responsable,
fecha compromiso, referencia y soporte. Una clasificación equivocada se
cancela expresamente antes de redistribuir, siempre que no existan gestiones
realizadas ni períodos cerrados afectados. No compense automáticamente saldos
de personas o empresas distintas.

Para rendir el mes sin detener datos tardíos, registre un corte de control y
consulte **Más opciones → Ver cortes registrados**. Cada corte conserva Excel
y JSON privados del período con las cifras de ese momento. La antigüedad
normal es actual; no debe presentarse como reconstrucción de una fecha pasada.
Respalde tanto la base (incluido Version) como los archivos privados.

## Corrección trazable de gestiones y compensaciones

En un saldo a favor, use **Revertir gestión** para corregir una devolución o
aplicación externa registrada por error. Seleccione la gestión original e indique
fecha y motivo. La app agrega una entrada negativa enlazada al registro original;
no elimina el historial, no modifica el asiento del core y no libera efectivo del
depósito. El importe vuelve a quedar pendiente de gestión. No se puede cancelar
la partida para borrar ese historial, aunque todas sus gestiones estén revertidas.

En una compensación entre partidas, use **Revertir compensación**. La reversión
se registra en ambas partidas dentro de una sola transacción y restaura sus
pendientes por el importe original compensado. La fecha no puede preceder al
último movimiento de cualquiera de las dos partidas. Las consultas a fechas
anteriores conservan el saldo que correspondía antes de la reversión. Repetir
una misma confirmación no duplica su efecto. Se requieren permisos de escritura
y confirmación en ambas partidas; los períodos cerrados siguen protegidos.

## Evidencia de registro en el core

Escribir un comprobante deja la partida como **Asiento informado**, no como
registro verificado. **Importada del core** indica que existe evidencia contable
original completa, no que se haya aprobado su clasificación o distribución.
Para la gestión **Registrar ajuste en el core**, la excepción debe comprobar
el asiento contra la importación antes de mostrar **Registro verificado**.
Un depósito conciliado puede conservar una gestión contable pendiente.

Un detalle de empresa ausente o inconsistente se muestra como **Detalle de
empresa pendiente** o **Detalle de empresa por aclarar**. Ni la solicitud de
cobranza ni la deducción crean esta CxC, incluso cuando el detalle es válido.
La CxC nace de las aplicaciones y su cobertura confirmada, en ambas modalidades.

## Cobrar una CxC por ajuste

Un ajuste negativo con subcategoría **CxC a la empresa** puede cuadrar un depósito
sin liquidar la deuda trasladada. Por ejemplo, aplicación US$100, depósito US$90
y ajuste −US$10 explican la distribución de US$90; siguen existiendo US$10 por cobrar.
La antigüedad muestra **CxC total** por defecto y permite separar **Aplicado
pendiente de depósito** de **CxC por ajustes**. No reste los saldos a favor ni el
efectivo sin asignar sin una liquidación vinculada.

Desde la partida original confirmada, use **Aplicar cobro / Compensar CxC**:

1. Seleccione la empresa deudora. Si la partida es genérica, su deuda se separa
   por las distribuciones reales de cada empresa, sin atribuirla a un cliente.
2. Seleccione un depósito confirmado con saldo disponible, o una partida de la
   misma empresa apta para compensar. Indique importe, fecha y motivo.
3. Un supervisor confirma. La app crea un registro complementario vinculado al
   origen y a la liquidación, sin duplicar un asiento del core. Solo el depósito
   efectivamente distribuido o la compensación confirmada reduce la CxC.
4. Revise **CxC original**, **Cobrado**, **Compensado** y **Pendiente**. Una
   liquidación parcial US$3 del ejemplo deja US$7 por cobrar. Escribir o verificar
   un comprobante no paga esa deuda. No se admite cerrarla por condonación.

Las correcciones conservan el historial. Revierta la distribución o compensación
equivocada antes de cancelar su registro; el pendiente vuelve a quedar abierto.
Si un depósito conserva un destino hacia una partida cancelada o sin confirmar,
retire o corrija ese destino expresamente antes de reutilizar el efectivo.

Una aplicación con efectivo asignado o reservado no puede ocultarse como
ignorada, desactivarse ni borrarse para corregirla. Primero corrija sus vínculos.
Los resultados calculados no son campos para edición manual; un CSV sin
asignaciones puede reprocesarse mediante la acción normal de carga.
