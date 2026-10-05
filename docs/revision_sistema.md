# Revisión de control y uso de Credinómina

Revisión del 4 de octubre de 2026 sobre el código local, pruebas transaccionales
y recorridos visuales en un sitio aislado con Frappe **15.111.1**. No se modificó
producción ni se cambiaron los permisos del catálogo. Las correcciones están en
el árbol de trabajo, sin commit ni push.

La estructura del sistema permite un flujo simple: **cartera y clientes →
cobranza → detalle de empresa → aplicaciones → depósito y distribución**. El
histórico comienza en aplicaciones. Mi conclusión es que los controles locales
probados son adecuados para avanzar a un piloto supervisado, pero no basta
esta revisión para autorizar el despliegue: quedan verificaciones del entorno
y del respaldo real de producción.

## Hallazgos y correcciones

| Prioridad | Riesgo encontrado | Resultado de la corrección |
| --- | --- | --- |
| Alta | Ocultar, desactivar o cambiar una aplicación con dinero asignado podía dejar efectivo distribuido contra una fuente alterada. También se podían cambiar manualmente resultados calculados. | Se protege la evidencia original, la elegibilidad y los resultados derivados. Los recálculos internos y el reprocesamiento legítimo de filas sin asignaciones siguen permitidos. |
| Alta | Los filtros de antigüedad podían mezclar aplicaciones con ajustes y dar una lectura incorrecta del pendiente. | **CxC total** reúne las dos poblaciones; **Aplicado pendiente de depósito** y **CxC por ajustes** las separan sin consultar ni sumar la población equivocada. Los KPI abren el filtro correspondiente. |
| Alta | Una CxC trasladada a un ajuste necesitaba una liquidación vinculada, no solo un estado contable o una nota de gestión. | **Aplicar cobro / Compensar CxC** registra liquidaciones parciales con origen, empresa, importe y evidencia de destino. Solo las distribuciones reales o compensaciones confirmadas reducen la deuda; no se crea un segundo asiento del core. |
| Media | Una misma partida podía aparecer como varios trabajos y aparentar varias deudas. | La bandeja agrupa por caso y conserva sus acciones financiera, contable y de gestión; sus importes no se suman entre sí. |
| Media | Consultas de saldos recorrían depósitos ajenos y «Mostrar más» reconstruía miles de aplicaciones para devolver solo 100. | Las lecturas de complementarias se acotan a los vínculos necesarios; las aplicaciones históricas sin período se paginan en base de datos, conservando el contador completo y los permisos de los padres. |
| Media | La ayuda todavía mencionaba registros eliminados, un botón combinado de confirmar y conciliar, y negaba que el histórico formara parte de la CxC. | Se actualizaron las guías de instalación y operación, las categorías vigentes y el procedimiento de cobro de ajustes. |

## Saldos que deben mantenerse separados

La cobranza y la deducción son controles de la primera conciliación; no son la
CxC de esta herramienta. La CxC parte de lo aplicado en el core y de su cobertura
vinculada, sin descontar dos veces los ajustes ya incluidos en el aplicado neto.

Ejemplo comprobado: aplicación **US$100**, depósito **US$90** y ajuste **−US$10**
con subcategoría CxC a la empresa permiten explicar la distribución del depósito,
pero siguen dejando **US$10 por cobrar**. Un cobro posterior vinculado de US$3
deja **US$7 pendientes**. Registrar o verificar el asiento no paga esos US$7.

Un depósito puede estar conciliado y conservar un saldo a favor por devolver,
una CxC trasladada o una gestión contable pendiente. Por eso se distinguen:

- Distribución del efectivo y resultado de conciliación.
- Saldo financiero pendiente de cada partida.
- Saldo a favor pendiente de gestión, separado entre cliente y empresa.
- CxC por ajuste original, cobrada, compensada y pendiente.
- Registro informado o verificado en el core.

No se netean automáticamente créditos, efectivo sin asignar ni deudas de
empresas o personas diferentes. Una partida genérica conserva un único importe
y distribuciones por empresa; no se inventa su atribución a clientes. La prueba
de deuda genérica separó US$10 y US$20 de dos empresas y rechazó usar capacidad
de una para sobrecobrar a la otra.

## Facilidad de uso y trazabilidad

Calendario y Trabajo de conciliación permiten consultar y gestionar por
separado. Los pendientes antiguos siguen visibles aunque el calendario muestre
el año actual. El operador debe comenzar por los casos sin identificar o con
evidencia inconsistente, continuar por distribuciones pendientes y terminar con
la gestión de saldos y verificación de asientos.

En el navegador se verificaron filtros, fechas según configuración, cifras de
control, formulario y modal de cobro de CxC, tarjetas mensuales y navegación
mes → depósito → volver al mes. Los modales permanecen por encima del calendario
ampliado. Se separan los depósitos completos recibidos en el mes de los importes
asignados a períodos, evitando sumar el mismo efectivo dos veces.

La trazabilidad conserva archivos y huellas, filas originales, comprobantes,
identidades y vínculos financieros. Las correcciones de gestiones o compensaciones
agregan reversiones; no borran su historia. Si se cancela una partida y un depósito
conserva un destino hacia ella, el sistema exige retirar o corregir ese destino
expresamente antes de reutilizarlo. No elimina silenciosamente instrucciones ni
reactiva una operación cancelada. Las lecturas de distribución corrupta fallan
con una identificación del depósito, no con un saldo cero aparentemente válido.

Se probaron usuarios con los roles existentes: el operador prepara y consulta;
el supervisor puede confirmar el cobro. Esto no certifica la configuración
particular de usuarios y restricciones de empresa del servidor real.

## Verificación y rendimiento

- **907 pruebas Python y 540 subcasos**, más **51 archivos de pruebas JavaScript**
  aprobados. No son una certificación de todos los recorridos posibles.
- Ensayos SQL de protección de fuentes, recuperación parcial, compensación,
  cancelación acotada, partidas genéricas, permisos y dos conexiones concurrentes.
  La concurrencia no permitió cobrar dos veces la misma CxC.
- Carga real del importador con **26,000 líneas sintéticas**, 260 documentos y
  26 bloques confirmados: **108.4 segundos**, pico **405.7 MiB**, sin releer el
  archivo original por bloque y conservando las selecciones manuales de empresa.
- Lectura sintética de **100,000 aplicaciones**: calendario **2.13 segundos**,
  bandeja **1.56 segundos**, páginas de 100 aplicaciones **0.54–0.59 segundos**;
  contador completo de 100,000. No fue una importación real de 100,000 líneas.
- Respaldo de la versión actual restaurado en otro sitio vacío: contenido
  idéntico de **25 tablas**, **272 entradas de Version** y **dos archivos de
  evidencia** por huella. La CxC conservó sus US$7 y una segunda migración no
  cambió los datos comparados. Hay tablas vacías en este conjunto sintético.

Los tiempos son del ensayo local, no un compromiso de producción. La paginación
real en base de datos se incorporó a las aplicaciones sin período; las páginas
de depósitos y trabajos todavía reconstruyen sus conjuntos. Conviene medirlas
con la distribución real de empresas, depósitos y vínculos antes de ampliarlas.

## Condiciones antes de desplegar

1. Restaurar un respaldo **real** de producción en un sitio aislado y comparar
   datos, privados, cortes, historial y configuración de cifrado. El ensayo
   sintético no verificó restauración de credenciales cifradas.
2. Probar allí la actualización y un mes operativo con cartera, convenios y
   usuarios reales. El cuadre previo de marzo 2026 verificó 3,262 movimientos de
   la cuenta 160209013004 contra balanza, no su conciliación financiera.
3. Verificar workers, colas, límites de memoria, reintentos y Socket.IO. La carga
   local ejecutó directamente la lógica de trabajo, sin transporte de colas.
4. Contrastar Python y MariaDB del servidor: esta revisión usó Python 3.12 y
   MariaDB 10.11.14; el framework emite una advertencia de compatibilidad para
   esta última. No se certificó otra combinación de dependencias.

Con esos controles cumplidos, recomiendo un primer cierre supervisado y revisar
diariamente CxC de aplicaciones, CxC por ajustes, depósitos sin asignar y saldos
a favor por gestionar. El verde de conciliación nunca sustituye estas cuatro
revisiones ni la rendición contable externa.
