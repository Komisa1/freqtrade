# memebot: bot de meme coins en Solana

Bot que detecta meme coins nuevas en Solana (pump.fun y DexScreener; GMGN opcional), descarta las que tienen pinta de rug, compra las que muestran impulso real y las vende con stop loss, toma de ganancias escalonada y trailing stop.

> **Léelo antes de poner dinero.** La gran mayoría de las meme coins terminan en cero, y muchas son estafas hechas para que los bots las compren. Ningún bot garantiza ganancias. Este arranca en **modo simulación** (paper trading) y no gasta dinero real hasta que tú lo actives. Déjalo varios días en simulación, mira el `report`, y si pasas a real empieza con montos que puedas perder enteros.

## Cómo funciona

```
pump.fun (PumpPortal, en vivo) ─┐
DexScreener (boosts/perfiles) ──┼─► watchlist ─► filtros + puntaje ─► RugCheck ─► límites de riesgo ─► COMPRA
GMGN trending (opcional) ───────┘        (re-evalúa cada 20 s, hasta 30 min)                                │
                                                                                                             ▼
                                   VENTA ◄── stop loss / take profit 2x y 4x / trailing / tiempo / rug ◄── seguimiento cada 5 s
```

1. **Fuentes.**
   - **pump.fun**: cada token nuevo y cada token que se "gradúa" (migra a PumpSwap/Raydium), en tiempo real vía el websocket gratuito de PumpPortal.
   - **DexScreener**: tokens con boosts o perfiles recientes, que suelen ser los que tienen atención.
   - **GMGN**: trending, en modo *experimental*. GMGN no tiene API pública, así que su web puede bloquear las peticiones. Viene desactivado; actívalo con `"gmgn": true`.
   - **FOMO**: no tiene API pública, así que no hay forma fiable de conectarlo. Los tokens que se mueven ahí aparecen igual en pump.fun y DexScreener, que el bot sí monitorea.
2. **Filtros y puntaje (0-100).** Para que un token se compre tiene que cumplir todo esto:
   - más compras que ventas en los últimos 5 minutos;
   - volumen alto respecto a su liquidez;
   - precio subiendo, pero sin haber hecho ya una vela vertical (no persigue tokens con +400% en 1 h);
   - edad mínima de 3 minutos, para no caer en los snipers y bundles del primer bloque;
   - market cap y liquidez dentro de rango.
3. **RugCheck.** Descarta el token si tiene *mint authority* (pueden imprimir más tokens) o *freeze authority* (pueden congelar tu wallet: honeypot), si RugCheck le pone un riesgo alto, o si tiene cualquier riesgo marcado como "danger".
4. **Riesgo.** Tamaño fijo por operación y máximo de posiciones abiertas. Deja de comprar cuando llega al límite de pérdida diaria, espera un rato después de cada pérdida y nunca recompra el mismo token.
5. **Salidas.**
   - **Stop loss**: −30 %.
   - **Toma de ganancias escalonada**: vende el 50 % a 2x (recuperas lo invertido) y otro 25 % a 4x.
   - **Breakeven**: después del primer take profit, el stop sube al precio de entrada.
   - **Trailing stop**: −25 % desde el máximo, una vez que el precio pasó 1.5x.
   - **Salida por tiempo**: vende si en 45 min no hizo +10 %, y como máximo a las 4 h.
   - **Salida de emergencia**: vende si la liquidez se desploma (posible rug).
6. **Ejecución (modo live).** Opera vía **Jupiter**, que encuentra la mejor ruta en todos los DEX, incluida la bonding curve de pump.fun. Opcionalmente puede usar la API de trading de PumpPortal. La transacción se **firma en tu computadora**: la clave privada nunca se envía a ningún servidor.

## Instalación (mañana en tu compu)

Necesitas **Python 3.10 o más nuevo**: https://www.python.org/downloads/ (en Windows marca *"Add python.exe to PATH"*).

```bash
git clone https://github.com/komisa1/freqtrade.git
cd freqtrade/memebot
```

**Windows** (PowerShell):
```powershell
.\install.ps1
```
**Linux / macOS**:
```bash
./install.sh
```

El instalador crea un entorno virtual (`.venv`), instala las dependencias y copia `config.example.json` → `config.json` y `.env.example` → `.env`.

## Panel (la app)

Al arrancar, el bot abre solo tu navegador en **http://127.0.0.1:8080**. Si no se abre, copia esa dirección en Chrome. Ahí ves:

- **Total y saldo** en SOL y en dólares, con la **gráfica de ganancias/pérdidas** (24H / 7D / 30D).
- **Calendario** con el PnL de cada día (verde = ganó, rojo = perdió).
- **Posiciones abiertas**: cuánto metiste, cuánto vale ahora, ganancia o pérdida, multiplicador y tiempo.
- **Cerradas**: historial con el resultado de cada operación y por qué vendió.
- **Radar**: las monedas que el bot está analizando ahora mismo, con su puntaje.
- **Clic en cualquier moneda**: abre su **gráfica en tiempo real** (velas y operaciones en vivo de DexScreener), tu posición, el análisis del bot, un botón **"Vender todo ahora"** y links a GMGN, pump.fun, DexScreener y RugCheck.

- **Trenches** (botón arriba): tres columnas como en GMGN: **Nuevas**, **A punto de migrar** (con la barra de progreso de la curva de pump.fun) y **Migradas**. Cada columna tiene un switch **Comprar** para decidir en qué etapas puede comprar el bot.
- En **Cerradas** sale una tabla de **resultados por etapa**, para ver con números cuál etapa deja más.

- **Mascota propia**: guarda una imagen con el nombre **`mascota.png`** (o `.jpg`, `.webp`, `.gif`) en la carpeta del bot (donde está `start`) y aparece en lugar del cangrejo, con los mismos bailes y reacciones. Para volver al cangrejo, borra o renombra ese archivo. La imagen se queda solo en tu compu.

El panel solo se ve desde tu computadora: nadie de afuera puede entrar.

## Uso

Activa el entorno primero: en Windows `.\.venv\Scripts\Activate.ps1`; en Linux/macOS `source .venv/bin/activate`. En Windows también puedes arrancar con doble clic en `start.bat`.

| Comando | Qué hace |
|---|---|
| `python -m memebot run` | Arranca en **modo simulación** (2 SOL ficticios) |
| `python -m memebot report` | Resultados de la simulación: % de aciertos, PnL, mejores trades |
| `python -m memebot check <MINT>` | Analiza un token puntual: puntaje, motivos y RugCheck |
| `python -m memebot new-wallet` | Crea una wallet nueva para el bot |
| `python -m memebot balance` | Saldo de la wallet del bot |
| `python -m memebot run --live` | **Opera con dinero real** (pide confirmación) |
| `python -m memebot report --live` | Resultados reales |

`Ctrl+C` detiene el bot. Las posiciones abiertas quedan guardadas en `data/` y el bot las retoma al reiniciar. Cada operación queda registrada en `data/trades_paper.csv` o `data/trades_live.csv`, y el log completo en `data/memebot.log`.

## Actualizar

Para bajar la versión más nueva sin perder tu configuración ni tu historial: en Windows corre `.\update.ps1`; en Linux/macOS, `./update.sh`.

## Pasar a dinero real

1. Simula al menos unos días y revisa `python -m memebot report`. Si en simulación pierde, en real va a perder más, porque el slippage real es peor.
2. Crea una wallet **nueva y solo para el bot** (`python -m memebot new-wallet`, o una cuenta nueva en Phantom). No uses tu wallet principal.
3. Deposita poco: por ejemplo 0.5–1 SOL, más unos 0.05 SOL para fees.
4. Edita `.env`:
   ```
   SOLANA_PRIVATE_KEY=tu_clave_privada_base58
   SOLANA_RPC_URL=https://mainnet.helius-rpc.com/?api-key=TU_KEY
   ```
   El RPC público de Solana es lento y limitado. Uno gratuito de [Helius](https://helius.dev) o QuickNode funciona mucho mejor y hace que las órdenes entren a tiempo.
5. `python -m memebot run --live`

**Nunca subas `.env` a GitHub** (ya está en `.gitignore`). Quien tenga esa clave puede vaciar la wallet.

## Alertas en Telegram (opcional)

1. Crea un bot con [@BotFather](https://t.me/BotFather) y copia el token.
2. Consigue tu chat id escribiéndole a [@userinfobot](https://t.me/userinfobot).
3. Ponlos en `.env` como `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID`.

El bot te avisa de cada compra, venta parcial y cierre, con el PnL.

## Ajustes principales (`config.json`)

| Clave | Por defecto | Qué es |
|---|---|---|
| `risk.trade_size_sol` | 0.1 | SOL por operación |
| `risk.max_open_positions` | 3 | Posiciones abiertas a la vez |
| `risk.max_daily_loss_sol` | 0.5 | Al perder esto en el día, deja de comprar |
| `filters.min_score` | 60 | Más alto = menos trades, más selectivos |
| `filters.min_age_minutes` | 3 | Más bajo = entra antes, pero con más riesgo de rug |
| `filters.min_liquidity_usd` | 8000 | Liquidez mínima |
| `filters.max_market_cap_usd` | 3,000,000 | No compra tokens más grandes que esto |
| `exits.stop_loss_pct` | 30 | Stop loss |
| `exits.take_profit_ladder` | `[[2,50],[4,25]]` | `[múltiplo, % de la posición a vender]` |
| `exits.trailing_stop_pct` | 25 | Caída desde el máximo para vender el resto |
| `execution.slippage_bps` | 1500 | Slippage máximo (1500 = 15 %) |
| `execution.priority_fee_sol` | 0.0005 | Propina para que la transacción entre rápido |
| `execution.router` | `jupiter` | `jupiter` o `pumpportal` |
| `sources.gmgn` | false | Activar trending de GMGN (experimental) |

Si quieres buscar *runners* grandes, no subas el tamaño de la posición. Cambia la escalera de salidas: por ejemplo `[[2,40],[5,20],[10,20]]` con `trailing_stop_pct` 35 deja correr más la parte que queda. Ten en cuenta que muchas de esas posiciones van a devolver la ganancia antes de llegar al siguiente escalón.

## Limitaciones honestas

- En **simulación** el bot compra al precio de DexScreener con un 2 % de costo estimado. En real, el slippage en tokens de poca liquidez puede ser bastante mayor, y algunas transacciones fallan.
- DexScreener actualiza con unos segundos de retraso. El bot no compite con los snipers de bloque 0; su estrategia es entrar en tokens que ya muestran tracción.
- RugCheck ayuda pero no es infalible: hay rugs que pasan todos los filtros (el dev vende de golpe, bundles ocultos).
- GMGN y los endpoints no oficiales pueden dejar de funcionar sin aviso.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```
