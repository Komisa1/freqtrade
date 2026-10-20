# Videos con Claude Code (motion graphics desde un prompt)

Escribes qué video quieres y Claude lo arma y lo convierte en **MP4**. Usa dos herramientas gratuitas y de código abierto:

- **[HyperFrames](https://github.com/heygen-com/hyperframes)** (de HeyGen): la principal. Claude arma cada escena con HTML y animaciones y HyperFrames la vuelve video.
- **[Remotion skills](https://github.com/remotion-dev/skills)**: alternativa basada en React, por si la pides.

Las skills **no vienen copiadas aquí**: se descargan solas de sus repositorios oficiales (`scripts/instalar-skills.sh`) la primera vez que abres el proyecto con Claude Code. Así siempre tienes la versión más nueva.

En `ejemplos/marin-promo/` hay un video de ejemplo (`muestra.mp4`) hecho con esto.

## Opción 1: en la nube (lo más fácil, sin instalar nada)

1. En Claude Code (app o claude.ai/code) abre una **sesión nueva** con el repositorio **Komisa1/freqtrade** y la rama **`videos`**.
2. Pídele, por ejemplo: *"Hazme un video de 15 segundos presentando mi bot Marín, estilo oscuro con verde neón"*.
3. Claude lo crea, lo renderiza y te manda el MP4.

## Opción 2: en tu compu (Windows)

1. Descarga la rama: https://github.com/Komisa1/freqtrade/archive/refs/heads/videos.zip y descomprímela.
2. Abre PowerShell en la carpeta (escribe `powershell` en la barra de arriba del explorador) y corre:
   ```
   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned -Force
   .\instalar.ps1
   ```
   Instala Node.js, FFmpeg y Git (con winget) y descarga las skills.
3. Prueba el ejemplo:
   ```
   cd ejemplos\marin-promo
   npx hyperframes render
   ```
   El video sale en `ejemplos\marin-promo\renders\`.
4. Para videos nuevos, abre **Claude Code** en esta carpeta y pídele lo que quieras con `/hyperframes`.

## Comandos útiles (dentro de la carpeta de un video)

| Comando | Qué hace |
|---|---|
| `npx hyperframes preview` | Abre una vista previa en el navegador |
| `npx hyperframes check` | Revisa que el video no tenga errores |
| `npx hyperframes render` | Genera el MP4 |

## Notas

- HyperFrames manda estadísticas anónimas de uso; en este proyecto están apagadas (`DO_NOT_TRACK=1`).
- Algunas funciones avanzadas (música generada, voces, render en la nube de HeyGen) piden cuenta o API key de terceros. Lo básico (animaciones, textos, gráficas, transiciones) es gratis y local.
