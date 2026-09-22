# Conciliacion Credinomina

Aplicacion independiente para **Frappe Framework**. No depende de ERPNext ni de
`loan_manager`, y no contabiliza ni modifica creditos en el core de la IMF.

La página **Control de Credinómina** presenta una matriz real por empresa y mes,
con indicadores, saldos parciales, excepciones y depósitos sin distribuir.

La aplicacion mantiene un submayor operativo que separa tres hechos distintos:

1. la cuota enviada a la empresa para deduccion;
2. la deduccion confirmada al trabajador (cuenta por cobrar a la empresa);
3. el deposito de la empresa y la aplicacion del pago en el core.

La conciliación de remesas admite pagos parciales, un depósito para varias
cobranzas y varios depósitos para una cobranza. Solo se distribuye
automáticamente cuando la referencia y los importes identifican un destino
inequívoco; los repartos ambiguos requieren una distribución manual aprobada.

La implantación tiene dos modalidades: **histórica** (abril de 2025 a agosto de
2026), que compara directamente aplicaciones del core contra depósitos, y
**operativa** (desde septiembre de 2026), que agrega la conciliación de
cobranza/deducción. El histórico no presume deducciones ni genera una cuenta
por cobrar a la empresa sin evidencia de planilla.

Cada empresa configura cobranza **mensual o quincenal**. En la modalidad
operativa, las dos quincenas son períodos independientes dentro de un mes;
el tablero muestra el total mensual y permite abrir cada quincena.
Si el core registra una sola aplicación para ambas quincenas, se reparte entre
ellas cuando la suma exacta de sus saldos y sus identificadores permiten un
cruce único; el reparto queda trazable en la fila de origen.

Cada empresa puede autorizar una **tolerancia de conciliación en US$** (cero
por defecto, máximo US$0.10). Una diferencia de hasta ese importe, tanto
positiva como negativa, entre una aplicación y un depósito inequívocamente
vinculados genera un movimiento interno de conciliación. El movimiento deja
visibles ambos importes y la diferencia con signo; no crea un asiento ni
modifica el core. No se usa para diferencias cambiarias ni repartos ambiguos.

Cuando no llega el detalle de la empresa, un depósito contable y bancario que
cubra exactamente toda la cobranza puede usarse, con confirmación y justificación
del operador, como **deducción inferida por depósito**. La app distingue esta
inferencia del detalle de planilla confirmado y permite revertirla o sustituirla.

## Instalacion

```bash
bench get-app /ruta/a/credinomina
bench --site sitio.local install-app credinomina_reconciliation
bench --site sitio.local migrate
```

Consulte [docs/procedimiento_operativo.md](docs/procedimiento_operativo.md) para
el flujo mensual y [docs/instalacion_y_uso.md](docs/instalacion_y_uso.md) para la
configuracion inicial.
