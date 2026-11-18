# cs-carry live (Binance USDT-M)

Mismos parámetros que el backtest: top 40 por mediana 180d de volumen, funding acumulado 7d, long tercio inferior / short tercio superior, inverse-vol (EWMA span 20), rebalanceo diario, gross 1 / neto 0.

| archivo | rol |
|---|---|
| `strategy.py` | señal pura (sin I/O), verificada igual al backtest |
| `binance_futures.py` | cliente REST (HMAC), solo `requests` |
| `run_live.py` | orquestador: datos → pesos → plan de órdenes → chequeos → ejecución |
| `tests/test_live.py` | paridad contra `../backtest.py` + pipeline con cliente simulado |

## Uso
```bash
pip install -r requirements.txt
export BINANCE_API_KEY=... BINANCE_API_SECRET=...      # permisos: Enable Futures; SIN retiros; restringir por IP
python run_live.py                       # dry-run (default): imprime objetivos y órdenes
python run_live.py --testnet --live      # testnet primero (keys de testnet.binancefuture.com)
python run_live.py --live --first-run    # primer día en producción (turnover grande permitido)
# cron (UTC), diario:  1 0 * * *  cd /ruta/live && python run_live.py --live >> run.log 2>&1
```
Parámetros: `--capital-fraction` (fracción del equity a operar), `--leverage` (solo margen), `--max-weight`, `--max-turnover`.

## Timing (replica el backtest)
Señal con barras diarias cerradas al 00:00 UTC y funding incluyendo el settlement de las 00:00. El script espera hasta que ese funding esté publicado (≥90% del universo) y aborta si no aparece. La ejecución es a mercado, justo en el cierre.

## Seguridad / supuestos
- Dry-run por defecto; `--live` es obligatorio para enviar órdenes. Un marcador diario evita doble ejecución.
- Usar una **subcuenta dedicada**, one-way mode, USDT margin: el script asume que toda posición abierta es de la estrategia.
- Aborta si: gross/neto anómalos, peso > `--max-weight`, turnover > `--max-turnover`, gross actual > 1.5x capital, menos de 9 nombres.
- Órdenes `reduce-only` primero, luego aperturas; trocea por `maxQty`; polvo bajo `minNotional` se omite. Si falla alguna orden, re-ejecutar recalcula deltas.
- Log en `state/` (targets CSV, órdenes JSONL).

## No verificado / a vigilar
- **No se probó contra Binance** (la API estaba geobloqueada en el entorno de desarrollo): probar en testnet antes de producción. `account`/`positionRisk` usan v3 con fallback a v2.
- Slippage: mercado a las 00:00 UTC puede ser peor que los 5 bps del backtest en alts poco líquidos; medir `avgPrice` vs. mark y considerar TWAP / órdenes límite.
- Sin stops ni control de liquidación: con gross 1x y leverage 3x el riesgo de liquidación es bajo pero no nulo en gaps; el backtest tuvo −24% de MaxDD.
- Delistings/halts: símbolos fuera de TRADING se marcan "NO OPERABLE" y requieren acción manual.
