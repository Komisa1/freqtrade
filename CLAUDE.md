# Proyecto: videos con Claude Code

Este proyecto sirve para crear videos de motion graphics a partir de un prompt.

- Habla siempre en espanol, de forma sencilla: el usuario no es programador.
- Por defecto usa **HyperFrames** (`/hyperframes`). Usa Remotion (`/remotion-best-practices`) solo si el
  usuario lo pide.
- Cada video nuevo va en su propia carpeta dentro de `videos/` (ej. `videos/promo-marin/`), creada con
  `npx hyperframes init <nombre> --non-interactive`.
- Las skills viven en `.claude/skills/` y se descargan solas al iniciar la sesion
  (`scripts/instalar-skills.sh`). Si faltan, corre ese script.
- Sesiones en la nube: el CDN `cdn.jsdelivr.net` puede estar bloqueado. Si el render falla cargando GSAP,
  descarga GSAP con `npm pack gsap@3.14.2`, copia `package/dist/gsap.min.js` a la carpeta del video (y a
  `compositions/` si hay sub-composiciones) y cambia la URL del CDN por `gsap.min.js`.
- Al terminar un render, entrega el MP4 al usuario (en la nube: con la herramienta para enviar archivos).
- No subas los MP4 a git (estan en `.gitignore`).
