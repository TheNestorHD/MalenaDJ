# MalenaDJ - Bot de Música para Discord

MalenaDJ es un bot de Discord escrito en Python que permite reproducir música desde YouTube en canales de voz. El bot puede reproducir música a partir de enlaces de YouTube o mediante búsqueda por nombre de canción, y ahora incluye sincronización de letras en tiempo real.

## Características

- Reproducción de música desde YouTube (enlaces o búsqueda por nombre)
- Sistema de cola de reproducción
- Controles básicos (pausa, reanudar, saltar)
- Visualización de la cola y canción actual
- Sincronización de letras en tiempo real (usando LRCLib, no requiere token de API)
- Comandos de barra (slash commands) para mejor integración con Discord
- Sistema de reconexión automática en caso de desconexiones
- Desconexión automática después de 30 segundos solo en el canal
- Logging detallado para facilitar la depuración
- Modo radio para reproducción automática de canciones similares
- Detección de playlists con mensaje informativo

## Requisitos

- Python 3.8 o superior
- FFmpeg instalado en el sistema o en la carpeta raíz del proyecto
- Token de bot de Discord
- Ya no se requiere token de Genius API (ahora usamos LRCLib para letras sincronizadas)
- Las siguientes bibliotecas de Python:
  - discord.py[voice]
  - yt-dlp (reemplazo mejorado de youtube_dl)
  - python-dotenv
  - lyricsgenius (para búsqueda de letras)
  - pylrc (para sincronización de letras)

## Instalación

1. Clona este repositorio o descarga los archivos

2. Instala las dependencias necesarias:

```bash
pip install discord.py[voice] yt-dlp python-dotenv lyricsgenius pylrc
```

3. Instala FFmpeg:
   - **Windows**: Descarga desde [ffmpeg.org](https://ffmpeg.org/download.html) y añade a PATH
   - **Linux**: `sudo apt install ffmpeg`
   - **macOS**: `brew install ffmpeg`

4. Configura tu token de Discord:
    - Cambia el nombre del archivo .env.example a .env
   - Crea un bot en [Discord Developer Portal](https://discord.com/developers/applications)
   - Copia el token del bot
   - Crea un archivo `.env` con el siguiente contenido:
     ```
     DISCORD_TOKEN=tu_token_de_discord_aquí
     ```

5. (Opcional) Configura un token de Genius API para búsqueda de letras:
   - Regístrate en [Genius](https://genius.com/signup)
   - Ve a [Genius API Clients](https://genius.com/api-clients)
   - Crea una nueva API Client (aplicación)
   - Copia el "Client Access Token"
   - Añade el token a tu archivo `.env`:
     ```
     GENIUS_TOKEN=tu_token_de_genius_aquí
     ```
   - Nota: Aunque el bot usa principalmente LRCLib para letras sincronizadas, Genius se usa como respaldo cuando no se encuentran letras en LRCLib.

6. Invita al bot a tu servidor:
   - En el portal de desarrolladores, ve a OAuth2 > URL Generator
   - Selecciona los scopes: `bot` y `applications.commands`
   - Permisos de bot: `Connect`, `Speak`, `Send Messages`, `Read Message History`
   - Usa la URL generada para invitar al bot a tu servidor

## Uso

Ejecuta el bot con:

```bash
python bot.py
```

## Comandos

Todos los comandos utilizan el formato de comandos de barra (`/`)

| Comando | Descripción | Uso |
|---------|-------------|-----|
| `/join` | Conecta el bot al canal de voz | `/join` |
| `/leave` | Desconecta el bot del canal de voz | `/leave` |
| `/play` | Reproduce música desde YouTube | `/play https://www.youtube.com/watch?v=dQw4w9WgXcQ` o `/play nombre de la canción` |
| `/pause` | Pausa la reproducción actual | `/pause` |
| `/resume` | Reanuda la reproducción pausada | `/resume` |
| `/skip` | Salta a la siguiente canción en la cola | `/skip` |
| `/queue` | Muestra la cola de reproducción | `/queue` |
| `/now` | Muestra la canción que se está reproduciendo | `/now` |
| `/clear` | Limpia la cola de reproducción | `/clear` |
| `/volume` | Ajusta o muestra el volumen actual | `/volume` o `/volume 50` |
| `/radio` | Activa/desactiva el modo radio | `/radio` |
| `/help` | Muestra la lista de comandos disponibles | `/help` |

## Funcionalidad de Letras

El bot busca automáticamente las letras de las canciones que reproduzcas utilizando LRCLib. Si encuentra letras sincronizadas (con marcas de tiempo), las mostrará en tiempo real a medida que avanza la canción.

## Desconexión Automática

El bot se desconectará automáticamente después de 30 segundos si se queda solo en un canal de voz (sin usuarios humanos), ahorrando recursos del servidor.

## Solución de problemas

- **El bot no reproduce audio**: Asegúrate de tener FFmpeg instalado correctamente
- **Errores de conexión**: Verifica que el token en el archivo `.env` sea correcto
- **Problemas con yt-dlp**: Si encuentras errores relacionados con yt-dlp, intenta actualizarlo con `pip install --upgrade yt-dlp`
- **No aparecen letras**: Verifica tu conexión a internet, ya que el bot necesita acceder a LRCLib para obtener las letras sincronizadas
- **Desconexiones frecuentes**: El bot intentará reconectarse automáticamente, pero si persisten los problemas, verifica tu conexión a internet

## Notas

- El bot utiliza streaming para reproducir música, lo que significa que no descarga los archivos completos
- Algunas canciones o videos pueden no ser reproducibles debido a restricciones de YouTube
- La sincronización de letras puede no ser perfecta para todas las canciones, especialmente si no se encuentran letras sincronizadas en LRCLib

## Licencia

Este proyecto está disponible bajo la licencia MIT.

## Modo Radio

El bot incluye un modo radio que permite la reproducción automática de canciones similares cuando la cola está vacía. Para activar o desactivar esta función:

1. Usa el comando `/radio` para alternar el estado del modo radio
2. Cuando está activado, el bot buscará automáticamente canciones relacionadas con la última canción reproducida
3. Se añadirán hasta 3 canciones similares a la cola de reproducción
4. El modo radio permanecerá activo hasta que lo desactives manualmente

Esta función es ideal para mantener la música sonando sin tener que añadir canciones manualmente constantemente.

## Detección de Playlists

El bot detecta automáticamente cuando se intenta reproducir una playlist de YouTube y muestra un mensaje informativo indicando que esta funcionalidad no está soportada. Para reproducir música, debes proporcionar enlaces a canciones individuales o nombres de canciones para búsqueda.