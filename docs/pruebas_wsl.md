# Ensayo funcional en WSL

Sitio aislado: `cn-reconciliation-test.local`, Frappe Framework 16.33.1. Las
únicas aplicaciones instaladas son `frappe` y `credinomina_reconciliation`;
no se usa ERPNext ni Loan Manager. El asistente inicial se completó en
español de Nicaragua, zona `America/Managua` y moneda base NIO. Los datos son
sintéticos y algunas fechas futuras se usan deliberadamente para reproducir
la secuencia de cobranza, aplicación y depósito; no representan operaciones
reales de la IMF.

## Cobertura y resultados

| Escenario | Resultado comprobado |
| --- | --- |
| Tres empresas, 20 clientes cada una, septiembre–noviembre 2026 | 9 períodos, 180 filas de cobranza, 171 aplicaciones y 9 depósitos. |
| Detalles que llegan después de los depósitos | 8 depósitos del mes siguiente conciliados con detalle simulado tres días después y 1 conservado como `Detalle pendiente`, con US$920.50 sin distribuir; no se inventa el cliente destinatario. Una referencia única tampoco libera la asignación automática sin detalle. |
| Archivos XLSX procesados por las cuatro acciones | 20 filas de cobranza, 20 de deducción, 19 aplicaciones y 20 filas de detalle integrador. El depósito termina conciliado por US$920.50; la fila sin deducción permanece visible. |
| Histórico con corte de fecha exacta | Dos aplicaciones por US$83 y un depósito posterior por US$83; el período termina `Historico conciliado` sin reconstruir una deducción antigua. |
| Monedas | Un depósito de C$3,650 con tasa documentada de 36.5 equivale a US$100; sin evidencia de tasa se rechaza. Los formularios y reportes muestran US$ y C$ según cada campo, aunque el sitio tenga NIO como moneda base. |
| Permisos | Operador y supervisor consultan tablero y antigüedad; ambos cargan fuentes. Solo supervisor puede confirmar depósitos. |
| Reportes | El resumen devuelve 9 períodos, el estado de cuenta 180 filas y la antigüedad 37 saldos abiertos para los tres meses. Funcionan los filtros por empresa y cliente. |
| Interfaz | Workspace, tablero, tres reportes y formularios de período, importación y remesa abiertos en navegador de prueba sin errores JavaScript. |

La antigüedad mostró US$805.50 de cuotas no deducidas al trabajador y
US$920.50 de deducciones sin remesa **asignada por cliente**. El último monto
puede estar cubierto por el depósito recibido sin detalle: no se suma al
depósito ni se presenta como CxC confirmada a la empresa. El reporte usa
bandas excluyentes y permite revisar cada cliente y empresa.

## Comandos de verificación

Ejecute estos comandos desde el Bench, solo en el sitio de ensayo ya poblado:

```bash
bench --site cn-reconciliation-test.local migrate
env/bin/python -m unittest discover -s apps/credinomina_reconciliation/tests -q
bench --site cn-reconciliation-test.local execute credinomina_reconciliation.testing.simulate_three_employers.audit
bench --site cn-reconciliation-test.local execute credinomina_reconciliation.testing.import_generated_files.audit
bench --site cn-reconciliation-test.local execute credinomina_reconciliation.testing.simulate_historical.audit
bench --site cn-reconciliation-test.local execute credinomina_reconciliation.testing.smoke_roles.run
bench --site cn-reconciliation-test.local execute credinomina_reconciliation.testing.smoke_currency.run
```

Los generadores `run` de los ensayos crean datos en un sitio descartable;
`simulate_three_employers.run` admite repetición, mientras los otros se
ejecutan una sola vez. Las funciones `audit` son de lectura. Ninguno de
estos comandos debe ejecutarse en producción.

El control de antigüedad es **operativo**, no un cálculo de provisión ni una
fotografía contable histórica. Para registrar provisiones de cartera se debe
contrastar la cuota no deducida con el saldo y los días de mora del crédito en
el core y aplicar la política vigente de la IMF. La app no presume tasas de
provisión ni genera asientos contables.
