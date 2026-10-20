# Marín (memebot): bot de meme coins en Solana

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

## Estrategia Marín v3 (rebote confirmado), la actual

Ajustes hechos con 85 operaciones simuladas (28% de aciertos, -18% con la v2):

- **Las perdedoras se compraban todavía cayendo** (-3% en 5 min); las ganadoras ya rebotaban (+3%). La v3 espera el retroceso **y además** que la moneda **rebote +4% desde su mínimo** e ir positiva en 5 min.
- Las perdedoras habían subido más antes de comprarlas (+89% en 1 h contra +62% las ganadoras): máximo **+100% en 1 h** (el pump con excepción sigue igual).
- Las ganancias se escapaban (llegaban a 1.5–1.7x y salían en ~1.05x): take profit **40% a 1.5x, 30% a 2.5x y 15% a 5x**, trailing desde **1.3x** con **-18%**.
- Stop loss **-22%** y revisión de precio **cada 2 s** (antes -30% y cada 5 s; los stops salían muy tarde).

- **Régimen del mercado** (arriba en el panel, "Mercado 🟢 62"): puntaje 0–100 que combina la tendencia de SOL, SOL y BTC hoy, y cuántas meme coins del radar van subiendo. Por ahora solo se mide y se guarda en cada compra; cuando haya datos se decide si filtrar con `risk.regime_min_score`. Inspirado en `crypto-regime-analyzer` de [claude-trading-skills](https://github.com/tradermonty/claude-trading-skills) (MIT).
- **Freno por racha** (solo en real): tras 4 pérdidas seguidas, pausa 1 hora (`risk.max_consecutive_losses`, `risk.streak_pause_minutes`).
- **Simulación más pesimista:** 3% de costo por operación en vez de 2% (`execution.paper_slippage_pct`), para que un resultado verde sea más creíble en real.

Aplicarla: `python -m memebot estrategia` (o `estrategia marin-v2` para volver a la anterior). En el reporte sale como **"Rebote confirmado (v3)"**.

## Estrategia Marín v2 (entrada en retroceso), anterior

En la primera simulación casi todas las pérdidas fueron stop loss justo después de comprar: el bot compraba en el pico de la vela. La v2 cambia la entrada:

1. Cuando una moneda califica (filtros + puntaje), Marín **no compra todavía**: la pone **"en la mira" 🎯** y guarda su máximo.
2. Compra cuando la moneda **retrocede entre 12% y 35%** desde ese máximo y todavía hay compradores (compras/ventas ≥ 0.9). Si cae más de 35% o pasan 20 minutos sin retroceso, la descarta.
3. Ya no persigue monedas que subieron más de **150% en 1 hora**, y pide **$15k de liquidez** y **$30k de market cap** como mínimo.
   - **Excepción de pump 🚀:** una moneda que ya subió más de 150% (hasta 1000%) puede entrar si se ve muy sana: liquidez ≥ $40k, market cap ≥ $100k, compras/ventas ≥ 1.3, ≥ 60 transacciones en 5 min y buen puntaje. A esas les pide un retroceso más profundo (20–45%) y se miden aparte en el reporte ("Pump con excepción"). Se apaga con `python -m memebot set filters.pump_exception false`.
4. Si una posición llega a **1.25x**, el stop sube a precio de entrada (breakeven).
5. Guarda **cómo estaba cada moneda al comprarla**. El `report` y la pestaña Cerradas comparan **ganadoras vs perdedoras**, para ajustar con datos.

Para aplicarla a un `config.json` existente: `python -m memebot estrategia` (muestra los cambios y pide confirmar). Para volver a la v1: `python -m memebot set filters.entry_mode momentum`.

No hay estrategia garantizada: esto es una hipótesis que la simulación tiene que confirmar.

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

Puedes dejar todo preparado sin arriesgar nada:

1. `python -m memebot setup-wallet` crea una wallet **nueva y solo para el bot** y la guarda en `.env`. No uses tu wallet principal (ni la de GMGN).
2. **Respaldo:** abre `.env` con el Bloc de notas, copia la clave e impórtala en Phantom (Agregar cuenta → Importar clave privada). Si pierdes `.env` sin respaldo, pierdes lo que haya en esa wallet.
3. Para ver la wallet del bot en GMGN, usa el link que da el comando o el de arriba del panel.
4. Opcional pero recomendado: crea un RPC gratis en [Helius](https://helius.dev) y ponlo en `.env` como `SOLANA_RPC_URL`.
5. `python -m memebot preflight` revisa la wallet, la conexión, Jupiter y tus límites de riesgo **sin enviar ninguna transacción**.

**¿Quieres usar tu propia wallet (la de GMGN o Phantom)?** Corre `python -m memebot import-wallet`: te pide la clave sin mostrarla y, antes de guardar, te enseña la dirección para que confirmes que es la tuya. En ese caso pon topes en `config.json` → `risk`:
- `max_capital_sol`: máximo de SOL que el bot puede tener metido a la vez (por ejemplo `0.2`).
- `max_total_loss_sol`: si la pérdida acumulada llega a esto, deja de comprar (por ejemplo `0.15`).

Así el bot nunca toca todo tu saldo. El bot solo vende las monedas que él compró, nunca las tuyas. Mientras el bot opera en real, evita operar a mano desde la misma wallet: se confunden los números de ganancia del bot.

Cuando la simulación lleve días dando buenos números:

6. Deposita poco SOL en la dirección del bot.
7. Corre `python -m memebot preflight` otra vez y aplica las recomendaciones de riesgo que te dé.
8. `python -m memebot run --live` (pide escribir SI para confirmar).

**Nunca subas `.env` a GitHub** (ya está en `.gitignore`) ni se lo pases a nadie, ni a un chat.

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
| `filters.min_liquidity_usd` | 15000 | Liquidez mínima |
| `filters.min_market_cap_usd` | 30000 | No compra monedas más chicas que esto |
| `filters.max_price_change_h1_pct` | 150 | No persigue monedas que ya subieron más que esto en 1 h |
| `filters.entry_mode` | `pullback` | `pullback` = espera retroceso; `momentum` = compra en cuanto califica (v1) |
| `filters.pullback_min_pct` / `pullback_max_pct` | 12 / 35 | Rango de retroceso desde el máximo para comprar |
| `exits.breakeven_after_multiple` | 1.25 | Al llegar a este múltiplo, el stop sube a la entrada |
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
