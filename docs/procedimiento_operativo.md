# Manual operativo de conciliación Credinómina

Esta guía permite cargar evidencia del core, relacionarla con los depósitos y dar seguimiento a diferencias, ajustes y saldos a favor. Está dirigida a los operadores y supervisores de conciliación de MIDESA. La app funciona sobre Frappe Framework, sin depender de ERPNext ni de Loan Manager, y no sustituye el core de crédito ni registra asientos en él.

Versión del manual: 1.2. Fecha de revisión: 7 de octubre de 2026. Referencia de plataforma: Frappe Framework v15 o superior. Las rutas indicadas son relativas al sitio instalado; pueden abrirse mediante la búsqueda de Frappe. Los nombres de botones corresponden a la versión de la app revisada.

## Cómo usar el manual

Para una carga histórica, siga preparación, cartera, movimientos contables, período histórico y depósito. Para una operación nueva, agregue cobranza y detalle de deducción antes de completar las aplicaciones y depósitos. Consulte los capítulos de ajustes y saldos a favor solamente cuando el caso lo requiera.

Cada procedimiento explica la acción y el control posterior. Guardar un documento, adjuntar un archivo y conciliar son operaciones distintas. Antes de una confirmación, revise el importe, la empresa, el cliente y los vínculos. Una referencia parecida o un total que cuadra no demuestra por sí solo una distribución correcta.

Los ejemplos numéricos son didácticos y están expresados en US$, salvo indicación de C$. Las autorizaciones institucionales para reembolsos, reclasificaciones y correcciones deben obtenerse fuera de la app cuando corresponda. El manual no concede esas autorizaciones.

## Responsabilidades y acceso

| Participante | Trabajo habitual | Control que debe conservar |
| --- | --- | --- |
| Operador Credinomina | Preparar catálogos, importar archivos, identificar filas, proponer destinos y registrar gestiones permitidas | Archivo original, referencia, motivo y vínculos verificables |
| Supervisor Credinomina | Revisar y confirmar depósitos y partidas, liquidar CxC por ajustes, cerrar o reabrir períodos según sus permisos | Evidencia del importe y destino, revisión de saldos y motivo de correcciones |
| Contabilidad de la IMF | Registrar correcciones y devoluciones en el core y entregar movimientos y balanza externa | Asiento, fecha, línea contable y soporte del registro |
| Administrador del sitio | Asignar accesos, mantener workers, realizar respaldos y aplicar actualizaciones | Base de datos, Version, archivos privados y configuración de recuperación |

La disponibilidad de botones depende de los permisos del usuario y del estado del documento. No comparta credenciales ni use una sesión de administrador para sustituir la revisión del supervisor. La separación entre preparación y aprobación es un control organizativo recomendado; la app no garantiza que dos personas distintas intervengan si se asignan todos los permisos a una misma persona.

El rol adicional **Eliminar Mov. del Core** permite intentar la cancelación o eliminación de importaciones contables, depósitos y complementarias procedentes del archivo del core. Debe asignarse expresamente al usuario, incluso a Administrator; no se concede automáticamente a operadores ni supervisores. También se requieren los permisos normales de la operación y cumplir las restricciones de vínculos, efectivo y períodos cerrados. Para corregir la distribución de un depósito, use **Desconciliar** sin eliminar la evidencia contable.

## Preparar empresas clientes y cuentas

Ruta: **Conciliación Credinómina → Preparación**. También puede buscar directamente **Empresa de convenio**, **CN Client** y **Cuenta bancaria**.

1. Cree o revise la empresa. Registre nombre oficial, código, nombre corto, frecuencia mensual o quincenal, días límite de depósito y contacto. El nombre identifica el documento; el código se conserva como referencia.
2. Use el nombre corto para facilitar los identificadores de períodos e importaciones. Si queda vacío se usa el código. Cambiarlo no renombra en masa los registros anteriores.
3. En **Nombres alternativos**, agregue únicamente variantes verificadas que aparecen en planillas y contabilidad. No use un mismo alias para dos empresas para forzar coincidencias.
4. Revise los clientes de la empresa. Registre nombre, Nro. Cliente del core, cédula y, si existe, Nro. Empleado de la empresa. El número de empleado no sustituye el número de cliente. El documento del cliente usa su número de cliente.
5. Agregue alias del cliente cuando la empresa invierta apellidos y nombres o use una variante comprobada. La búsqueda por nombre se limita a las empresas autorizadas; una coincidencia ambigua queda para revisión.
6. Cree las cuentas bancarias con nombre identificable, banco, número y moneda. El documento usa el nombre de cuenta. Elija una cuenta activa al registrar el depósito; su moneda completa la moneda del depósito en borrador.
7. Mantenga la tolerancia automática en cero hasta que el supervisor autorice su uso. Si se habilita, el máximo es US$0.10 y se aplica a la diferencia absoluta, positiva o negativa.

Control posterior: confirme que una variante de nombre devuelve una sola empresa o persona y que los números no pertenecen a otro cliente. Nunca cambie un identificador para ocultar una contradicción entre la cartera y el archivo.

### Cuando una empresa paga por otra

En la ficha de la pagadora, agregue las beneficiarias en **Empresas por las que puede pagar**. Si INDENICSA paga a CBC, configure CBC dentro de INDENICSA y registre un solo depósito con pagadora INDENICSA. Esta autorización no fusiona empresas, no autoriza el pago inverso y no se extiende a terceros indirectos.

Seleccione destinos de las empresas autorizadas e indique la empresa beneficiaria en el detalle cuando sea necesario. Un depósito de US$1,000 distribuido US$700 a INDENICSA y US$300 a CBC reduce sus respectivas aplicaciones; el efectivo se cuenta una sola vez bajo la pagadora. El exceso sin identificar permanece en la pagadora. No quite una autorización utilizada por depósitos confirmados.

## Importar el corte mensual de cartera

Ruta: **Conciliación Credinómina → Preparación → Corte de cartera** o `/app/cn-credit-portfolio-snapshot`.

1. Cree el corte con su mes y adjunte el archivo de cartera del core. Guarde antes de importar.
2. Presione **Importar / actualizar corte** y espere la respuesta. Al terminar revise los créditos cargados, clientes identificados, clientes por revisar y empresas o clientes creados.
3. Revise una muestra de créditos activos y cancelados, números de cliente SIAF, cédulas y EMPRESA_DE_CONVENIO. La cartera guarda sus columnas como campos del detalle para consulta y reportes.
4. Revise las empresas y clientes creados automáticamente cuando EMPRESA_DE_CONVENIO no está vacía ni es N/A. Una empresa nueva se crea con el mismo nombre como código; complete después contacto, plazo, nombre corto y alias.
5. Verifique la normalización del crédito. Un número como 109136 se guarda para el cruce como 109136-1; no se agrega otro sufijo si ya existe. No modifique el archivo contable para forzar un cruce.
6. Si aparece un error o no se actualiza el formulario, recárguelo y verifique estado y filas antes de repetir. Un mensaje de conexión interrumpida no permite concluir que el servidor no guardó nada.

El corte se identifica como CARTERA-mes-año. La cartera sirve para identificar al cliente, validar empresa y consultar situación del crédito; no crea aplicaciones ni depósitos. Un crédito cancelado no elimina automáticamente una aplicación contable pendiente de conciliar.

En una importación contable individual puede elegir **Corte de cartera para validar**. Si no elige uno, cada aplicación busca un corte habilitado de su mismo mes y, si falta, el más reciente de un mes anterior. En **Carga masiva**, el **Corte de cartera** es obligatorio. Revise la antigüedad de esa evidencia al trabajar con meses históricos.

### Guardar actualizar renombrar y desactivar un corte

**Guardar** conserva los cambios permitidos de Archivo, Notas y Es Desactivar de un corte existente. No lee nuevamente el archivo, no reescribe los créditos ni recalcula sus contadores. Después de sustituir un archivo, use **Importar / actualizar corte** para procesarlo y actualizar filas y resumen. No intente corregir créditos editando resultados calculados y guardando.

Use **Renombrar** en el menú del documento si necesita un nombre personalizado. Se conservan los vínculos y las siguientes importaciones respetan ese nombre. Renombrar no cambia la fecha del corte. Puede cargar varios cortes del mismo mes y año con nombres distintos, siempre que solo uno esté activo. Desactive el anterior antes de cargar su reemplazo; el sistema agrega un sufijo al nombre mensual si ya existe.

Marque **Es Desactivar** para retirar un corte de las nuevas selecciones y búsquedas automáticas. En un documento existente, la casilla guarda su cambio automáticamente; espere el aviso. Los cambios pendientes de otros campos siguen sin guardar y debe guardarlos por separado. La casilla puede actualizarse incluso en un documento confirmado; conserva filas y referencias históricas. Desmarcarla vuelve a habilitar el corte si no existe otro activo del mismo mes y año. Desactivar no recalcula los depósitos que ya usaron esa evidencia.

## Cargar movimientos contables

Ruta: **Conciliación Credinómina → Aplicaciones del core → Importación contable** o `/app/cn-accounting-import`.

### Carga de una empresa

1. Cree una importación y seleccione la empresa antes de cargar. Adjunte el archivo con los movimientos correspondientes; para el control de esta operación use la cuenta contable 160209013004. Si recibe todas las cuentas, prepare una extracción de esa cuenta conservando el original y coteje sus totales.
2. Seleccione **Moneda reportada en el archivo**. Para NIO, indique **Tipo de cambio manual C$ por US$** cuando la fila no tenga equivalente documentado en dólares. Separe archivos que mezclen monedas; no suponga USD por el nombre del reporte.
3. Elija el corte de cartera si necesita uno específico. Para histórico, marque **Carga histórica de aplicaciones** cuando corresponda y seleccione un período histórico abierto o asígnelo después por fila.
4. Guarde y presione **3. Cargar movimientos contables**. La carga clasifica líneas: una aplicación, un depósito y un movimiento interno no son el mismo hecho financiero.
5. En **Resultados**, revise nombre, Nro. Cliente, Nro. Crédito, fecha, monto original, monto US$, asiento, recibo, empresa y motivos. Si falta un cliente, la app puede crearlo con la información identificada; revise que su empresa y número provengan de la evidencia correcta.
6. Abra **Ver excepciones**. Filtre por cliente, crédito, asiento, estado, etapa o rango de importes. Las alertas de importación son pendientes detectados y no necesariamente documentos de excepción ya registrados.
7. Use **Crear período** si necesita preparar un período desde esta carga. Revise mes de cobranza y corte; el botón crea un borrador, no cierra ni liquida aplicaciones.
8. Presione **Conciliar esta empresa** después de guardar cambios. Revise la respuesta con filas procesadas, conciliadas y pendientes. Esta acción puede actualizar el conjunto financiero relacionado de la empresa; para trabajar solo un depósito use Conciliar en ese depósito.

Control posterior: compare número de líneas, débitos y créditos originales con la extracción contable. El total de aplicaciones no sustituye el total de todas las líneas contables, porque también hay depósitos y complementarias.

El identificador de la carga utiliza CONTA-nombre corto o código-mes-año-consecutivo. El mes y año se obtienen de las fechas de las filas, no de la fecha de creación del documento. Duplicar una importación no copia el archivo adjunto.

### Carga masiva de varios meses

La acción **Carga masiva** está en la vista de lista, no en el formulario. Prepare primero el corte de cartera y los convenios para reducir los casos sin identificar.

1. Abra la lista de importaciones y pulse **Carga masiva**. Adjunte el archivo, indique moneda y tasa cuando corresponda y seleccione un **Corte de cartera** importado y habilitado. Es obligatorio para analizar, confirmar o reanudar la carga. El límite es 100,000 movimientos por carga.
2. Revise la tabla de empresas identificadas y la tabla separada de **NO IDENTIFICADA**. En estas últimas, consulte la descripción del asiento y cambie la empresa solo si tiene evidencia.
3. Repita el análisis después de corregir empresas. Las elecciones se conservan y se consideran para agrupar; verifique el resultado antes de confirmar la selección.
4. Seleccione las empresas a importar y confirme la carga. Las aplicaciones se agrupan por empresa y fecha de aplicación. Los casos NO IDENTIFICADA se registran por separado, aunque sean del mismo día, para corregir cada caso posteriormente.
5. La carga conserva un CSV individual para cada importación y referencias al archivo masivo original compartido. Ver el mismo original en los adjuntos de varios documentos no significa que exista una copia física por documento. Los CSV de cada grupo sí son archivos distintos. Para reprocesar un grupo use su CSV en **3. Cargar movimientos contables**; no requiere otro botón.
6. Si la carga se interrumpe, revise qué documentos se crearon antes de iniciar otra. Una nueva vista previa distingue lo ya importado. No ejecute cargas concurrentes ni borre bloqueos desde Redis sin comprobar primero el trabajo del servidor.

El CSV individual conserva la empresa resuelta y CN_FILA_ORIGEN. No quite ni cambie esa columna. Líneas contables iguales en filas originales distintas se conservan; se señalan para revisión, no se eliminan por parecer duplicadas. La app protege la evidencia original y puede rechazar un CSV alterado que intente reutilizar la identidad de otra fila.

Las empresas identificadas deben tener créditos en el corte seleccionado, incluso si se corrigieron manualmente en la vista previa. Los casos NO IDENTIFICADA mantienen su flujo de revisión. Una carga antigua preparada sin corte debe analizarse de nuevo seleccionando uno antes de continuar.

### Clasificar las líneas del core

| Evidencia | Tratamiento en la app | Revisión requerida |
| --- | --- | --- |
| TMov 12 y TDoc 16 | ND de aplicación de pago | Cliente, crédito, empresa y monto de la aplicación |
| TMov 02 y TDoc 12 | Depósito | Empresa pagadora, cuenta bancaria, referencia, moneda e importe del dinero recibido |
| Movimiento interno o concepto distinto de aplicación | Partida complementaria para revisión | Clasificar si ajusta una aplicación, integra un depósito o compensa otra partida |

Consulte la clasificación y el motivo guardados en cada línea; no clasifique otros pares TMOV/TDOC por analogía. Los depósitos creados desde contabilidad quedan en borrador. La cuenta bancaria se identifica solo si la evidencia es fiable; puede quedar vacía para revisión. La descripción completa, cuenta, fecha, moneda, débito, crédito, archivo y fila original permanecen disponibles.

Una partida importada puede tener importe positivo en el formulario y ser un crédito contable. Para control mensual y saldo contable, el signo se interpreta desde el débito y crédito originales. No invierta el signo de una partida importada solo para que el reporte cuadre.

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

## Primera conciliación de cobranza y deducción

En un período **Operativo**, la conciliación 1 compara las aplicaciones del core con la **Base de la primera conciliación** elegida: **Cobranza** o **Detalle de empresa**. La conciliación 2 compara las aplicaciones con los depósitos. Pueden realizarse en cualquier orden; el período solo puede cerrarse cuando ambas están completas y no existen excepciones o vínculos pendientes.

1. Revise la empresa y su frecuencia y plazo de depósito.
2. Cree el Período de Conciliación con el primer día del mes de planilla, modalidad Operativa y ciclo correspondiente. Registre Observaciones que permitan identificar el envío.
3. En Plantillas descargue la de cobranza. Prepare el archivo, adjúntelo y presione **1. Cargar cobranza**. La app carga las filas, identifica o crea clientes cuando la evidencia lo permite y normaliza los créditos; cargar la cobranza no registra efectivo.
4. Use **Exportar archivo empresa**. Incluye las columnas requeridas y una `Fila ID` técnica para facilitar la coincidencia exacta. Conserve esa columna cuando la empresa responda.
5. La empresa llena `Deducido C$` o `Deducido US$` y devuelve el mismo archivo.
6. Registrar la fecha de la planilla o constancia que sirve como evidencia de la deduccion.
7. Adjunte el detalle devuelto en el mismo período y presione **2. Cargar deducción de empresa**. Revise importes y motivos de todas las filas que no coinciden.

El archivo de cobranza contiene Nro. Cliente, Nombre y Apellidos del Cliente, Nro. Cédula, Nro. Crédito, Nro. Cuota, Nro. de cuotas totales, Monto de la cuota en US$, Monto de la cuota en C$, Comentarios, Referencia de Aplicación y Comentario de Aplicación. Use la plantilla actual para conservar los encabezados. La respuesta agrega Deducido C$ y Deducido US$; no reemplace la cuota solicitada por lo efectivamente descontado.

En la cobranza puede dejar vacíos u omitir nombre, número de cliente y cédula. Se completan por número de crédito desde la cartera activa más reciente del mes de la fecha de corte del período, o la última anterior si no hay una en ese mes. Un período con fecha 15/09/2026 usa la cartera del 30/09/2026 si está disponible. No se consultan meses posteriores. Si no puede completar el nombre, hay créditos duplicados o la identidad contradice la cartera, la carga indica la fila y el motivo. Los importes y números de cuota se toman del archivo de cobranza.

La cobranza conserva un único identificador: **Cliente** (`client`), que enlaza al registro del cliente. No guarda un segundo número de cliente; las plantillas y reportes lo obtienen de ese vínculo. Todas las filas deben quedar identificadas antes de guardar. Al actualizar el sistema, la migración conserva las filas, sus importes y vínculos, y deja los números originales en la actividad del período. Si encuentra identidades contradictorias, informa el período y la fila para corregirlos antes de continuar.

Resultados posibles:

- **Deducción total:** la deducción coincide con lo solicitado; no crea ni liquida por sí sola CxC.
- **Deducción parcial:** se documenta la diferencia de la primera conciliación; no se convierte automáticamente en deuda del trabajador.
- **No deducido:** se documenta el motivo y la gestión necesaria. La CxC de aplicaciones existentes permanece hasta recibir cobertura vinculada, independientemente de esta clasificación.
- **Deduccion en exceso:** no se aplica automaticamente; queda como excepcion.
- **Pendiente de detalle:** la empresa aun no envio evidencia. No equivale a pago ni a falta de pago definitiva.
- **Ambigua o sin coincidencia:** revision humana obligatoria; nunca se asigna por similitud de nombre.

### Ejecutar las conciliaciones en cualquier orden

Para realizar primero la conciliación 1, cargue la base elegida y las aplicaciones del core. En el período pulse **Conciliación 1: validar aplicaciones**. Se actualiza el control contra esa base conservando la distribución de los depósitos existentes. Puede obtener un control Conforme aunque no haya recibido ningún depósito; el cierre permanece bloqueado mientras falte la conciliación 2.

Para realizar primero la conciliación 2, incluso sin cobranza ni detalle de empresa:

1. Cree el período operativo de la empresa y mes correspondientes. Seleccione la base que utilizará posteriormente en la conciliación 1.
2. En **CN Accounting Import**, seleccione ese período en **Período predeterminado de aplicaciones**, o en **Período asignado de aplicación** de cada fila si la carga mezcla períodos. La modalidad debe coincidir con el período; no marque la carga como histórica para evitar la primera conciliación.
3. Guarde y use **Conciliar esta empresa** para registrar los vínculos de las aplicaciones. La asignación del período no acredita que la conciliación 1 esté completa.
4. En el depósito seleccione el período, cargue su detalle o use **Usar aplicaciones pendientes como detalle**, revise identidades e importes, confirme el depósito y pulse **Conciliar**. También puede elegir la **Aplicación del core** desde el selector de partidas pendientes. No se requiere haber cargado la base de la conciliación 1.
5. Revise el saldo de la aplicación y del depósito. Cuando llegue la cobranza o el detalle de empresa, cárguelo en ese mismo período y ejecute **Conciliación 1: validar aplicaciones**. El sistema conserva el pago directo de la aplicación y lo refleja en la fila identificada sin volver a distribuir ese dinero.

Si cambia la distribución bancaria, use **Conciliar** en el depósito correspondiente. Actualizar la base de la conciliación 1 no redistribuye depósitos automáticamente. Los destinos ya pagados siguen protegidos; no cambie el período de una aplicación con dinero vinculado sin corregir primero esa distribución mediante las acciones autorizadas.

Ejemplo: una aplicación de US$100 y un depósito de US$100 pueden completar la conciliación 2 antes de recibir cobranza. El período queda abierto hasta cargar la cobranza y completar los vínculos de las aplicaciones. Si la cobranza indica US$100.01 y los US$100 aplicados están totalmente vinculados y depositados, la diferencia de US$0.01 permanece como información y no impide el cierre. No es necesario aprobar ni materializar un ajuste provisional para eliminarla. Una aplicación adicional sin vincular o sin pagar sí impide el cierre.

## Implementación histórica y corte de septiembre de 2026

De **abril de 2025 a agosto de 2026**, usar períodos de modalidad **Histórica**. No se importa la cobranza ni se ejecuta la conciliación de deducciones. Se enlazan aplicaciones reales del core en US$ con depósitos registrados en Distribución de Depósito (en US$ o C$ con tasa documentada). Cada aplicación histórica tiene empresa/mes y corte asignados explícitamente: mensual, fecha exacta de aplicación o rango inclusivo de fechas. Las fechas de aplicación pueden ser el 15, el 30 o cualquier otro día, incluso de un mes posterior al de cobranza. El depósito puede cubrir varias aplicaciones, una parte de una aplicación o combinarse con otros depósitos. Los repartos ambiguos requieren Distribución de Depósito confirmada.

El saldo de aplicaciones sin depósito o compensación vinculada constituye la CxC de esta herramienta, con la misma regla que en operativo. No prueba por sí solo que el trabajador no haya pagado ni registra un pago nuevo en el core. Una aplicación de agosto depositada en septiembre permanece en agosto por su período asignado. Desde **septiembre de 2026**, los períodos nuevos son **Operativos** y usan ambas conciliaciones descritas abajo. Véase la secuencia de carga en `docs/instalacion_y_uso.md`.

### Crear y enlazar un período histórico

1. Cree un período para la empresa y mes de cobranza que está reconstruyendo, no para el mes en que llegó el depósito. Seleccione modalidad **Histórica**.
2. Elija el alcance **Mensual**, **Fecha exacta** o **Rango de fechas**. En fecha exacta registre el día real de aplicación; en rango registre ambos extremos inclusivos. Puede usar fechas diferentes al 15 o 30.
3. Para separar aplicaciones de abril del 8, 23, 28 y 30, cree cuatro cortes por fecha exacta si esa es su evidencia. Para un archivo agregado de toda la empresa y mes puede usar alcance mensual. No cree un alcance mensual y cortes superpuestos para el mismo conjunto.
4. Guarde y revise su identificador y Observaciones. El consecutivo distingue registros permitidos, pero no habilita duplicar una empresa y corte ya existentes ni mezclar modalidades para el mismo mes.
5. En la importación contable, seleccione **Período predeterminado de aplicaciones** para un archivo uniforme. Si mezcla cortes, asigne el período a las filas correspondientes. Las aplicaciones sin período quedan visibles para completar el vínculo.
6. Use **Conciliar esta empresa** y revise en el período sus aplicaciones y Pendientes detectados. No cargue una cobranza ficticia para llenar collection_rows: en histórico puede estar vacía mientras las aplicaciones enlazadas son la base del saldo.
7. Registre o revise los depósitos de la empresa, seleccione esos períodos y cargue o genere el detalle. Concilie cada depósito y verifique cobertura y pendientes por aplicación.

No use **Carga histórica de aplicaciones** como solución general para una fila que no coincide. Esa marca define el tratamiento cuando no hay período asignado; no identifica empresa ni crea cobertura. Si un depósito paga varios cortes, se registra una sola vez y se distribuye entre ellos.

## Cobranza mensual y quincenal

La **Frecuencia de cobranza** se configura por empresa. Una empresa mensual tiene un solo período operativo por mes; una empresa quincenal tiene dos: primera quincena (cierre día 15) y segunda quincena (cierre el último día del mes). Cada período conserva su archivo de cobranza, respuesta de la empresa, deducción, aplicaciones, depósitos y excepciones. La misma cuota puede aparecer en dos envíos, pero el período y la `Fila ID` distinguen los registros; nunca se suman dos quincenas como si fueran un solo descuento.

La carga **histórica** sigue agrupada por empresa y mes de cobranza asignado, pero puede separarse además por fecha exacta o rango de aplicación. No se presume que un corte sea una primera o segunda quincena: sus fechas se registran según la evidencia real.

Normalmente el core registra una aplicación por quincena. Si registra **una sola aplicación en US$ para ambas**, la conciliación la reparte automáticamente entre Q1 y Q2 únicamente cuando corresponden al mismo crédito, empresa y mes, pasan los controles de cliente, cuota y referencia, y el importe es exactamente la suma de los saldos disponibles de ambas. El reparto queda visible en **Distribución de la aplicación (JSON)** y alimenta los saldos y las referencias de depósito de cada quincena. Si hay varias combinaciones posibles o la aplicación es parcial, queda como excepción para revisión; el sistema no adivina el reparto.

El tablero agrega importes para la vista del mes y permite abrir cada quincena. En el estado de cuenta y los reportes se indica el ciclo para explicar cuándo se envió y dedujo cada importe. La fecha límite mensual se calcula con los días pactados para el mes siguiente. Para empresas quincenales se registra la fecha límite acordada en cada período, sin asumir que la primera y la segunda quincena se pagan juntas o el mes siguiente.

### Reconocer la cobranza como detalle de la empresa

Si la empresa confirmó la deducción completa, use **Más opciones → Reconocer cobranza como detalle de la empresa** en un período abierto. Indique la fecha de evidencia y confirme la deducción. La app copia los importes de cobranza a `Deducido US$` y `Deducido C$`, conservando quién lo reconoció y cuándo. No sobrescribe un detalle o deducciones ya registrados.

Esta acción no registra ni confirma un depósito y no sustituye la evidencia individual de descuento salarial. No reconozca una deducción solo porque llegó dinero. En el depósito, **Usar aplicaciones pendientes como detalle** permite preparar filas con las aplicaciones seleccionadas de sus períodos; revise los destinatarios e importes antes de conciliar. Un detalle generado por la herramienta no debe presentarse como archivo enviado por la empresa.

## Subsidio suspensión e ingreso insuficiente

- Registrar el monto efectivamente deducido, incluso si es cero.
- Usar la excepcion para documentar `subsidio`, `suspension`, `ingreso insuficiente` u otra causa.
- Mantener la diferencia como pendiente de aclaración de cobranza; no sumarla a la CxC ni presentarla automáticamente como deuda del trabajador.
- Determinar cualquier responsabilidad contractual fuera de este cálculo de conciliación, con el convenio y la evidencia correspondiente.
- No capitalizar, condonar ni reprogramar automaticamente desde esta aplicacion; esas decisiones pertenecen al core y a las politicas de credito.

## Cuando el detalle llega tarde

- El detalle permanece pendiente de recibir; revise la alerta correspondiente y documente la gestión. No confunda este faltante de evidencia con el estado financiero del período.
- No se simula una deduccion para cuadrar el deposito.
- Cuando llegue la evidencia, se importa al periodo original aunque el archivo llegue a finales de mayo.
- Si el deposito llega antes que el detalle, puede quedar registrado y confirmado sin asignar todavía a una cobranza. Cuando llegue evidencia posterior, se recalcula la conciliación; una aplicación enlazada antes de comprobar la deducción se muestra como **provisional**, no como descuento salarial confirmado.
- Para dejar constancia del cierre de control mensual con pendientes, use **Registrar corte de control** en el período y anote la siguiente gestión. La foto fechada no bloquea evidencia tardía ni equivale a liquidación. `Cerrar período` exige aplicaciones vinculadas y liquidadas, sin excepciones abiertas ni pendientes de depósitos. Las diferencias informativas de la primera conciliación se conservan al cerrar; no son por sí mismas deuda del empleado.
- Un período `Cerrado` queda en solo lectura. Si se descubre una corrección necesaria, un supervisor o administrador debe usar **Reabrir período** y documentar el motivo; después revisa nuevamente los saldos y ejecuta el cierre otra vez.

## Segunda conciliación de aplicaciones y depósitos

1. Importar solo **Movimientos contables** para las aplicaciones. Antes de cargar, indicar la moneda reportada; si el archivo está en NIO, ingresar la tasa manual C$/US$. La conciliación se realiza en US$ y el monto original en C$ queda conservado. Cada importación debe tener una sola moneda; separar archivos mixtos. Las `DISPENSAS` no se tratan como efectivo.
2. Registrar cada depósito de convenio en **Distribución de Depósito**, con su fecha real, empresa, referencia, importe y moneda. Un supervisor lo **confirma** para que participe en la conciliación; el borrador no tiene ese efecto. **Confirmar depósito** y **Conciliar** son acciones separadas. No cargar el Excel bancario mensual como archivo de **Movimientos contables**: puede mezclar depósitos de otras empresas, clientes sin convenio y movimientos operativos. La importación contable sí puede crear depósitos desde líneas identificadas como tales; revise esos borradores antes de confirmarlos. Cuando la empresa entregue el detalle por cliente, adjuntarlo a ese mismo depósito e importarlo allí. Si el depósito es en C$, indicar una tasa C$/US$ positiva; la app calcula y redondea el equivalente a dos decimales. No exige una justificación de tasa.
3. La aplicación del core se conserva en US$ y se enlaza a una deducción por crédito, importe y referencia cuando la empresa la informó. Si la deducción fue en C$, se usa únicamente una tasa documentada para comparar en US$.
4. El depósito registrado puede cotejarse con un movimiento contable de depósito por referencia e importe en su moneda original o equivalente documentado.
5. El depósito se distribuye en US$ entre aplicaciones y partidas complementarias. Seleccione los períodos que puede cubrir y use **Seleccionar partidas pendientes** para indicar destinos e importes. Cuando la selección manual corresponde a una fila del detalle, vincule esa fila con sus destinos: cuadrar el total del depósito no demuestra por sí solo que cada cliente esté conciliado. El botón **Conciliar** recalcula ese depósito y su conjunto financiero afectado, no toda la empresa indiscriminadamente.

Un depósito puede cubrir parte de una cobranza, varias cobranzas o combinarse con otros depósitos para cubrir una misma cobranza. Cada depósito conserva su importe distribuido, su saldo sin distribuir y el detalle de destinos; cada fila de cobranza muestra los depósitos que la financiaron, lo remitido y lo pendiente de la empresa. Una aplicación parcial del core tampoco se confunde con el pago completo de la cuota.

### Traslado de excepciones entre conciliaciones

El operador puede escribir la **Excepción de cobranza vs aplicación** en la cuota tan pronto se detecte una aplicación parcial, sin esperar el depósito. Una aplicación del core puede enlazarse a la cobranza antes de que llegue el detalle de deducción, siempre que crédito, cuota y demás identificadores dejen un único destino; esto no confirma que la empresa haya descontado ni habilita distribuir un depósito sin evidencia. También se conserva el comentario del archivo de la empresa y la descripción/resolución de la excepción de deducción. Al recibir y distribuir el depósito, el sistema compara por la misma fila de cobranza los faltantes en **US$**: cobranza menos aplicación (descontando partidas complementarias atribuibles a esa fila), cobranza menos deducción y cobranza menos importe remitido. Si el faltante del depósito coincide con alguno de los faltantes ya comentados, muestra ese comentario como **antecedente trasladado** en la cuota y en el reparto del depósito, con la referencia de la excepción cuando exista. No crea otra excepción por el mismo comentario.

Si todavía no existe depósito, no hay comentario trasladado. Si el faltante del pago es distinto, permanece para revisión por separado. Trasladar el antecedente no registra dinero, no resuelve la excepción original, no convierte un depósito parcial en completo y no altera el saldo pendiente. Editar posteriormente el comentario o la resolución recalcula el antecedente mostrado.

La asignación automática de un depósito registrado requiere su detalle por cliente, importado o generado y revisado desde las aplicaciones pendientes. Una referencia única sin ese detalle no basta; alternativamente, el supervisor puede documentar una distribución manual por depósito y destino, indicando referencia bancaria, comprobante contable si hace falta distinguir depósitos, período, `Fila ID` e importe en US$. También puede destinar una distribución a una partida complementaria. El sistema rechaza importes que excedan los saldos disponibles. No prorratea; la distribución por antigüedad (FIFO) solo se utiliza si el operador la activa expresamente y selecciona los períodos autorizados.

Cuando la tasa de la planilla difiere de la tasa del depósito, la diferencia en US$ se muestra por separado y requiere revisión de cada caso antes del cierre.

### Registrar y distribuir un depósito paso a paso

Ruta: **Conciliación Credinómina → Depósitos → Distribución de Depósito** o `/app/cn-remittance-allocation`. El nombre automático es DEP-mes-año-consecutivo, según la fecha del depósito.

1. En **1. Depósito**, seleccione la empresa pagadora y la cuenta bancaria conocida. Registre referencia, fecha real, importe en su moneda original y, si hace falta distinguir referencias repetidas, comprobante contable. Adjunte el soporte disponible.
2. Revise la moneda que completó la cuenta. Si el depósito está en NIO, ingrese tasa positiva C$/US$. **Equivalente US$** es calculado y de solo lectura. Por ejemplo, C$3,662.43 a 36.6243 equivalen a US$100.00. Los importes se redondean a dos decimales mediante Decimal; conserve la precisión de la tasa.
3. Guarde. El supervisor usa **Confirmar depósito** cuando el dinero y la evidencia son correctos. Esta confirmación no ejecuta la conciliación ni crea pagos en el core.
4. En **2. Detalle**, agregue en **Períodos del detalle** los períodos que espera cubrir. Puede seleccionar varios meses o cortes de la misma empresa o de sus beneficiarias autorizadas. Esta tabla restringe la búsqueda automática; no asigna dinero.
5. Descargue la plantilla y cargue **Detalle de pago por cliente**. Pulse **Cargar detalle del depósito**. Adjuntar el Excel sin pulsar el botón no importa sus filas.
6. Revise cada fila: identidad, crédito, Estado del crédito, Fecha de corte de cartera, Deducido C$ o Deducido US$, Importe US$, Vinculado, saldos a favor y Pendiente. El importe convertido se calcula desde la carga; no espere a Conciliar para detectar una tasa o moneda errónea.
7. Use **Seleccionar crédito del cliente** si debe vincular un crédito concreto. La selección se limita a la cartera del cliente identificado. No seleccione otro préstamo para completar artificialmente la suma.
8. En **Conciliación → Conciliar**, ejecute el cruce y revise el mensaje final y los motivos por fila. Compruebe en **3. Destinos** la distribución completa y el dinero sin asignar ni justificar.

La plantilla del depósito admite identidad por al menos uno de estos datos: Nro. Crédito, Nro. Cliente, Nro. Cédula, Nro. Empleado o Nombre y Apellidos. No exige nombre si existe otro identificador resoluble. Si informa varios, todos deben identificar a la misma persona. El número de crédito sin sufijo se normaliza con -1 cuando corresponde. La plantilla usa Deducido C$ y Deducido US$ y no requiere Nro. Cuota, Referencia de Aplicación, Comentario de Aplicación ni importes esperados de cobranza.

El nombre del documento se agrega al archivo de plantilla para identificarlo fácilmente. No confunda esta plantilla con la de cobranza o la del detalle de deducción de empresa.

Al cargar el archivo de detalle devuelto por la empresa ya no se agregan ceros iniciales. Se conservan los dígitos recibidos y solo se incorpora el sufijo -1 cuando corresponde. La comparación interna ignora los ceros iniciales de los créditos en cartera, movimientos contables, cobranza y depósitos: 1807-1 y 001807-1 coinciden, pero 1807-2 es distinto. Los registros anteriores con ceros siguen siendo compatibles sin cambiar sus números guardados. Si hay varias filas de cartera equivalentes, deben revisarse como duplicadas antes de continuar.

### Consultar el estado del crédito en la cartera

Al cargar o generar el detalle se completan **Estado del crédito** y **Fecha de corte de cartera**, de solo lectura. La búsqueda parte del período seleccionado con la fecha de aplicación o cierre más reciente y del corte utilizado por sus importaciones contables; si estas usaron varios cortes, toma el más reciente. Cuando la selección de cartera fue automática, considera el corte registrado en las aplicaciones.

El crédito se busca por número y empresa en ese corte habilitado. Si no aparece, se consulta únicamente el corte importado habilitado inmediatamente anterior, aunque no esté seleccionado. No se continúa buscando en todos los cortes antiguos. Sin coincidencia muestra **No Identificado** y deja la fecha vacía; una identidad ambigua requiere revisión. Sin períodos seleccionados no se presupone un corte global.

La fila conserva el vínculo al corte realmente utilizado. El dato se actualiza al cargar el detalle o cambiar crédito o períodos; guardar una fila sin esos cambios no garantiza una nueva consulta. Un estado **Cancelado** describe la cartera consultada: no cancela la fila, no asigna efectivo y no demuestra por sí solo que deba crearse un saldo a favor.

### Generar detalle desde aplicaciones pendientes

Si cuenta con evidencia suficiente para identificar los destinatarios y no tiene un archivo de detalle, seleccione los períodos y use **Usar aplicaciones pendientes como detalle**. Se presenta una vista previa con todas las aplicaciones seleccionadas por defecto. Desmarque las que no pertenecen a ese depósito y revise importes antes de generar.

El **Importe del detalle US$** es editable en la vista previa y propone el pendiente de cubrir; el **Pendiente original** queda visible como referencia. Ajuste el importe cuando la evidencia del depósito sea distinta, usando un valor positivo con hasta dos decimales. El cambio queda documentado en los comentarios del detalle y no modifica la aplicación original. El total seleccionado y la advertencia de diferencia se actualizan al editar; un importe mayor que el pendiente no habilita sobrepagar la aplicación.

Si ya hay detalle, la acción exige confirmar su reemplazo. Conserva archivos y destinos, pero retira los vínculos a las filas sustituidas; revise la nueva vinculación antes de conciliar. Las filas respaldadas por saldos a favor confirmados están protegidas contra reemplazos incompatibles. La app conserva el origen como aplicaciones pendientes del período; esto no es una constancia de planilla enviada por la empresa y no concilia por sí solo.

### Seleccionar destinos manuales y vincular el detalle

Use selección manual cuando existan varias aplicaciones posibles, pago parcial, complementarias o una distribución que la automatización no pueda demostrar.

1. En **3. Destinos**, pulse **Seleccionar partidas pendientes**. Si **Usar períodos del detalle** está marcado, la lista se limita desde su apertura a esos períodos. Desmárquelo solo para ampliar la búsqueda conscientemente.
2. Filtre cliente, crédito, referencia, período, tipo de partida y empresa beneficiaria. Compare **Aplicado US$**, **Asignado US$** y **Pendiente US$** antes de elegir.
3. Seleccione aplicaciones o partidas complementarias e indique el importe que cubrirá cada una. No puede usar más de su pendiente ni más efectivo del depósito. Puede minimizar el modal para consultar el formulario y regresar.
4. Agregue destinos y revise **Destinos del depósito**. Guardarlos representa instrucciones; el dinero no se aplica hasta Conciliar.
5. Pulse **Vincular detalle y destinos**. Seleccione la fila del cliente y sus destinos e importes. Si una fila paga dos aplicaciones, vincule ambas. Si un destino cubre varias filas, distribuya sus importes sin repetir el dinero.
6. Guarde y use **Conciliar**. La app valida la identidad y los importes vinculados. Una selección que solo cuadra el depósito, pero no explica la fila, puede dejar **Revisar detalle**.
7. Confirme que la fila muestre cero pendiente y un resumen legible de aplicaciones, créditos, períodos, fechas, asiento y recibo. El crédito se completa automáticamente si los destinos permiten identificarlo de forma única.

Para trabajar con una persona concreta, abra su fila en **2. Detalle** y pulse **Seleccionar partidas pendientes**. Está disponible en depósitos confirmados con pendiente en la fila. El modal limita la selección al cliente de esa línea y, al agregar los destinos, los vincula automáticamente con ella. Revise crédito, períodos e importes; después guarde y pulse **Conciliar**. Esta vía evita repetir la vinculación manual de los pasos anteriores, pero no aplica dinero por el solo hecho de seleccionar.

No repita en Destinos una asignación que ya quedó conciliada automáticamente. La distribución completa incluye también saldos a favor, aunque estos no se agreguen como pagos a créditos en la tabla targets.

Si necesita vincular explícitamente una fila a aplicaciones de otros períodos abiertos, active **Permitir vínculos manuales con períodos distintos a los seleccionados** en **Asignación manual (opcional)**. Guarde y concilie. El selector inicia entonces con **Usar períodos del detalle** desmarcado. Se conservan los controles de empresa, cliente, crédito, importe y disponibilidad; la búsqueda automática y FIFO siguen limitados a los períodos seleccionados. Ampliar solamente el filtro de búsqueda no sustituye esta autorización del vínculo.

### Conciliar varias aplicaciones de una persona

Si una fila de Juan indica US$110.51 y existen dos aplicaciones pendientes del mismo cliente y crédito de US$50.25 y US$60.26, seleccione sus períodos y cargue el detalle. La suma exacta permite un cruce automático cuando el conjunto es único y pasa los controles de identidad. Revise que ambas aplicaciones aparezcan en la distribución.

Sin FIFO, si existen otras combinaciones posibles que también suman US$110.51, el sistema requiere selección manual. Si el depósito solo incluye US$70.00, documente qué parte cubre de cada aplicación o active FIFO cuando esa sea la instrucción de distribución. Dos depósitos de US$40.00 y US$60.00 pueden cubrir una aplicación de US$100.00 mediante sus respectivas distribuciones.

### Distribuir por antigüedad con FIFO

1. En **Períodos a conciliar**, agregue los períodos autorizados en **Períodos del detalle**. Con FIFO esta selección es obligatoria y limita el universo de aplicaciones.
2. Active **Aplicar por antigüedad (FIFO)**. Revise previamente las identidades y créditos del detalle; no se reparte entre personas ni préstamos distintos para completar un monto.
3. Pulse **Conciliar**. Cada fila se distribuye entre las aplicaciones pendientes del mismo cliente y crédito, desde la fecha de aplicación más antigua hasta la más reciente. Se respetan los destinos manuales y los importes ya cubiertos o reservados.
4. Revise el resumen de cada fila y los destinos generados. Con aplicaciones pendientes de US$50.25 y US$60.26, un pago de US$70.00 cubre US$50.25 de la primera y US$19.75 de la segunda; quedan US$40.51 pendientes en esta última.
5. Si el cliente aparece varias veces en el archivo, conserve sus filas cuando sean pagos reales distintos. El motor consume el pendiente restante a medida que distribuye cada línea, sin volver a utilizar lo ya asignado. No elimine filas únicamente por repetir nombre y crédito.

FIFO admite una distribución parcial cuando el importe de la fila supera las aplicaciones elegibles. Por ejemplo, con detalle de **US$13.05** y aplicación disponible de **US$9.62**, distribuye **US$9.62** y conserva **US$3.43** pendientes en la fila, que queda para revisión. Volver a conciliar no duplica el pago.

FIFO no corrige identidades contradictorias, no inventa una aplicación faltante y no crea saldos a favor ni ajustes de diferencia. Para el resto pendiente, identifique otros destinos o documente el excedente por la vía correspondiente. Revise también los centavos resultantes del redondeo por fila; la tasa mantiene su precisión.

### Corregir una distribución equivocada

Conserve primero el detalle de la distribución actual y consulte **Conciliación → Historial de conciliación**. Revise qué períodos y partidas quedarían afectados y obtenga la autorización correspondiente.

Para corregir, modifique o retire los destinos equivocados y los vínculos del detalle, guarde y vuelva a Conciliar. Al verificar o cambiar una distribución existente se solicita un motivo y se conserva su antes y después. Si el detalle sigue provocando el mismo cruce automático, corríjalo o restrinja sus períodos; borrar una instrucción manual no garantiza que la automatización deje de seleccionar la misma aplicación.

Para retirar la conciliación vigente, use **Conciliación → Desconciliar**. Lea la advertencia, indique el motivo y confirme la acción. El depósito permanece confirmado y conserva su origen contable; revise los destinos antes de conciliar otra vez. Las restricciones por períodos cerrados, reservas de saldos a favor y otros vínculos siguen vigentes. Desconciliar no requiere el rol de eliminación del core porque no cancela ni elimina el documento.

No edite los importes calculados ni cancele una aplicación para liberar efectivo. Si un período está cerrado, reábralo con motivo antes de alterar su distribución. Si existe saldo a favor confirmado, no puede usar esa reserva otra vez: corrija primero su clasificación por la vía permitida. Una partida con gestiones realizadas conserva su historial y no se cancela para borrarlo.

Control posterior: la aplicación retirada debe volver a mostrar su pendiente, el depósito debe mostrar su nueva distribución y su saldo disponible, y el historial debe explicar la corrección. Si no puede retirar el efecto por sus vínculos o estados, detenga la redistribución y solicite revisión del supervisor.

### Corregir datos de un depósito confirmado

Use **Corregir datos** para rectificar empresa pagadora, fecha del depósito o referencia en un depósito confirmado, si cuenta con permiso de escritura. Indique el motivo obligatorio y pulse **Guardar corrección**. La acción conserva los cambios y su motivo, verifica que el documento no haya cambiado en otra sesión y mantiene las validaciones de vínculos y períodos cerrados. No permite alterar libremente importes protegidos ni evita las restricciones de saldos a favor.

Después de guardar, revise detalle, períodos y destinos y pulse **Conciliar**. La corrección de cabecera no demuestra que la nueva distribución sea válida. Si una validación bloquea el cambio, resuelva primero los vínculos o solicite la reapertura autorizada; no fuerce la edición en la base de datos.

### Ajustes menores por tolerancia

El supervisor puede definir por empresa una tolerancia de **0 a US$0.10**, inicialmente cero. Tras la distribución de un depósito en US$, si el depósito registrado y una única aplicación del core se enlazan sin ambigüedad, una diferencia absoluta no mayor que la tolerancia crea una **Partida Complementaria**, categoría **Diferencia por tolerancia**. La diferencia firmada es `depósito − aplicación`: US$46.53 depositados contra US$46.52 aplicados producen **+US$0.01**; US$46.52 depositados contra US$46.53 aplicados producen **−US$0.01**. Ambos se muestran sin cambiar el importe aplicado al préstamo ni el dinero recibido.

La partida automática es interna, de solo lectura y no se exporta al core. No la agregue manualmente a Destinos porque su efecto ya está incluido. Si necesita registrarla contablemente, use **Crear / Ver excepción** para dar seguimiento y verificar el asiento importado. El signo positivo puede clasificar únicamente el efectivo sobrante correspondiente; el negativo no representa un depósito ficticio. La tolerancia no resuelve diferencias cambiarias, varias asignaciones posibles, pagos parciales ni partidas administrativas. Si el origen cambia, la partida se revierte de forma trazable; no se recalcula un período cerrado para modificarlo.

## Depósitos mayores que la cobranza

El depósito se registra en el depósito por su importe total, aunque supere las cobranzas informadas. Solo la porción identificada se distribuye a las cobranzas o partidas complementarias. El excedente nunca se aplica automáticamente a un crédito.

- Si la empresa pagó de más por error o remitió una partida no informada, use **Crear saldo a favor de la empresa**. Si el exceso está incluido en una fila del detalle, use el botón de esa fila para vincularlo y resolver su pendiente. Se crea una **Partida Complementaria**, con importe, motivo, responsable y fecha compromiso. Si el exceso pertenece a una persona, use **Crear saldo a favor del cliente** desde su fila. Una aplicación sin identificar no es automáticamente un saldo a favor.
- El sistema valida que el excedente documentado no supere el saldo sin distribuir del depósito. Esa porción se muestra como **saldo a favor documentado de la empresa**, separado de las cobranzas. No se considera conciliada con un crédito.
- Lo que no tenga justificación queda como **sin distribuir ni justificar** y continúa siendo excepción. Una partida que posteriormente se identifique se debe conciliar mediante la distribución o partida complementaria correspondiente, corrigiendo/cancelando antes la clasificación de excedente para evitar doble uso.
- Esta app no crea automáticamente el pasivo, devolución ni compensación contable en el core; esos movimientos requieren el procedimiento contable autorizado de la IMF.

## Página de control

**Control de Credinómina** separa **Calendario** y **Trabajo de conciliación** en pestañas. La bandeja reúne detalle de empresa, aplicaciones sin período, depósitos por revisar, complementarias, saldos a favor por gestionar y excepciones aunque no tengan período. Siempre consulta **todos los años**, independientemente del año elegido para el calendario, cifras de control y Excel. Filtre por empresa, tipo de pendiente, responsable o compromiso; el filtro considera todas las páginas. La bandeja se carga al abrir su pestaña; si falla, muestra un aviso y permite reintentar, sin presentar un conjunto parcial como completo. La celda mensual muestra por separado los depósitos completos recibidos ese mes, aunque paguen períodos anteriores. El efectivo no debe sumarse otra vez a sus asignaciones.

En el formulario del depósito, **depositado = asignado + saldo a favor documentado + sin asignar ni justificar**. Un depósito completamente distribuido puede mostrar **Conciliado con saldo a favor** y, simultáneamente, una gestión de devolución o aplicación futura pendiente. El saldo a favor original se conserva aunque su gestión se complete; no se vuelve a liberar ese efectivo para pagar otra aplicación. El estado de conciliación no certifica por sí mismo el registro contable en el core. Si el importe no cuadra o el detalle sigue por revisar, no se muestra en verde aunque el resultado guardado diga «Conciliado».

La celda mensual por empresa suma los períodos disponibles y permite abrir cada quincena por separado; no muestra una quincena inexistente como si ya estuviera conciliada.

## Partidas complementarias con asiento separado

Si la empresa deposita US$100, el core aplica US$90 al crédito y los US$10 restantes son una cobranza administrativa asentada en otro comprobante:

1. Crear una **Partida Complementaria** con referencia, concepto, fecha, importe y justificación. El asiento puede completarse después; si falta, dé seguimiento con **Crear / Ver excepción → Registrar ajuste en el core** y verifique el registro contra la importación contable. Si el importe está en C$, indique la tasa C$/US$.
2. El supervisor revisa y confirma la partida. Solo las partidas confirmadas participan en la conciliación. No se genera ni se modifica el asiento contable desde esta app.
3. Si esos US$10 corresponden a una cuota concreta, indicar crédito y, de ser necesario, empresa, período, cliente y número de cuota. Solo se atribuyen a la fila de cobranza cuando el enlace es único. Sin ese enlace, cuadran el depósito pero no reducen un saldo individual.
4. Al recalcular, la segunda conciliación puede asignar **US$90 a la cobranza del crédito + US$10 a la partida complementaria = US$100 depositados**. El estado de cuenta mantiene visibles los US$90 de aplicación y los US$10 administrativos por separado; no registra ficticiamente US$100 como pago del préstamo. Si el reparto no es único, debe documentarse con Distribuciones de Depósito.

La referencia sola no prueba que una partida pertenezca a un cliente. Conserve la justificación y documente el comprobante cuando exista. La cabecera de la partida separa importe utilizado, pendiente financiero y situación contable: conciliar no equivale a contabilizar.

### Elegir el tratamiento de una complementaria

Ruta: **Conciliación Credinómina → Diferencias y seguimiento → Partida Complementaria** o `/app/cn-complementary-item`.

| Situación | Tratamiento | Efecto que debe revisar |
| --- | --- | --- |
| Depósito incluye cobranza administrativa o ingreso separado | Partida de depósito con concepto correcto | Explica efectivo fuera del pago al crédito |
| NC o reversión del core reduce una aplicación anterior | Ajuste de aplicación vinculado | Reduce aplicado neto, sin crear un depósito |
| Débito y crédito internos se cancelan entre sí | Compensación entre partidas | Consume sus pendientes, sin pago ficticio |
| Dinero recibido excede lo aplicado y pertenece al cliente | Saldo a favor del cliente | Reserva efectivo y mantiene seguimiento del beneficiario |
| Exceso de la pagadora que no corresponde a un cliente | Saldo a favor de la empresa | Reserva efectivo de la empresa y mantiene su gestión |
| Faltante se traslada a deuda de la empresa | Ajuste de conciliación con subcategoría CxC a la empresa | Explica el depósito, pero conserva CxC hasta cobrarla o compensarla |
| Diferencia menor automática y única | Diferencia por tolerancia | Movimiento interno trazable, de solo lectura |

Para crear una partida desde un depósito, pulse **Crear partida complementaria**. Seleccione concepto, importe y signo, fecha, justificación y referencias. Si es **Ajuste de conciliación**, la subcategoría es obligatoria. Guarde y confirme mediante la acción autorizada del modal, revise que la partida esté disponible y agréguela al destino adecuado. Una partida sin período puede utilizarse cuando su vínculo manual y evidencia explican el depósito; no invente un período para habilitarla.

El concepto **Cuenta por Cobrar a la Empresa** prepara un ajuste con tratamiento CxC. Ingrese el faltante con signo negativo: aplicado US$100 y depositado US$90 requieren −US$10. Revise su distribución y seguimiento de cobro. Este concepto reconoce un faltante; **Saldo a favor de la empresa** reserva un excedente y usa importe positivo.

La convención del depósito es: exceso US$10 sobre una aplicación US$90, partida **+US$10**; falta US$10 para cubrir una aplicación US$100, partida **−US$10**. En el segundo caso debe decidir qué significa el faltante. Elegir CxC conserva una deuda de US$10; elegir Otro ajuste sin CxC necesita un motivo respaldado. No use esa segunda subcategoría para condonar una CxC ya reconocida.

Las subcategorías iniciales son **CxC a la empresa**, **Por clasificar** y **Otro ajuste sin CxC**. Por clasificar mantiene el caso visible para decidir; no acredita un cobro. Una subcategoría utilizada no puede cambiar de tratamiento retroactivamente; cree otra si la política cambia.

Los ajustes provisionales permiten cualquier subcategoría existente, incluida **Por clasificar**. Aprobarlos registra la revisión sin crear partidas ni aplicar dinero. Se conserva la regla de importe negativo cuando el tratamiento elegido es **CxC a la empresa**.

El campo **Identificador de la partida en el asiento** es un dato corto que distingue líneas de un mismo asiento, no un espacio para escribir varias líneas de detalle. Use **Descripción** para el concepto completo. El asiento contable es opcional al preparar el ajuste, pero el registro pendiente debe tener seguimiento.

### Partida genérica para varios clientes o empresas

Un ajuste global de US$500 por diferencias menores puede afectar a varias personas sin tener un crédito único. Registre el concepto completo, marque la distribución genérica cuando corresponda y documente las empresas y porciones reales. No atribuya el total a un cliente cualquiera.

Para cada depósito o fila manual, indique la parte que corresponde a su caso. La suma utilizada no puede exceder el importe de la partida. Si el monto afecta varias empresas, cada porción conserva su empresa beneficiaria; el importe global se cuenta una vez. Una complementaria genérica que explica dinero no demuestra automáticamente que cada fila del detalle esté correcta: vincule las porciones necesarias o documente que el concepto queda fuera del detalle por cliente.

### Vincular una NC o reducción parcial de aplicación

1. Abra la complementaria importada del core y revise TMOV/TDOC, descripción, débito/crédito, fecha y asiento. Confirme que es un ajuste real de una aplicación, no un depósito ni otra partida interna.
2. Guarde y use **Vincular a aplicación**. Busque la importación contable y seleccione la aplicación original por cliente, crédito, fecha, asiento y recibo. Vincular no altera saldos por sí solo.
3. Elija **Ajuste de aplicación** en la acción de revisión e indique el importe a reducir. Puede ser menor al total original; documente el motivo.
4. Use **Confirmar ajuste** con permisos de supervisor. Revise en la importación el ajuste confirmado y el aplicado neto.
5. Distribuya depósitos únicamente contra el importe neto pendiente. No vuelva a incluir la misma NC como otra cobertura del depósito.

Ejemplo: aplicación US$100, NC confirmada US$20 y depósito US$30 dejan aplicado neto US$80 y CxC US$50. Si la reversión pretende reducir por debajo del efectivo ya utilizado, corrija primero los vínculos incompatibles; no esconda la aplicación ni cambie sus datos originales.

Si cambia la decisión a **Partida de depósito**, guarde y revise que se retire el vínculo financiero con la aplicación y que el ajuste quede en cero. Confirme la partida antes de agregarla al depósito. Revise siempre el resultado; cambiar una etiqueta sin guardar no modifica el tratamiento.

### Compensar dos partidas internas

1. Identifique las dos partidas que se cancelan, incluso si fueron registradas en meses distintos. Conserve ambos asientos y el motivo de la corrección.
2. Desde una partida apta, pulse **Compensar con otra partida**, seleccione la contraparte e indique importe, fecha y motivo. Confirme que ambas corresponden al mismo caso.
3. Revise empresa e importes de signo contrario y disponibles. Si no tienen empresa identificada, la verificación expresa no sustituye la evidencia contable.
4. Confirme la compensación. La app registra la relación en ambas partidas; no crea un depósito ni modifica aplicaciones de crédito.
5. Revise original, compensado y pendiente en ambas. Una compensación parcial deja el resto abierto. Use **Consultar saldo a fecha** para revisar ese historial específico.

Ejemplo: débito de US$75 en abril y crédito correctivo de US$75 en agosto pueden quedar totalmente compensados. Con crédito US$50 quedan US$25 de la primera partida pendientes. Si se equivocó, use **Revertir compensación** con fecha y motivo; no borre el historial ni registre otra compensación del mismo importe sin revisar disponibilidad.

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

### Cerrar y reabrir un período

1. Revise **Pendientes detectados**, **Excepciones del período**, aplicaciones y depósitos vinculados. Un período histórico no puede cerrarse sin aplicaciones efectivas asignadas.
2. Resuelva los destinos inválidos y los detalles de depósito por revisar. Verifique que el aplicado neto esté cubierto por depósitos o ajustes confirmados y que no haya excepciones abiertas del período.
3. En operativo, cargue la cobranza y complete los vínculos de las aplicaciones. Las diferencias informativas de la primera conciliación no bloquean el cierre ni exigen aprobar ajustes provisionales. Todas las filas deben estar aplicadas y remitidas; si hay pagos o vínculos pendientes, registre un corte de control para rendir con pendientes.
4. Guarde y pulse **Cerrar período** o **Cerrar período histórico**, según la modalidad. Lea el mensaje; si falla, corrija el caso indicado, no el campo Estado.
5. Compruebe **Cerrado** y el resultado financiero conservado en el detalle. Cerrar bloquea edición; no garantiza que toda gestión externa de un saldo a favor o asiento contable haya terminado. Revise esas partidas en la bandeja de trabajo.
6. Para una corrección posterior, el supervisor o administrador usa **Reabrir período**, indica motivo y revisa las operaciones afectadas. Conserve la evidencia de la reapertura y cierre nuevamente cuando proceda.

**Registrar corte de control** sirve para conservar Excel y JSON privados con las cifras de ese momento, fecha y motivo o próxima gestión. **Más opciones → Ver cortes registrados** permite recuperarlos. No equivale a cerrar, no paga deuda y no impide recibir evidencia tardía.

En el período, **Total aplicado US$** expresa las aplicaciones antes de compensaciones; **Total cubierto US$** reúne depósitos y partidas complementarias confirmadas que cubren esas aplicaciones; **Pendiente US$** suma lo aún pendiente por aplicación o cuota. Por ejemplo, aplicado US$100.00, depósito US$80.00 y compensación US$20.00 muestran aplicado 100.00, cubierto 100.00 y pendiente 0.00. No sume otra vez los equivalentes en C$ ni reste dos veces la complementaria. Un exceso en otra cuota no reduce ese pendiente, y una CxC trasladada a una partida conserva su gestión fuera del saldo de la aplicación.

## Saldos a favor y rendición

Si se reciben US$1,000, se distribuyen US$800 a créditos, US$100 a cobranza
administrativa y US$100 a saldo a favor, los US$1,000 quedan explicados, pero
pueden quedar US$100 por devolver. Ese pendiente de gestión se presenta por
separado y nunca se convierte en otro pago ni libera el depósito original.

Registre la devolución o aplicación externa, parcial o total, con responsable,
fecha compromiso, referencia y observaciones. El soporte adjunto de la gestión
es opcional en la app; adjúntelo cuando esté disponible y respete la política
documental de la IMF. Una clasificación equivocada se
cancela expresamente antes de redistribuir, siempre que no existan gestiones
realizadas ni períodos cerrados afectados. No compense automáticamente saldos
de personas o empresas distintas.

Para rendir el mes sin detener datos tardíos, registre un corte de control y
consulte **Más opciones → Ver cortes registrados**. Cada corte conserva Excel
y JSON privados del período con las cifras de ese momento. La antigüedad
normal es actual; para consultar una fecha pasada active **Corte histórico**
según el capítulo de consultas históricas. Cambiar solo la fecha de antigüedad
no reconstruye los saldos de esa fecha.
Respalde tanto la base (incluido Version) como los archivos privados.

### Documentar y gestionar un saldo a favor

1. Compruebe que el importe excede realmente la aplicación neta identificada. Antes de crear un saldo a favor, busque aplicaciones faltantes o de otros períodos; dinero sin destinatario no equivale a un exceso del cliente.
2. Desde el depósito confirmado use **Crear saldo a favor de la empresa**. Si el excedente está incluido en una fila, abra esa fila y use **Crear saldo a favor de la empresa** o **Crear saldo a favor del cliente**, según quién sea su propietario. Si la partida ya fue importada del core, reclasifíquela con su evidencia y vínculo en lugar de crear una copia.
3. Indique importe positivo, beneficiario, motivo, tratamiento propuesto, responsable y fecha compromiso. Seleccione la fila que ya incluye el excedente, si corresponde; no agregue otra fila duplicada por ese dinero.
4. Confirme mediante la acción autorizada y revise la distribución completa. El saldo aparece como reserva de la empresa o del cliente, no como otra aplicación al préstamo. **Ver saldos a favor** permite abrir las partidas del depósito.
5. Cuando el core o banco ejecute la devolución o aplicación futura autorizada, abra la partida y use **Registrar gestión**. Indique importe parcial o total, fecha, referencia del core o comprobante y observaciones. El soporte adjunto es opcional; documentar la operación sigue siendo necesario.
6. Revise el pendiente de gestión y su historial. Mantenga seguimiento por el resto. La reserva financiera original no vuelve a estar disponible para otra distribución del depósito.

**Aplicación futura** documenta una operación externa ya realizada; no crea automáticamente un pago futuro en el core. No registre una intención como gestión cumplida. Si el saldo era de otra persona o empresa, solicite autorización y corrija la clasificación de forma trazable antes de redistribuir.

Seleccione **Motivo del saldo a favor** según la evidencia, no por el valor predeterminado. Entre las opciones están **Por refinanciamiento** y **Por cancelación**. El motivo puede corregirse después de confirmar la partida; cambiarlo no altera importes, beneficiarios ni gestiones realizadas.

### Resolver una fila con saldo a favor de la empresa

Esta opción sirve, por ejemplo, cuando la empresa remite dos veces el pago de una aplicación ya cubierta y el segundo importe pertenece a la empresa. Compruebe antes si hubo una segunda deducción al trabajador: eso puede requerir un saldo a favor del cliente.

1. Abra la fila pendiente en **2. Detalle** de un depósito confirmado y pulse **Crear saldo a favor de la empresa**. Requiere permisos para escribir el depósito, crear y confirmar complementarias, y efectivo sin clasificar.
2. Revise el importe positivo propuesto: el menor entre el pendiente de la fila y el efectivo sin clasificar del depósito. Puede registrar solo una parte del exceso.
3. Verifique el motivo sugerido **Error de la empresa** y complete descripción, responsable, fecha compromiso y tratamiento propuesto. Registre fecha y referencias disponibles.
4. Confirme. La partida queda vinculada al depósito y a la fila como evidencia; la beneficiaria es la empresa pagadora. No genera otro pago al crédito ni se agrega como destino de una aplicación.
5. Revise **Saldo a favor de la empresa US$**, separado de **Saldo a favor del cliente US$**, el pendiente y el resumen de la fila. Al confirmar se recalcula la conciliación: si todo el importe queda explicado, la fila se resuelve; si el registro fue parcial, conserva el resto pendiente.

Los saldos a favor de empresa y cliente comparten el límite disponible de la fila y del depósito. No cree ambos por el mismo dinero. Una reserva confirmada protege la fila frente a cambios o eliminaciones incompatibles. Si se cancela una reserva sin gestiones por la vía permitida, vuelve a quedar pendiente esa porción; si ya hubo devolución o aplicación futura, debe corregirse primero la gestión. El registro original y su historial permanecen como evidencia.

### Crear saldos a favor de clientes seleccionados

Cuando varias filas tienen el mismo motivo y comentario, marque al menos dos filas con pendiente positivo en **Pagos por cliente** de un depósito confirmado y pulse **Crear saldos a favor seleccionados**. Requiere permisos para escribir el depósito, crear y confirmar complementarias. Revise cada identidad e importe, complete los datos comunes y confirme. Esta acción crea una complementaria independiente por fila, con su propio vínculo y seguimiento; no reúne los beneficiarios en una sola partida.

Los importes deben ser positivos y respetar el pendiente de cada fila y el efectivo disponible. Las reservas previas de cliente o empresa se descuentan para evitar duplicidades. Revise el resultado y los registros creados antes de repetir la operación. La acción masiva corresponde a saldos de clientes; para el excedente de la empresa use el botón específico de su fila.

### Vincular un saldo a favor con su reclasificación del core

Use **Compensar con otra partida** cuando un saldo a favor documentado tenga una contrapartida contable importada. No cree otro ingreso ni otra distribución del mismo depósito para hacer desaparecer el saldo.

1. Identifique el saldo a favor confirmado y el movimiento original del core. Deben corresponder a la misma empresa identificada y, para saldos de cliente, a la misma persona por número, crédito o nombre completo verificable.
2. Desde **Compensar con otra partida**, seleccione la contrapartida, revise el importe disponible e indique fecha, referencia y motivo solicitados. El saldo a favor requiere un débito original del core; no basta con que ambas partidas muestren el mismo importe positivo.
3. Confirme y revise el historial y pendiente de compensación de ambas partidas. La app conserva el débito original para el control mensual; una partida del core completamente compensada deja de representar un pendiente por clasificar.
4. Gestione la devolución o aplicación al beneficiario por separado con **Registrar gestión**, cuando realmente se haya efectuado. El vínculo contable no acredita por sí solo una devolución ni libera el efectivo reservado.

Ejemplo: saldo a favor de US$19.25 y reclasificación de US$19.25 a la cuenta 3001. La compensación explica la reclasificación; si luego se verifica el reembolso al cliente contra esa cuenta, registre además la gestión con su fecha y referencia. Si falta una acción o evidencia que deba seguirse, el usuario registra una excepción; el vínculo no sustituye ese seguimiento.

### Eliminar una partida previamente cancelada

Cancelar y eliminar son acciones distintas. Para cancelar una partida se mantienen las validaciones de períodos abiertos y de vínculos financieros. Si ya está **Cancelada**, eliminarla no vuelve a exigir que esos períodos permanezcan abiertos, porque su efecto fue retirado al cancelar.

La eliminación sigue requiriendo permisos y respetando los vínculos e historiales protegidos. Una partida con gestiones, compensaciones u otras referencias que impidan eliminar no se borra para perder su trazabilidad. Conserve el respaldo y la evidencia institucional; esta regla no autoriza eliminar una partida confirmada ni evita los controles al cancelarla.

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

## Gestionar excepciones

Ruta: **Conciliación Credinómina → Diferencias y seguimiento → Excepciones** o `/app/cn-reconciliation-exception`.

### Registrar una diferencia de conciliación

1. Abra el período y consulte **Pendientes detectados**. Esta sección calcula problemas actuales; **Excepciones del período** muestra los documentos registrados. Un contador de pendientes no implica que ya existan ocho documentos de excepción.
2. Desde el caso disponible en el tablero, use **Registrar excepción**, o cree una excepción y pulse **Seleccionar caso relacionado**. Elija empresa, cobranza o aplicación y período abierto cuando corresponda.
3. Compruebe nombre, Nro. Cliente, crédito, fila de origen, asiento y recibo. El importe que muestra el selector es el total de la cobranza o aplicación, no la diferencia de la excepción.
4. Escriba el importe de la diferencia, descripción de lo observado y causa inicial. No copie el total original cuando solo se está investigando un faltante parcial. Seleccionar el caso no completa automáticamente el importe de una excepción manual.
5. Asigne responsable, próxima gestión y fecha compromiso. Para pasar a **En revisión**, estos datos son necesarios. Adjunte la evidencia disponible y guarde.
6. Agregue cada gestión al historial. Las gestiones guardadas no se editan ni borran; una corrección se registra como una nueva gestión.
7. Cuando exista evidencia suficiente, registre resolución y causa confirmada, revise los saldos reales y cambie el estado permitido. Resolver una excepción no registra un depósito, una compensación ni un pago al préstamo.

Puede existir una excepción sin período por una partida genérica, una aplicación todavía no ubicada o un seguimiento contable. No asigne un período solo para darle nombre. El identificador usa CN-EXC-mes-año-consecutivo cuando nace con período y CN-EXC-año-consecutivo cuando nace sin él; conserva su identificación inicial aunque se relacione después.

### Dar seguimiento a un asiento pendiente

1. Abra la partida complementaria y pulse **Crear excepción**. Si ya existe, use **Ver excepción** para continuar el mismo caso.
2. Indique responsable y fecha compromiso. La gestión es **Registrar ajuste en el core**. La excepción conserva el vínculo directo y el importe de la partida.
3. Contabilidad registra el ajuste en el core por su procedimiento autorizado. En la excepción documente el asiento y la resolución propuesta.
4. Importe el movimiento contable correspondiente y utilice la verificación del asiento desde la excepción. Revise candidato, signo, monto, empresa y evidencia; una referencia escrita sin coincidencia no demuestra registro.
5. Resuelva mediante la acción de verificación cuando el asiento sea válido. El mismo movimiento no puede volver a usarse como otra partida de cobertura para duplicar su efecto.

El estado **Asiento informado** significa que se indicó una referencia; **Registro verificado** requiere contrastarla con la importación. Aunque esa excepción se resuelva, una CxC por ajuste puede seguir pendiente de cobro y un saldo a favor puede seguir pendiente de devolución.

## Leer y controlar los saldos

### Revisar el depósito

Compruebe el dinero original y su equivalente, luego la distribución. El importe en C$ de la cuenta por cobrar en la contabilidad no siempre coincide con el importe original recibido en el banco; conserve ambos hechos y revise la tasa y descripción, sin reemplazar un valor por el otro.

| Cifra | Qué significa | Uso correcto |
| --- | --- | --- |
| Equivalente US$ | Dinero recibido convertido a US$ | Base de distribución del depósito |
| Asignado | Importes efectivamente distribuidos a destinos conciliatorios | Verificar aplicaciones y complementarias que lo integran |
| Saldo a favor documentado | Parte reservada para un cliente o empresa | Gestionar devolución o aplicación externa sin reutilizar efectivo |
| Sin asignar ni justificar | Dinero cuyo destino no está explicado | Identificar destino antes de declarar distribución completa |
| Remanente antes de saldos a favor | Depósito menos asignaciones | Incluye reservas documentadas; no es todo dinero libre |
| Pendiente de gestión | Importe de saldo a favor aún por devolver o aplicar externamente | Seguimiento independiente del cuadre financiero |

Si un depósito es US$120 y cubre US$100 de aplicaciones más US$20 de cobranza administrativa, está explicado. Si esos US$20 corresponden a saldo a favor, el depósito puede estar conciliado con saldo a favor y conservar US$20 pendientes de gestión. Si no se sabe qué son, quedan sin asignar ni justificar.

### Revisar la cuenta por cobrar

Para cada aplicación, revise aplicado original, ajustes a aplicación, aplicado neto, depósito asignado, cobertura complementaria y pendiente. Un ajuste ya incluido en aplicado neto no se resta otra vez. Las asignaciones planeadas en targets no son cobertura efectiva.

Para una CxC por ajuste de depósito, revise su origen y liquidaciones vinculadas por separado. Un faltante trasladado deja de figurar como pendiente de esa aplicación cubierta, pero permanece como deuda de la empresa. Sume ambas posiciones para evaluar CxC total sin duplicar el traslado.

No use un saldo neto de empresa como autorización para compensar automáticamente una deuda de un cliente con un saldo a favor de otro. El resumen facilita control; la liquidación requiere vínculos concretos.

### Elegir el reporte adecuado

| Reporte o vista | Pregunta que responde | Precaución |
| --- | --- | --- |
| Estado de Cuenta Operativo | Qué está solicitado, aplicado, cubierto y pendiente por caso | Cobranza y deducción son informativas; no es el saldo contractual del core |
| Estado de Cuenta por Empresa | Posición resumida o detalle por empresa con las mismas columnas de saldo | Un saldo neto no ejecuta compensaciones entre beneficiarios |
| Antigüedad de Saldos | Qué CxC está pendiente y con qué vencimiento | Para saldos a una fecha pasada active Corte histórico; cambiar solo la fecha no basta |
| Antigüedad de Saldos por Empresa | La misma deuda agrupada, sin detalle por cliente | Abrir el detalle para identificar la aplicación o ajuste responsable |
| Control Mensual de Movimientos Contables | Qué líneas del core se importaron, sus débitos y créditos originales y convertidos | No es una balanza integrada; cotejar con la balanza externa |
| Transacciones por Empresa | Cuántas transacciones hay por mes y grado de conciliación por importe | Los colores no son porcentajes por cantidad de filas |
| Control de Credinómina | Períodos, depósitos, saldos y gestiones que faltan | El mes de recepción del dinero puede ser distinto al de la cobranza |

En **Estado de Cuenta por Empresa**, el resumen distingue aplicado pendiente, complementarias del core pendientes por clasificar con su signo contable, saldos a favor de empresa y clientes, depósitos sin conciliar y CxC por ajustes. Las reservas y efectivo sin asignar se presentan por separado para explicar el saldo. Una partida del core totalmente conciliada sin gestión pendiente no se vuelve a sumar como pendiente por clasificar.

Los dos reportes de antigüedad separan **No vencido**, **1–15**, **16–30**, **31–60**, **61–90** y **Más de 90 días**. El vencimiento se determina por el plazo configurado para la empresa; las CxC por ajustes usan su fecha compromiso. El día del vencimiento aún no cuenta como día de mora: si vence el 10 de mayo, el 11 es día 1, el 25 es día 15 y el 26 es día 16. Revise vencimientos y configuración antes de interpretar los rangos.

### Cotejar el control mensual con la balanza externa

1. Abra **Control Mensual de Movimientos Contables** y elija el rango desde el primer hasta el último día del mes. Por defecto propone el mes actual.
2. Filtre la cuenta o el conjunto contable que está revisando según las opciones del reporte. Para la cuenta 160209013004, verifique que la extracción original no incluya otras cuentas.
3. No limite empresa, tipo o estado cuando quiera comprobar integridad de la carga mensual completa. Si filtra, documente el alcance del subtotal.
4. Compare por separado total débito y total crédito en moneda original C$ con la balanza externa y los movimientos de esa cuenta. Compare también sus equivalentes US$ sin mezclar monedas ni tasas.
5. Si falta importe, revise archivos, fechas, filas de origen, pendientes de carga y empresas no identificadas. Una clasificación pendiente no justifica que desaparezca la línea del control contable.
6. Revise descripción completa, asiento, cliente, crédito y empresa cuando estén disponibles. Los depósitos sin cliente en la evidencia original no deben aparecer atribuidos a una persona por simple coincidencia de una clave.
7. Para los depósitos, la columna Estado consulta el resultado del documento de depósito vinculado. Un depósito en borrador no prueba dinero ya utilizado en la conciliación.

El reporte incluye movimientos provenientes del core. Un ajuste interno creado manualmente en la app no se suma como débito o crédito del core antes de que exista evidencia importada. El saldo de la balanza puede incluir saldo inicial y movimientos ajenos al período cargado; compare movimientos del mes con movimientos del mes, no solo el saldo final con la CxC actual.

### Leer Transacciones por Empresa

Seleccione año, tipo de transacción y **Incluir borradores** según el alcance. Aplicaciones cuenta las filas de aplicación de CN Accounting Import; Depósitos cuenta documentos de depósito, no las filas de su detalle. Las complementarias del core y las internas se consultan como tipos separados.

La celda verde indica conciliación completa; rojo indica pendiente sin cobertura; naranja y amarillo distinguen cobertura parcial menor y mayor o igual al umbral del 50% del importe. El cálculo considera el importe cubierto de esas transacciones, aunque un depósito pague aplicaciones de meses anteriores. No compara el número de filas verdes con el número de celdas coloreadas: una empresa y mes pueden contener muchas transacciones con estados distintos.

Pulse la celda para abrir **Transacciones del mes** y consulte sus documentos e importes. Revise la línea total del modal y la fila total del reporte. La opción de borradores cambia el universo del conteo; no transforma un borrador en dinero conciliado.

## Consultar saldos a una fecha de corte

El **corte histórico** responde cuánto estaba aplicado, cubierto o pendiente a una fecha efectiva. No debe confundirse con la modalidad **Histórica** de conciliación, que omite la primera conciliación de cobranza, ni con **Registrar corte de control**, que conserva archivos con las cifras del momento en que se registró.

### Consultar el estado de cuenta y la antigüedad al corte

1. En **Control de Credinómina**, pulse **Consultar corte histórico**, indique fecha de corte y empresa si desea limitarla, y abra el reporte. También puede abrir **Estado de Cuenta por Empresa** y completar **Corte histórico al**.
2. Use **Resumen** para la matriz de empresas o **Detalle** para revisar sus movimientos. La fecha incluye las operaciones efectivas de ese día; no admite una fecha futura. Dejarla vacía consulta la posición actual.
3. En **Antigüedad de Saldos** o **Antigüedad de Saldos por Empresa**, active **Corte histórico** y establezca **Fecha para antigüedad**. Sin ese check, la fecha solo mide la antigüedad de los pendientes actuales. La reconstrucción histórica se aplica a CxC, no a la cobranza informativa.
4. Para obtener la posición completa, no limite los movimientos de origen a un mes con filtros Desde/Hasta. Estos filtros acotan el universo consultado; no aportan automáticamente un saldo inicial de períodos excluidos.
5. Revise las advertencias y el detalle antes de exportar. Conserve la fecha y los filtros junto con el informe. La consulta no modifica documentos ni reabre períodos. El Excel general de Control de Credinómina sigue siendo una consulta actual; use la exportación del reporte consultado para entregar el corte histórico.

### Qué considera el corte y cuáles son sus límites

La reconstrucción utiliza fechas efectivas de aplicaciones, depósitos, partidas y gestiones o compensaciones documentadas. Un depósito posterior no cubre una aplicación en un corte anterior. Una devolución posterior tampoco resuelve el saldo a favor antes de su fecha. Se utilizan equivalentes US$ y tasas registrados; no se revalúa con una tasa de cierre nueva.

Se basa en documentos, clasificaciones y vínculos válidos actuales, junto con sus movimientos fechados; no restaura la base de datos tal como estaba ese día. Los documentos actualmente cancelados se excluyen y una reclasificación posterior puede modificar la reconstrucción. Para demostrar exactamente qué se informó en su momento, conserve el corte de control y los respaldos de entonces.

Si una distribución conjunta entre aplicaciones anteriores y posteriores al corte no permite atribuir importes con evidencia, la app no inventa un reparto: muestra la advertencia y el efectivo que no puede atribuir. Corrija la trazabilidad en el documento fuente por el flujo autorizado. Un historial inválido o una tasa ausente no debe interpretarse como saldo cero. Mantenga aparte los faltantes de evidencia y no certifique el corte hasta resolverlos.

## Usar el calendario y la bandeja de trabajo

Ruta: `/app/control-credinomina`. Abra **Calendario** para observar la empresa por mes y **Trabajo de conciliación** para ejecutar las gestiones pendientes.

1. Elija año y empresa. En Año, **Todos** permite consultar todos los años; el calendario dispone entonces de su propio selector de año, inicialmente el corriente.
2. Mantenga **Resumen** para agrupar todos los períodos de una empresa y mes. La celda muestra **Aplicado / Asignado**, período registrado y estado financiero. No suma los depósitos completos otra vez a las asignaciones.
3. Pulse la celda para abrir **Períodos del mes**. Primero revise los períodos de cobranza y después los depósitos recibidos en ese mes, aunque financien otros meses. En cada grupo las tarjetas se ordenan por fecha, de la más antigua a la izquierda a la más reciente.
4. Abra la tarjeta de un período para consultar detalle, aplicado neto, asignado y pendiente. Las observaciones pueden explicar qué quincena o cobranza real corresponde al período.
5. Abra la tarjeta de depósito para ver pagadora, referencia, fecha, cuenta bancaria, original, equivalente US$, resultado y distribución. El resumen distingue créditos, complementarias y saldos a favor del cliente o empresa; el detalle por persona se utiliza para destinos de créditos.
6. Use **Volver al mes** para regresar. Puede abrir el calendario en pantalla completa y salir sin perder acceso a los modales.
7. En **Trabajo de conciliación**, filtre tipo, responsable y compromiso. Revise vencidos y casos sin responsable o fecha. La bandeja consulta todos los años; cambiar el año del calendario no oculta automáticamente gestiones antiguas.

La bandeja agrupa acciones del mismo documento. Si tiene varias, sus importes no se suman y la columna puede mostrar un guion. Un **Importe US$0.00** en una excepción puede deberse a que no se cuantificó la diferencia; revise el documento. En una partida con distribución por revisar puede quedar pendiente técnico sin saldo monetario. Cero no certifica que el caso esté resuelto y el importe de trabajo no siempre es el original del depósito.

## Exportar evidencia y entregar saldos

Desde Control de Credinómina, descargue el Excel con el alcance de año y empresa que corresponde. Revise filtros antes de entregarlo. Las hojas de detalle permiten rastrear períodos, aplicaciones, depósitos y complementarias, sin separar las reglas financieras de histórico y operativo.

La hoja **Resumen** presenta la posición de **Estado de Cuenta por Empresa**. **Partidas y excepciones** conserva lo que requiere gestión; una complementaria del core conciliada y sin pendiente de gestión no aparece allí como problema abierto, aunque siga visible en los detalles. Las partidas se muestran como complementarias, sin duplicarlas bajo columnas de tolerancia o redondeo.

Para un cliente, entregue el estado oficial del core junto con el detalle de conciliación cuando necesite explicar importes en tránsito. Identifique fecha de consulta, empresa, cliente y alcance. No declare una deducción como recibida en banco si el depósito aún no existe, ni use el reporte de conciliación como sustituto de capital, intereses y saldo contractual.

## Casos didácticos para capacitación

### Aplicación de abril pagada en mayo con detalle tardío

La aplicación del 30 de abril es US$100. La empresa deposita el 20 de mayo y entrega detalle el 28 de mayo. Registre el período correspondiente y la aplicación en abril. Registre y confirme el depósito con fecha 20 de mayo. Hasta tener detalle o una distribución manual respaldada, el dinero puede quedar sin asignar. El 28 de mayo cargue el detalle en ese mismo depósito, vincule la aplicación y concilie.

Resultado: aplicación cubierta US$100, pendiente US$0; depósito US$100 explicado. El calendario de mayo muestra el depósito recibido y el período de abril muestra su cobertura. La fecha tardía del archivo no cambia la fecha bancaria ni la contable.

### Deducción incompleta por subsidio

Se envió una cuota de US$80 y la empresa dedujo US$30. Cargue Deducido US$30 y documente subsidio como causa. Si el core aplicó US$30 y el depósito asignado es US$30, la CxC de esa aplicación es cero; la diferencia informativa de cobranza es US$50. Si el core aplicó US$80 y solo se cubrieron US$30, hay US$50 de CxC de aplicación hasta corregir o cubrir con evidencia vinculada. No determine responsabilidad del trabajador a partir del faltante de cobranza solamente.

### Pago parcial y recuperación posterior

La aplicación es US$100 y el primer depósito cubre US$60. Distribuya US$60 y mantenga US$40 pendientes. Un segundo depósito de US$40 se registra con su propia fecha y referencia, se vincula a la misma aplicación y la liquida. No cambie el primer depósito a US$100 ni cree otra aplicación para recibir el segundo.

### Exceso que se registró como ingreso

El cliente depositó US$252.57 y el core aplicó US$252.28 porque ese era el saldo del préstamo. Si la diferencia US$0.29 fue registrada realmente como ingreso, revise y confirme la complementaria del core como **Otros ingresos** y vincúlela al depósito. Si el importe ya está dentro de la fila del cliente, vincule también esa porción al detalle correspondiente.

Resultado: US$252.28 a créditos y US$0.29 a ingreso explican US$252.57. No cree además saldo a favor por los mismos US$0.29. Si la decisión correcta fuera devolverlos, Contabilidad debe corregir el tratamiento en el core y la app debe reflejar esa evidencia.

### Exceso pendiente de devolución

El depósito es US$120 y la aplicación es US$100. Si los US$20 pertenecen al cliente, cree el saldo desde su fila; si pertenecen a la empresa, use el botón de saldo a favor de la empresa. Identifique beneficiario, motivo, responsable y compromiso. Con US$100 a créditos y US$20 reservados, el depósito queda explicado, pero la gestión es US$20 pendiente.

Al devolver US$8, registre gestión con fecha y referencia, adjuntando soporte si está disponible; quedan US$12 por gestionar. El depósito original sigue reservando US$20 y no puede utilizar esos US$8 otra vez. Si registró una devolución inexistente, revierta esa gestión con motivo; no elimine la partida.

### Pago duplicado que pertenece a la empresa

Una aplicación de US$309.12 quedó cubierta por el primer depósito. La empresa remite otros US$309.12 por error y el segundo depósito contiene una fila por ese mismo cliente. Compruebe que el dinero extra pertenece a la empresa y abra la fila del segundo depósito. Use **Crear saldo a favor de la empresa**, documente el error y confirme US$309.12.

Resultado: el primer pago conserva su distribución; la segunda fila muestra US$309.12 de saldo a favor de la empresa y cero pendiente, sin un segundo pago al préstamo. La empresa conserva US$309.12 pendientes de gestión. Si solo documenta US$100, la fila mantiene US$209.12 pendientes hasta explicar el resto. Registrar o completar esa reserva no acredita una devolución ya realizada.

### Faltante trasladado a CxC de empresa

La aplicación es US$100, el depósito US$90 y se confirma ajuste −US$10 con subcategoría CxC a la empresa. Distribuya el depósito con ese ajuste. Aunque el depósito quede explicado, los US$10 deben permanecer en la CxC por ajustes y en el seguimiento.

Use **Aplicar cobro / Compensar CxC** para vincular un cobro posterior US$3: quedan US$7. Cuando se cobren o compensen los US$7 restantes con evidencia válida, la deuda queda liquidada. Informar un asiento no la paga; no se admite condonarla mediante un cambio de clasificación.

## Resolver problemas frecuentes

### El archivo está adjunto pero no hay filas

Verifique que guardó y pulsó el botón de carga de ese documento. Consulte el mensaje final y recargue para comprobar estado. No repita a ciegas si se interrumpió la conexión; revise documentos creados y registro de errores con el administrador.

### Falta cliente o empresa en una aplicación

Revise crédito normalizado, corte elegido y Nro. Cliente del core. Luego revise nombre oficial y alias comprobados de empresa. NO IDENTIFICADA es una cola de revisión, no un convenio definitivo. Corregir una empresa exige evidencia y una revisión de vínculos antes de recalcular.

### Los destinos suman el depósito pero dice Revisar detalle

Compruebe que cada fila esté vinculada a sus destinos y que identidad e importes coincidan. Si la fila incluye un ingreso o saldo a favor, explique esa porción en su vínculo; si el concepto es externo al detalle por cliente, no lo agregue artificialmente a una persona. El resultado financiero y la validación del detalle son controles distintos.

### El depósito está conciliado pero sigue en Qué falta hacer

Revise si el pendiente es devolución, aplicación futura, registro del ajuste en el core o CxC por ajuste. Distribuir el dinero no realiza esas gestiones. Abra el documento de la acción antes de repetir la conciliación.

### El período muestra pendiente distinto del total del depósito

El período considera solo lo asignado a sus aplicaciones; el depósito puede cubrir otros períodos, otras empresas autorizadas, ingresos y saldos a favor. Abra la distribución completa. No compare aplicado de un período con el total bancario de todos los destinos.

### No puedo cambiar un documento confirmado o cerrado

Los importes calculados y datos de origen están protegidos. Corrija vínculos mediante acciones autorizadas; reabra un período cerrado con motivo. No fuerce valores desde la base de datos ni desactive una aplicación con efectivo asignado o reservado.

### No puedo cancelar o eliminar un movimiento del core

Compruebe si el registro proviene de un archivo contable. **CN Accounting Import**, **CN Complementary Item** y **CN Remittance Allocation** importados exigen la asignación explícita del rol **Eliminar Mov. del Core**, además del permiso normal. La restricción alcanza borradores importados y documentos cancelados que se intenten eliminar. Un borrador de importación sin filas ni evidencia de carga puede eliminarse con los permisos normales. **CN Accounting Import** conserva su flujo sin confirmación; esta protección no agrega un paso de cancelar al proceso habitual.

Para corregir únicamente el reparto de un depósito, use **Desconciliar** y vuelva a conciliar con los destinos correctos. Tener el rol de eliminación no permite saltarse períodos cerrados ni vínculos financieros.

### Recuperar un depósito histórico eliminado

Solicite al administrador la restauración desde **Deleted Document**, conservando la copia original eliminada. Al restaurar un depósito histórico en borrador o cancelado, se conserva su evidencia de origen, detalle y destinos válidos, se retiran destinos de complementarias anuladas o eliminadas y se registra el retiro en el historial. El depósito queda en borrador con resultados pendientes; revise, confirme y concilie de nuevo.

Verifique la pestaña **Origen contable** y sus datos. Crear una copia manual con los mismos importes no recupera la identidad contable ni equivale a restaurar el original. Si faltan los datos de origen, solicite revisión de la restauración antes de reutilizar el documento.

### El estado del crédito muestra No Identificado

Revise el número normalizado, la empresa, los períodos seleccionados y el corte vinculado a sus importaciones. Confirme que el corte esté habilitado y que el crédito aparezca en él o en el corte habilitado inmediatamente anterior. Un crédito presente en cualquier otro corte no satisface esa búsqueda. Cambiar notas y guardar no vuelve a consultar una fila sin cambios; revise el detalle mediante el flujo de carga o los vínculos de crédito y períodos, respetando sus reservas existentes.

### Los totales no coinciden con la balanza

Revise alcance de cuenta, fechas, monedas, débitos y créditos, filas originales, movimientos aún no cargados y saldo inicial. Diferencias de conciliación no justifican cambiar la evidencia del core. Pida la extracción completa y el detalle de la balanza a Contabilidad.

## Lista de revisión diaria y mensual

### Revisión diaria del operador

- Confirmar que las cargas terminaron y que no se duplicó una ejecución tras un error de conexión.
- Revisar empresa y cliente de las nuevas aplicaciones y resolver NO IDENTIFICADA con evidencia.
- Registrar depósitos por su fecha e importe real y solicitar detalle faltante.
- Revisar filas no conciliadas, vínculos manuales y dinero sin asignar ni justificar.
- Revisar compromisos vencidos, responsables y próximas gestiones de excepciones, CxC y saldos a favor.
- Conservar soportes y comprobar que toda corrección tenga motivo e historial.

### Revisión mensual del supervisor

- Cotejar débitos y créditos de la cuenta 160209013004 con los movimientos y la balanza externos.
- Revisar aplicado original, ajustes, aplicado neto, cobertura y CxC total, sin restar dos veces un ajuste.
- Revisar depósitos completos, distribución por destino, saldos a favor de clientes y empresas y efectivo sin clasificar.
- Verificar complementarias del core por clasificar, ajustes internos pendientes de asiento y CxC trasladada aún no cobrada.
- Revisar antigüedad y términos de vencimiento; no atribuir un plazo histórico no acreditado como si fuera el convenio original.
- Registrar un corte de control si hay pendientes, con motivo y siguiente gestión. Cerrar solo cuando se cumplan los controles de liquidación y revisión.
- Comprobar que el período cerrado queda protegido y que una reapertura conserva motivo y posterior revisión.
- Conservar Excel y JSON de cortes, Version y adjuntos privados en el respaldo; probar periódicamente la recuperación en un sitio aislado.

## Mantener el manual actualizado

Revise este manual cuando cambien campos, botones, reglas de asignación, permisos o reportes. Verifique los pasos en un sitio de ensayo antes de capacitar sobre una nueva versión. La guía de instalación y uso contiene los comandos de actualización; este manual se concentra en la operación financiera y no autoriza reparaciones directas en la base de datos.

El Word se genera desde este procedimiento mediante tools/build_reconciliation_manual.py. Ambos documentos deben actualizarse juntos para evitar instrucciones contradictorias.
