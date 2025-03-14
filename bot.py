import os
import discord
from discord.ext import commands
from discord import app_commands
import yt_dlp as youtube_dl  # Reemplazando youtube_dl con yt-dlp
import asyncio
import re
import time
import logging
import lyricsgenius
import pylrc
import aiohttp
import traceback
from dotenv import load_dotenv
from datetime import datetime, timedelta

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log")
    ]
)
logger = logging.getLogger('MalenaDJ')

# Load environment variables from .env file
load_dotenv()

# Configure youtube_dl
ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,  # Cambiado a True para no permitir playlists
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
    'socket_timeout': 10,  # Timeout for socket connections
    'retries': 5  # Number of retries for failed downloads
}

ffmpeg_options = {
    'options': '-vn -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'before_options': '-nostdin -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
}

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)

# Create a custom YTDLSource class
class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        try:
            # Añadir log para depuración
            logger.info(f'Extrayendo información de URL: {url}')
            data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
            
            # Verificar si es una búsqueda o una URL directa
            if 'entries' in data:
                # Es una búsqueda, tomar el primer resultado
                data = data['entries'][0]
                logger.info(f'Búsqueda detectada, usando primer resultado: {data.get("title", "Título desconocido")}')
            else:
                # Es una URL directa
                logger.info(f'Canción individual detectada: {data.get("title", "Título desconocido")}')
                
            filename = data['url'] if stream else ytdl.prepare_filename(data)
            return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)
        except Exception as e:
            logger.error(f'Error al extraer información de URL {url}: {str(e)}')
            raise e

# Create a Music cog for bot commands
class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queue = []
        self.current_song = None
        self.is_playing = False
        self.voice_client = None
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5
        self.last_channel = None
        self.last_interaction = None
        self.lyrics_message = None
        self.lyrics_task = None
        self.start_time = None
        self.genius = None
        self.radio_mode = False  # Variable para controlar el modo radio
        
        # Inicializar el cliente de Genius si hay una API key en .env
        genius_token = os.getenv('GENIUS_TOKEN')
        logger.info(f'Token de Genius encontrado: {bool(genius_token)}')
        if genius_token:
            self.genius = lyricsgenius.Genius(genius_token, timeout=15)  # Aumentar timeout a 15 segundos
            # Configurar el cliente para no mostrar mensajes de estado
            if self.genius:
                self.genius.verbose = False
                self.genius.remove_section_headers = True
                logger.info('Cliente de Genius inicializado correctamente')
        else:
            logger.warning('No se encontró token de Genius, la funcionalidad de letras estará limitada')

    @app_commands.command(name='join', description='Conecta el bot al canal de voz')
    async def join(self, interaction: discord.Interaction):
        logger.info(f'Comando /join ejecutado por {interaction.user.name}')
        if not interaction.user.voice:
            await interaction.response.send_message('Necesitas estar en un canal de voz para usar este comando.')
            logger.warning(f'Usuario {interaction.user.name} intentó usar /join sin estar en un canal de voz')
            return
        
        channel = interaction.user.voice.channel
        self.last_channel = channel  # Save the channel for reconnection purposes
        logger.info(f'Intentando conectar al canal: {channel.name}')
        
        if interaction.guild.voice_client is not None:
            logger.info(f'Moviendo bot de canal a: {channel.name}')
            await interaction.response.send_message(f'Moviendo al canal: {channel.name}')
            return await interaction.guild.voice_client.move_to(channel)
        
        try:
            self.voice_client = await channel.connect()
            await interaction.response.send_message(f'Conectado al canal: {channel.name}')
            logger.info(f'Conectado exitosamente al canal: {channel.name}')
            self.last_interaction = interaction
        except Exception as e:
            await interaction.response.send_message(f'Error al conectar: {str(e)}')
            logger.error(f'Error al conectar al canal {channel.name}: {str(e)}')
            self.voice_client = None

    @app_commands.command(name='leave', description='Desconecta el bot del canal de voz')
    async def leave(self, interaction: discord.Interaction):
        logger.info(f'Comando /leave ejecutado por {interaction.user.name}')
        if interaction.guild.voice_client is not None:
            self.queue = []
            self.is_playing = False
            await interaction.guild.voice_client.disconnect()
            await interaction.response.send_message('Desconectado del canal de voz')
            logger.info(f'Bot desconectado del canal de voz por {interaction.user.name}')
        else:
            await interaction.response.send_message('No estoy conectado a ningún canal de voz')
            logger.warning(f'Usuario {interaction.user.name} intentó usar /leave sin estar conectado a un canal de voz')

    async def play_next(self, interaction=None):
        logger.info('Intentando reproducir la siguiente canción en la cola')
        if len(self.queue) > 0:
            # Save interaction for potential reconnection
            if interaction:
                self.last_interaction = interaction
            
            # Check if voice client is connected
            if self.voice_client is None or not self.voice_client.is_connected():
                logger.warning('Cliente de voz no conectado, intentando reconectar')
                if self.last_channel is not None:
                    try:
                        self.voice_client = await self.last_channel.connect()
                        logger.info(f'Reconectado al canal: {self.last_channel.name}')
                        
                        # Enviar mensaje de reconexión
                        channel = None
                        if self.last_interaction:
                            channel = self.last_interaction.channel
                        
                        if channel:
                            await channel.send(f'Reconectado al canal: {self.last_channel.name}')
                    except Exception as e:
                        logger.error(f'Error al reconectar: {str(e)}')
                        if self.last_interaction and self.last_interaction.channel:
                            await self.last_interaction.channel.send(f'Error al reconectar: {str(e)}')
                        self.is_playing = False
                        return
                else:
                    logger.error('No se puede reproducir: no hay canal para reconectar')
                    if self.last_interaction and self.last_interaction.channel:
                        await self.last_interaction.channel.send('No se puede reproducir: no hay conexión de voz')
                    self.is_playing = False
                    return
            
            self.is_playing = True
            url = self.queue.pop(0)
            logger.info(f'Reproduciendo URL: {url}')
            
            try:
                # Verificar si la URL es una playlist o una canción individual
                player = await YTDLSource.from_url(url, loop=self.bot.loop, stream=True)
                self.current_song = player
                logger.info(f'Canción cargada: {player.title}')
                
                # Usar una función lambda para pasar el canal para mensajes
                channel = None
                if self.last_interaction:
                    channel = self.last_interaction.channel
                
                self.voice_client.play(player, after=lambda e: self.after_playback_callback(e, channel))
                
                if channel:
                    await channel.send(f'Reproduciendo ahora: {player.title}')
                
                # Reset reconnect attempts on successful playback
                self.reconnect_attempts = 0
                logger.info('Reproducción iniciada correctamente')
                
                # Cancelar tarea anterior de letras si existe
                if self.lyrics_task and not self.lyrics_task.cancelled():
                    self.lyrics_task.cancel()
                    logger.info('Tarea anterior de letras cancelada')
                
                # Buscar y mostrar letras para la canción actual
                self.start_time = datetime.now()
                if channel:
                    self.lyrics_task = asyncio.create_task(self.show_lyrics(channel, player.title))
                    logger.info(f'Iniciando búsqueda de letras para: {player.title}')
            except Exception as e:
                logger.error(f'Error al reproducir la canción: {str(e)}')
                if self.last_interaction and self.last_interaction.channel:
                    await self.last_interaction.channel.send(f'Error al reproducir la canción: {str(e)}')
                self.is_playing = False
        else:
            logger.info('Cola de reproducción vacía')
            
            # Si el modo radio está activado, buscar canciones relacionadas
            if self.radio_mode and self.current_song:
                logger.info('Modo radio activado, buscando canciones relacionadas')
                if self.last_interaction and self.last_interaction.channel:
                    await self.last_interaction.channel.send('🔄 Modo radio activado: buscando canciones relacionadas...')
                
                # Buscar canciones relacionadas basadas en la canción actual
                await self.find_related_songs()
                
                # Si se encontraron canciones relacionadas, reproducir la siguiente
                if len(self.queue) > 0:
                    await self.play_next()
                    return
            
            self.is_playing = False
            self.current_song = None
            
            # Cancelar tarea de letras si existe
            if self.lyrics_task and not self.lyrics_task.cancelled():
                self.lyrics_task.cancel()
                logger.info('Tarea de letras cancelada al finalizar la cola')

    async def show_lyrics(self, channel, song_title):
        logger.info(f'Iniciando búsqueda de letras para: {song_title}')
        # Crear un embed inicial para mostrar que se están buscando las letras
        embed = discord.Embed(
            title=f"Buscando letras para: {song_title}",
            description="Buscando letras sincronizadas en LRCLib...",
            color=discord.Color.blue()
        )
        embed.set_footer(text="MalenaDJ - Bot de Música | Las letras pueden ser erróneas o pueden no coincidir con la canción.")
        
        # Enviar el embed inicial
        self.lyrics_message = await channel.send(embed=embed)
        
        # Variables para almacenar resultados
        lyrics = None
        synced_lyrics = None
        is_synced = False
        
        try:
            # Limpiar el título para la búsqueda
            search_title = re.sub(r'\(.*?\)|\[.*?\]|\{.*?\}|ft\..*|feat\..*', '', song_title).strip()
            
            # Extraer posible artista del título (formato común: "Artista - Título")
            artist = None
            track = search_title
            if ' - ' in search_title:
                parts = search_title.split(' - ', 1)
                artist = parts[0].strip()
                track = parts[1].strip()
            
            logger.info(f'Buscando letras para: {artist if artist else ""} - {track}')
            
            # Buscar letras en LRCLib usando el endpoint de búsqueda
            search_url = "https://lrclib.net/api/search"
            search_params = {}
            
            if track:
                search_params['track'] = track
            if artist:
                search_params['artist'] = artist
            
            async with aiohttp.ClientSession() as session:
                # Primero intentar con el endpoint /api/get (método recomendado)
                get_url = "https://lrclib.net/api/get"
                get_params = {
                    "track_name": track,
                    "artist_name": artist if artist else "Unknown"
                }
                
                async with session.get(get_url, params=get_params) as response:
                    logger.info(f'URL de solicitud LRCLib GET: {response.url}')
                    logger.info(f'Estado de respuesta LRCLib GET: {response.status}')
                    
                    if response.status == 200:
                        # Procesar la respuesta exitosa
                        lyrics_data = await response.json()
                        
                        # Verificar si hay letras sincronizadas
                        synced_lyrics_text = lyrics_data.get('syncedLyrics')
                        if synced_lyrics_text:
                            logger.info('Letras sincronizadas encontradas en LRCLib')
                            lyrics = synced_lyrics_text
                            
                            # Parsear letras sincronizadas con pylrc
                            try:
                                synced_lyrics = pylrc.parse(lyrics)
                                is_synced = True
                                logger.info('Letras sincronizadas parseadas correctamente')
                            except Exception as e:
                                logger.error(f"Error al parsear letras sincronizadas: {e}")
                                is_synced = False
                        else:
                            # Si no hay letras sincronizadas, usar letras normales
                            plain_lyrics = lyrics_data.get('plainLyrics')
                            if plain_lyrics:
                                lyrics = plain_lyrics
                                logger.info('Letras planas encontradas en LRCLib')
                                
                                # Crear sincronización aproximada
                                try:
                                    lines = [line for line in lyrics.split('\n') if line.strip()]
                                    
                                    # Estimar duración promedio de 4 segundos por línea
                                    synced_lyrics = []
                                    for i, line in enumerate(lines):
                                        time_seconds = i * 4  # 4 segundos por línea
                                        synced_line = pylrc.LyricLine()
                                        synced_line.time = time_seconds
                                        synced_line.text = line
                                        synced_lyrics.append(synced_line)
                                    
                                    if synced_lyrics:
                                        is_synced = True
                                        logger.info(f'Sincronización aproximada creada con {len(synced_lyrics)} líneas')
                                except Exception as e:
                                    logger.error(f"Error al crear sincronización aproximada: {e}")
                    elif response.status == 404:
                        # Si no se encuentra con el endpoint principal, intentar con búsqueda
                        logger.info('No se encontraron letras con /api/get, intentando con /api/search')
                        
                        # Actualizar el embed para mostrar progreso
                        embed = discord.Embed(
                            title=f"Buscando letras para: {song_title}",
                            description="No se encontraron letras exactas. Buscando alternativas...",
                            color=discord.Color.blue()
                        )
                        embed.set_footer(text="MalenaDJ - Bot de Música | Las letras pueden ser erróneas o pueden no coincidir con la canción")
                        await self.lyrics_message.edit(embed=embed)
                        
                        # Intentar con el endpoint de búsqueda
                        async with session.get(search_url, params=search_params) as search_response:
                            logger.info(f'URL de búsqueda LRCLib: {search_response.url}')
                            logger.info(f'Estado de respuesta búsqueda: {search_response.status}')
                            
                            if search_response.status == 200:
                                results = await search_response.json()
                                
                                if results and len(results) > 0:
                                    # Tomar el primer resultado
                                    result = results[0]
                                    lyrics_id = result.get('id')
                                    
                                    # Obtener letras completas por ID
                                    if lyrics_id:
                                        lyrics_url = f"https://lrclib.net/api/get/{lyrics_id}"
                                        async with session.get(lyrics_url) as lyrics_response:
                                            if lyrics_response.status == 200:
                                                lyrics_data = await lyrics_response.json()
                                                
                                                # Verificar si hay letras sincronizadas
                                                synced_lyrics_text = lyrics_data.get('syncedLyrics')
                                                if synced_lyrics_text:
                                                    logger.info('Letras sincronizadas encontradas en búsqueda LRCLib')
                                                    lyrics = synced_lyrics_text
                                                    
                                                    # Parsear letras sincronizadas con pylrc
                                                    try:
                                                        synced_lyrics = pylrc.parse(lyrics)
                                                        is_synced = True
                                                        logger.info('Letras sincronizadas parseadas correctamente')
                                                    except Exception as e:
                                                        logger.error(f"Error al parsear letras sincronizadas: {e}")
                                                        is_synced = False
                                                else:
                                                    # Si no hay letras sincronizadas, usar letras normales
                                                    plain_lyrics = lyrics_data.get('plainLyrics')
                                                    if plain_lyrics:
                                                        lyrics = plain_lyrics
                                                        logger.info('Letras planas encontradas en búsqueda LRCLib')
            
            # Si no se encontraron letras en LRCLib, intentar con Genius como respaldo
            if not lyrics and self.genius:
                logger.info('No se encontraron letras en LRCLib, intentando con Genius...')
                
                # Actualizar el embed para mostrar que se está buscando en Genius
                embed = discord.Embed(
                    title=f"Buscando letras para: {song_title}",
                    description="No se encontraron letras en LRCLib. Buscando en Genius...",
                    color=discord.Color.blue()
                )
                embed.set_footer(text="MalenaDJ - Bot de Música | Las letras pueden ser erróneas o pueden no coincidir con la canción")
                await self.lyrics_message.edit(embed=embed)
                
                # Buscar la canción en Genius
                song = self.genius.search_song(search_title)
                if song:
                    logger.info(f'Canción encontrada en Genius: {song.title} por {song.artist}')
                    lyrics = song.lyrics
                    
                    # Verificar si las letras tienen formato de tiempo [mm:ss.xx]
                    if lyrics and re.search(r'\[\d{2}:\d{2}\.\d{2}\]', lyrics):
                        # Parece que tenemos letras sincronizadas
                        logger.info('Letras sincronizadas detectadas en Genius')
                        is_synced = True
                        try:
                            # Intentar parsear las letras sincronizadas con pylrc
                            synced_lyrics = pylrc.parse(lyrics)
                            logger.info('Letras sincronizadas parseadas correctamente')
                        except Exception as e:
                            logger.error(f"Error al parsear letras sincronizadas: {e}")
                            is_synced = False
                    else:
                        # Si no hay formato de tiempo, intentar crear una sincronización aproximada
                        logger.info('Creando sincronización aproximada para las letras de Genius')
                        try:
                            # Dividir las letras en líneas y crear un objeto de letras sincronizadas
                            lines = [line for line in lyrics.split('\n') if line.strip() and not line.startswith('[')]
                            
                            # Estimar duración promedio de 4 segundos por línea
                            synced_lyrics = []
                            for i, line in enumerate(lines):
                                time_seconds = i * 4  # 4 segundos por línea
                                synced_line = pylrc.LyricLine()
                                synced_line.time = time_seconds
                                synced_line.text = line
                                synced_lyrics.append(synced_line)
                            
                            if synced_lyrics:
                                is_synced = True
                                logger.info(f'Sincronización aproximada creada con {len(synced_lyrics)} líneas')
                        except Exception as e:
                            logger.error(f"Error al crear sincronización aproximada: {e}")
        except Exception as e:
            logger.error(f"Error al buscar letras: {e}")
        
        # Si no se encontraron letras, mostrar un mensaje
        if not lyrics:
            logger.warning(f'No se encontraron letras para: {song_title}')
            embed = discord.Embed(
                title=f"Letras para: {song_title}",
                description="No se encontraron letras para esta canción.",
                color=discord.Color.red()
            )
            embed.set_footer(text="MalenaDJ - Bot de Música | Las letras pueden ser erróneas o pueden no coincidir con la canción")
            await self.lyrics_message.edit(embed=embed)
            return
        
        # Si tenemos letras sincronizadas, mostrarlas con sincronización
        if is_synced and synced_lyrics:
            logger.info('Iniciando visualización de letras sincronizadas')
            # Iniciar bucle para actualizar las letras según el tiempo
            while self.is_playing and self.current_song and self.current_song.title == song_title:
                try:
                    # Calcular el tiempo transcurrido desde el inicio de la canción
                    elapsed = datetime.now() - self.start_time
                    elapsed_seconds = elapsed.total_seconds()
                    
                    # Encontrar la línea actual basada en el tiempo transcurrido
                    current_line = None
                    next_lines = []
                    prev_lines = []
                    
                    # Buscar la línea actual y las líneas anteriores/siguientes
                    current_index = -1
                    for i, line in enumerate(synced_lyrics):
                        if hasattr(line, 'time') and line.time <= elapsed_seconds:
                            current_line = line
                            current_index = i
                    
                    if current_index >= 0:
                        # Obtener hasta 2 líneas anteriores
                        for j in range(1, 3):
                            if current_index - j >= 0:
                                prev_lines.insert(0, synced_lyrics[current_index - j])
                        
                        # Obtener hasta 5 líneas siguientes
                        for j in range(1, 6):
                            if current_index + j < len(synced_lyrics):
                                next_lines.append(synced_lyrics[current_index + j])
                    
                    # Crear el contenido del embed
                    if current_line:
                        # Formatear las líneas anteriores, la actual y las siguientes
                        prev_text = "\n".join([f"*{line.text}*" for line in prev_lines])
                        
                        # Destacar la línea actual con formato más visible
                        current_text = f"# {current_line.text}"
                        
                        next_text = "\n".join([line.text for line in next_lines])
                        
                        # Construir la descripción completa
                        description = ""
                        if prev_text:
                            description += f"{prev_text}\n\n"
                        description += f"{current_text}\n\n"
                        if next_text:
                            description += next_text
                        
                        # Actualizar el embed con un color que cambia según la posición en la canción
                        # Esto crea un efecto visual interesante que ayuda a identificar el progreso
                        progress_ratio = min(1.0, elapsed_seconds / (len(synced_lyrics) * 4))
                        hue = int(120 * progress_ratio)  # De verde (0) a azul (240)
                        color = discord.Color.from_hsv(hue/360, 0.8, 0.8)
                        
                        embed = discord.Embed(
                            title=f"Letras para: {song_title}",
                            description=description,
                            color=color
                        )
                        
                        # Añadir información de tiempo y progreso
                        minutes = int(elapsed_seconds // 60)
                        seconds = int(elapsed_seconds % 60)
                        embed.set_footer(text=f"Tiempo: {minutes:02d}:{seconds:02d} | MalenaDJ | Las letras pueden ser erróneas o pueden no coincidir con la canción")
                        
                        # Actualizar el mensaje con el nuevo embed
                        await self.lyrics_message.edit(embed=embed)
                    
                    # Esperar antes de la próxima actualización (ajustar según necesidad)
                    await asyncio.sleep(0.5)  # Actualización más frecuente para mejor sincronización
                except Exception as e:
                    logger.error(f"Error al actualizar letras sincronizadas: {e}")
                    break
        else:
            # Si no son letras sincronizadas, mostrar las letras completas
            logger.info('Mostrando letras completas (no sincronizadas)')
            # Dividir las letras en partes si son muy largas para un solo embed
            if lyrics:
                # Limpiar las letras (quitar líneas vacías al principio y final)
                lyrics = lyrics.strip()
                
                # Dividir en partes si es muy largo (los embeds tienen un límite de 4096 caracteres en la descripción)
                if len(lyrics) > 4000:
                    parts = [lyrics[i:i+4000] for i in range(0, len(lyrics), 4000)]
                    
                    for i, part in enumerate(parts):
                        embed = discord.Embed(
                            title=f"Letras para: {song_title} (Parte {i+1}/{len(parts)})",
                            description=part,
                            color=discord.Color.green()
                        )
                        embed.set_footer(text="MalenaDJ - Bot de Música | Las letras pueden ser erróneas o pueden no coincidir con la canción")
                        
                        if i == 0:
                            await self.lyrics_message.edit(embed=embed)
                        else:
                            self.lyrics_message = await ctx.send(embed=embed)
                        
                        # Esperar un poco entre mensajes para no hacer spam
                        if i < len(parts) - 1:
                            await asyncio.sleep(1)
                else:
                    # Si las letras caben en un solo embed
                    embed = discord.Embed(
                        title=f"Letras para: {song_title}",
                        description=lyrics,
                        color=discord.Color.green()
                    )
                    embed.set_footer(text="MalenaDJ - Bot de Música | Las letras pueden ser erróneas o pueden no coincidir con la canción")
                    await self.lyrics_message.edit(embed=embed)
            
    async def attempt_reconnect(self, channel):
        try:
            logger.info('Intentando reconectar al canal de voz')
            if self.last_channel is not None and (self.voice_client is None or not self.voice_client.is_connected()):
                self.voice_client = await self.last_channel.connect()
                logger.info(f'Reconectado exitosamente al canal: {self.last_channel.name}')
                if channel:
                    await channel.send(f'Reconectado al canal: {self.last_channel.name}')
                # If we were playing something, try to resume the queue
                if self.is_playing:
                    logger.info('Reanudando reproducción después de reconexión')
                    await self.play_next()
        except Exception as e:
            logger.error(f'Error al reconectar: {str(e)}')
            if channel:
                await channel.send(f'Error al reconectar: {str(e)}')
            # Wait before next attempt
            await asyncio.sleep(5)

    @app_commands.command(name='play', description='Reproduce música desde YouTube (enlace o nombre)')
    async def play(self, interaction: discord.Interaction, query: str):
        logger.info(f'Comando /play ejecutado por {interaction.user.name} con query: {query}')
        # Join voice channel if not already connected
        if interaction.guild.voice_client is None:
            if interaction.user.voice:
                logger.info(f'Conectando al canal de voz: {interaction.user.voice.channel.name}')
                self.voice_client = await interaction.user.voice.channel.connect()
                self.last_channel = interaction.user.voice.channel
            else:
                logger.warning(f'Usuario {interaction.user.name} intentó usar /play sin estar en un canal de voz')
                await interaction.response.send_message('Necesitas estar en un canal de voz para usar este comando.')
                return
        
        # Add song to queue
        await interaction.response.send_message(f'Buscando: {query}')
        
        try:
            # Verificar si es una playlist
            if 'list=' in query or 'playlist' in query.lower():
                logger.warning(f'Usuario {interaction.user.name} intentó reproducir una playlist: {query}')
                await interaction.followup.send('⚠️ Lo siento, MalenaDJ no soporta la reproducción de playlists. Por favor, proporciona el enlace de una canción individual.')
                return
            
            # Procesar como canción individual
            result = await YTDLSource.from_url(query, loop=self.bot.loop, stream=True)
            
            # Es una canción individual
            logger.info(f'Añadiendo a la cola: {query}')
            self.queue.append(query)
            await interaction.followup.send(f'Añadido a la cola: {result.title}')
            
            # Start playing if not already playing
            if not self.is_playing:
                logger.info('Iniciando reproducción')
                await self.play_next(interaction)
        except Exception as e:
            logger.error(f'Error al procesar la solicitud: {str(e)}')
            await interaction.followup.send(f'Error al procesar la solicitud: {str(e)}')
            return

    @app_commands.command(name='pause', description='Pausa la reproducción actual')
    async def pause(self, interaction: discord.Interaction):
        logger.info(f'Comando /pause ejecutado por {interaction.user.name}')
        if interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
            interaction.guild.voice_client.pause()
            await interaction.response.send_message('Reproducción pausada')
            logger.info('Reproducción pausada')
        else:
            await interaction.response.send_message('No hay nada reproduciéndose actualmente')
            logger.warning('Intento de pausar sin reproducción activa')

    @app_commands.command(name='resume', description='Reanuda la reproducción pausada')
    async def resume(self, interaction: discord.Interaction):
        logger.info(f'Comando /resume ejecutado por {interaction.user.name}')
        if interaction.guild.voice_client and interaction.guild.voice_client.is_paused():
            interaction.guild.voice_client.resume()
            await interaction.response.send_message('Reproducción reanudada')
            logger.info('Reproducción reanudada')
        else:
            await interaction.response.send_message('La reproducción no está pausada')
            logger.warning('Intento de reanudar sin reproducción pausada')

    @app_commands.command(name='skip', description='Salta a la siguiente canción en la cola')
    async def skip(self, interaction: discord.Interaction):
        logger.info(f'Comando /skip ejecutado por {interaction.user.name}')
        if interaction.guild.voice_client and (interaction.guild.voice_client.is_playing() or interaction.guild.voice_client.is_paused()):
            interaction.guild.voice_client.stop()
            await interaction.response.send_message('Saltando a la siguiente canción...')
            logger.info('Saltando a la siguiente canción')
        else:
            await interaction.response.send_message('No hay nada reproduciéndose actualmente')
            logger.warning('Intento de saltar sin reproducción activa')

    @app_commands.command(name='queue', description='Muestra la cola de reproducción')
    async def show_queue(self, interaction: discord.Interaction):
        logger.info(f'Comando /queue ejecutado por {interaction.user.name}')
        if len(self.queue) == 0:
            await interaction.response.send_message('La cola está vacía')
            logger.info('Cola de reproducción vacía')
            return
        
        queue_list = '\n'.join([f'{i+1}. {song}' for i, song in enumerate(self.queue)])
        await interaction.response.send_message(f'**Cola de reproducción:**\n{queue_list}')
        logger.info(f'Mostrando cola con {len(self.queue)} elementos')

    @app_commands.command(name='now', description='Muestra la canción que se está reproduciendo')
    async def now_playing(self, interaction: discord.Interaction):
        logger.info(f'Comando /now ejecutado por {interaction.user.name}')
        if self.current_song:
            await interaction.response.send_message(f'**Reproduciendo ahora:** {self.current_song.title}')
            logger.info(f'Mostrando canción actual: {self.current_song.title}')
        else:
            await interaction.response.send_message('No hay nada reproduciéndose actualmente')
            logger.warning('Intento de mostrar canción actual sin reproducción activa')

    @app_commands.command(name='clear', description='Limpia la cola de reproducción')
    async def clear_queue(self, interaction: discord.Interaction):
        logger.info(f'Comando /clear ejecutado por {interaction.user.name}')
        self.queue = []
        await interaction.response.send_message('Cola de reproducción limpiada')
        logger.info('Cola de reproducción limpiada')

    @app_commands.command(name='volume', description='Ajusta o muestra el volumen actual (0-100)')
    async def volume(self, interaction: discord.Interaction, nivel: int = None):
        logger.info(f'Comando /volume ejecutado por {interaction.user.name}')
        
        if interaction.guild.voice_client is None:
            await interaction.response.send_message('No estoy conectado a un canal de voz')
            logger.warning(f'Usuario {interaction.user.name} intentó usar /volume sin estar conectado a un canal de voz')
            return
            
        if self.current_song is None:
            await interaction.response.send_message('No hay nada reproduciéndose actualmente')
            logger.warning(f'Usuario {interaction.user.name} intentó usar /volume sin reproducción activa')
            return
            
        # Si no se proporciona nivel, mostrar el volumen actual
        if nivel is None:
            current_volume = int(self.current_song.volume * 100)
            await interaction.response.send_message(f'Volumen actual: {current_volume}%')
            logger.info(f'Mostrando volumen actual: {current_volume}%')
            return
            
        # Validar que el nivel esté entre 0 y 100
        if nivel < 0 or nivel > 100:
            await interaction.response.send_message('El volumen debe estar entre 0 y 100')
            logger.warning(f'Usuario {interaction.user.name} intentó establecer un volumen fuera de rango: {nivel}')
            return
            
        # Ajustar el volumen (convertir de 0-100 a 0.0-1.0)
        self.current_song.volume = nivel / 100
        await interaction.response.send_message(f'Volumen ajustado a: {nivel}%')
        logger.info(f'Volumen ajustado a: {nivel}%')

    @app_commands.command(name='radio', description='Activa/desactiva el modo radio (reproducción automática de canciones relacionadas)')  
    async def radio(self, interaction: discord.Interaction):
        logger.info(f'Comando /radio ejecutado por {interaction.user.name}')
        
        # Cambiar el estado del modo radio
        self.radio_mode = not self.radio_mode
        
        if self.radio_mode:
            await interaction.response.send_message('🔄 Modo radio activado. Se reproducirán automáticamente canciones relacionadas cuando la cola esté vacía.')
            logger.info(f'Modo radio activado por {interaction.user.name}')
        else:
            await interaction.response.send_message('⏹️ Modo radio desactivado. La reproducción se detendrá cuando la cola esté vacía.')
            logger.info(f'Modo radio desactivado por {interaction.user.name}')

    async def find_related_songs(self, reference_song=None):
        try:
            # Usar la canción de referencia proporcionada o la actual
            song_to_use = reference_song or self.current_song
            
            if not song_to_use:
                logger.warning('No hay canción actual para buscar relacionadas')
                return
                
            # Extraer el título e ID de la canción para buscar relacionadas
            song_title = song_to_use.title
            logger.info(f'Buscando canciones relacionadas a: {song_title}')
            
            # Intentar obtener el ID del video de YouTube de la URL o datos
            video_id = None
            if hasattr(song_to_use, 'data') and 'id' in song_to_use.data:
                video_id = song_to_use.data['id']
            elif hasattr(song_to_use, 'data') and 'webpage_url' in song_to_use.data:
                # Extraer ID de la URL si está disponible
                url = song_to_use.data['webpage_url']
                if 'youtube.com/watch?v=' in url:
                    video_id = url.split('watch?v=')[1].split('&')[0]
                elif 'youtu.be/' in url:
                    video_id = url.split('youtu.be/')[1].split('?')[0]
            
            # Extraer información del artista y título para búsquedas alternativas
            artist = None
            track = song_title
            if ' - ' in song_title:
                parts = song_title.split(' - ', 1)
                artist = parts[0].strip()
                track = parts[1].strip()
            
            # Limpiar el título para búsquedas más efectivas
            clean_track = re.sub(r'\(.*?\)|\[.*?\]|\{.*?\}|ft\..*|feat\..*', '', track).strip()
            
            # Intentar primero con el ID del video
            entries = []
            if video_id:
                # Usar el ID del video para buscar videos relacionados
                search_query = f'https://www.youtube.com/watch?v={video_id}'
                logger.info(f'Buscando videos relacionados al ID: {video_id}')
                
                # Configurar opciones para buscar canciones relacionadas
                search_options = ytdl_format_options.copy()
                search_options['default_search'] = 'auto'
                search_options['noplaylist'] = False  # Permitir resultados tipo playlist
                search_options['extract_flat'] = True  # Solo extraer información básica
                search_options['quiet'] = True
                
                # Crear un nuevo extractor para esta búsqueda
                with youtube_dl.YoutubeDL(search_options) as ydl:
                    # Configurar para obtener videos relacionados
                    info = await self.bot.loop.run_in_executor(
                        None, lambda: ydl.extract_info(search_query, download=False, process=True)
                    )
                    
                    # Verificar si hay videos relacionados
                    if info and 'entries' in info:
                        entries = info['entries']
                    elif info and 'recommended_entries' in info:
                        entries = info['recommended_entries']
            
            # Si no se encontraron entradas con el ID, intentar búsquedas alternativas
            if not entries:
                logger.info('No se encontraron videos relacionados por ID, intentando búsquedas alternativas')
                
                # Lista de búsquedas alternativas a intentar
                search_queries = []
                
                # 1. Buscar por artista + "música similar"
                if artist:
                    search_queries.append(f'ytsearch5:{artist} música similar')
                    search_queries.append(f'ytsearch5:{artist} canciones populares')
                
                # 2. Buscar por género si se puede extraer del título
                genre_keywords = ['pop', 'rock', 'reggaeton', 'trap', 'hip hop', 'rap', 'electrónica', 'dance']
                detected_genre = None
                for genre in genre_keywords:
                    if genre.lower() in song_title.lower():
                        detected_genre = genre
                        break
                
                if detected_genre:
                    search_queries.append(f'ytsearch5:{detected_genre} top hits')
                    if artist:
                        search_queries.append(f'ytsearch5:{detected_genre} similar to {artist}')
                
                # 3. Buscar por título limpio
                search_queries.append(f'ytsearch5:{clean_track} similar music')
                
                # 4. Búsqueda genérica si todo lo demás falla
                search_queries.append('ytsearch5:top hits music 2024')
                
                # Configurar opciones para búsquedas alternativas
                search_options = ytdl_format_options.copy()
                search_options['default_search'] = 'auto'
                search_options['extract_flat'] = True
                search_options['quiet'] = True
                
                # Intentar cada búsqueda alternativa hasta encontrar resultados
                for query in search_queries:
                    if entries:  # Si ya encontramos entradas, salir del bucle
                        break
                        
                    logger.info(f'Intentando búsqueda alternativa: {query}')
                    with youtube_dl.YoutubeDL(search_options) as ydl:
                        try:
                            info = await self.bot.loop.run_in_executor(
                                None, lambda: ydl.extract_info(query, download=False, process=True)
                            )
                            
                            if info and 'entries' in info:
                                entries = info['entries']
                                logger.info(f'Encontradas {len(entries)} entradas con búsqueda alternativa')
                        except Exception as search_error:
                            logger.warning(f'Error en búsqueda alternativa: {str(search_error)}')
                            continue
            
            if not entries:
                logger.warning('No se encontraron canciones relacionadas después de intentar múltiples búsquedas')
                return
                
            # Filtrar y añadir hasta 5 canciones relacionadas a la cola
            related_count = 0
            for entry in entries:
                if related_count >= 5:  # Limitar a 5 canciones relacionadas
                    break
                    
                if 'id' in entry and entry.get('title'):
                    video_url = f"https://www.youtube.com/watch?v={entry['id']}"
                    self.queue.append(video_url)
                    related_count += 1
                    logger.info(f'Añadida canción relacionada a la cola: {entry.get("title")}')
                    
            if related_count > 0:
                logger.info(f'Se añadieron {related_count} canciones relacionadas a la cola')
                if self.last_interaction and self.last_interaction.channel:
                    await self.last_interaction.channel.send(f'🎵 Modo radio: se añadieron {related_count} canciones relacionadas a la cola')
            else:
                logger.warning('No se pudieron añadir canciones relacionadas')
        except Exception as e:
            logger.error(f'Error al buscar canciones relacionadas: {str(e)}')
            if self.last_interaction and self.last_interaction.channel:
                await self.last_interaction.channel.send(f'⚠️ Error en modo radio: {str(e)}')

    def after_playback_callback(self, error, channel):
        # Esta función es llamada por discord.py cuando termina la reproducción
        # Ejecuta la coroutine handle_playback_error en el event loop
        if self.bot and self.bot.loop:
            # Corregido: pasar la coroutine como objeto, no su resultado
            coro = self.handle_playback_error(error, channel)
            asyncio.run_coroutine_threadsafe(coro, self.bot.loop)
        else:
            logger.error('No se pudo ejecutar handle_playback_error: bot.loop no disponible')

    async def handle_playback_error(self, error, channel):
        # Registrar el error o finalización en el log
        if error:
            logger.error(f'Error durante la reproducción: {str(error)}')
            if channel:
                await channel.send(f'⚠️ Error durante la reproducción: {str(error)}')
        else:
            # Si no hay error, significa que la canción terminó normalmente
            logger.info('Reproducción finalizada correctamente')
        
        # Guardar el estado actual antes de cambiar a la siguiente canción
        current_song_title = None
        if self.current_song:
            current_song_title = self.current_song.title
        
        # Continuar con la siguiente canción
        self.is_playing = False
        
        # Si hay canciones en la cola, reproducir la siguiente
        if len(self.queue) > 0:
            logger.info(f'Reproduciendo siguiente canción en cola ({len(self.queue)} restantes)')
            await self.play_next()
        # Si la cola está vacía y el modo radio está activado, intentar encontrar canciones relacionadas
        elif self.radio_mode and self.current_song:
            logger.info('Cola vacía con modo radio activado, buscando canciones relacionadas')
            if channel:
                await channel.send('🔄 Modo radio: buscando canciones relacionadas...')
            
            # Guardar la canción actual antes de buscar relacionadas
            related_to = self.current_song
            self.current_song = None  # Evitar referencias circulares
            
            await self.find_related_songs(related_to)
            
            # Si se encontraron canciones, reproducir la siguiente
            if len(self.queue) > 0:
                await self.play_next()
            else:
                logger.info('No se encontraron canciones relacionadas en modo radio')
                if channel:
                    await channel.send('⚠️ No se encontraron canciones relacionadas')
        else:
            logger.info('Reproducción finalizada, no hay más canciones en cola')
            self.current_song = None
            
            # Cancelar tarea de letras si existe
            if self.lyrics_task and not self.lyrics_task.cancelled():
                self.lyrics_task.cancel()
                logger.info('Tarea de letras cancelada al finalizar reproducción')

    @app_commands.command(name='help', description='Muestra la lista de comandos disponibles y su uso')
    async def help_command(self, interaction: discord.Interaction):
        logger.info(f'Comando /help ejecutado por {interaction.user.name}')
        
        embed = discord.Embed(
            title="MalenaDJ - Comandos Disponibles",
            description="Aquí tienes la lista de todos los comandos disponibles",
            color=discord.Color.blue()
        )
        
        # Añadir cada comando con su descripción
        embed.add_field(name="/join", value="Conecta el bot al canal de voz donde estás", inline=False)
        embed.add_field(name="/leave", value="Desconecta el bot del canal de voz", inline=False)
        embed.add_field(name="/play [canción]", value="Reproduce música desde YouTube (enlace o nombre)", inline=False)
        embed.add_field(name="/pause", value="Pausa la reproducción actual", inline=False)
        embed.add_field(name="/resume", value="Reanuda la reproducción pausada", inline=False)
        embed.add_field(name="/skip", value="Salta a la siguiente canción en la cola", inline=False)
        embed.add_field(name="/queue", value="Muestra la cola de reproducción", inline=False)
        embed.add_field(name="/now", value="Muestra la canción que se está reproduciendo", inline=False)
        embed.add_field(name="/clear", value="Limpia la cola de reproducción", inline=False)
        embed.add_field(name="/volume [nivel]", value="Ajusta o muestra el volumen actual (0-100)", inline=False)
        embed.add_field(name="/radio", value="Activa/desactiva el modo radio (reproducción automática de canciones relacionadas)", inline=False)
        
        # Añadir información adicional
        embed.set_footer(text="MalenaDJ - Bot de Música | Desarrollado por TheNestorHD con ♥")
        
        await interaction.response.send_message(embed=embed)
        logger.info('Comando /help ejecutado correctamente')

# Set up the bot
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='/', intents=intents)

# Importar el módulo del servidor web
import web_server

@bot.event
async def on_ready():
    logger.info(f'Bot conectado como {bot.user}')
    
    # Añadir el cog de música
    music_cog = Music(bot)
    await bot.add_cog(music_cog)
    
    # Sincronizar los comandos de barra con Discord
    try:
        logger.info('Sincronizando comandos de barra con Discord...')
        synced = await bot.tree.sync()
        logger.info(f'Sincronizados {len(synced)} comandos de barra')
    except Exception as e:
        logger.error(f'Error al sincronizar comandos: {str(e)}')
    
    # Iniciar el servidor web en un hilo separado
    logger.info('Iniciando servidor web para panel de control...')
    web_server.start_web_server_thread()
    logger.info('Servidor web iniciado en http://127.0.0.1:5000')
    
    # Iniciar el bucle de actualización de estadísticas
    bot.loop.create_task(update_stats_loop(music_cog))

# Función para actualizar las estadísticas del bot periódicamente
async def update_stats_loop(music_cog):
    while True:
        try:
            # Recopilar información de servidores
            servers = []
            voice_channels = []
            current_songs = {}
            listeners = {}
            queue = {}
            
            for guild in bot.guilds:
                # Información del servidor
                server_info = {
                    'id': str(guild.id),
                    'name': guild.name,
                    'member_count': guild.member_count
                }
                servers.append(server_info)
                
                # Información de canales de voz
                if guild.voice_client and guild.voice_client.is_connected():
                    channel = guild.voice_client.channel
                    voice_channel_info = {
                        'id': str(channel.id),
                        'name': channel.name,
                        'server_id': str(guild.id),
                        'server_name': guild.name
                    }
                    voice_channels.append(voice_channel_info)
                    
                    # Información de oyentes
                    channel_listeners = []
                    for member in channel.members:
                        if not member.bot:
                            listener_info = {
                                'id': str(member.id),
                                'name': member.name,
                                'display_name': member.display_name
                            }
                            channel_listeners.append(listener_info)
                    
                    listeners[str(channel.id)] = channel_listeners
                    
                    # Información de canción actual
                    # Información de canción actual
                    if music_cog.is_playing and music_cog.current_song and music_cog.voice_client and music_cog.voice_client.guild.id == guild.id:
                        current_songs[str(guild.id)] = {
                            'title': music_cog.current_song.title,
                            'url': music_cog.current_song.url,
                            'radio_mode': music_cog.radio_mode  # Añadir información del modo radio
                        }
                    
                    # Información de cola
                    if hasattr(music_cog, 'queue') and music_cog.queue:
                        queue[str(guild.id)] = music_cog.queue.copy()
            
            # Actualizar estadísticas en el servidor web
            stats = {
                'servers': servers,
                'voice_channels': voice_channels,
                'current_songs': current_songs,
                'listeners': listeners,
                'queue': queue
            }
            web_server.update_bot_stats(stats)
            
        except Exception as e:
            logger.error(f'Error al actualizar estadísticas: {str(e)}')
        
        # Actualizar cada 5 segundos
        await asyncio.sleep(5)

@bot.event
async def on_voice_state_update(member, before, after):
    # Obtener el cog de música
    music_cog = bot.get_cog('Music')
    if not music_cog:
        return
        
    # Caso 1: El bot es desconectado manualmente
    if member.id == bot.user.id and before.channel is not None and after.channel is None:
        logger.warning(f'Bot desconectado del canal de voz: {before.channel.name}')
        # Intentar reconectar si fue una desconexión inesperada
        if music_cog and music_cog.is_playing and music_cog.last_channel:
            # Esperar un momento antes de intentar reconectar
            await asyncio.sleep(2)
            try:
                # Intentar reconectar al canal
                music_cog.voice_client = await music_cog.last_channel.connect()
                logger.info(f'Reconectado al canal: {music_cog.last_channel.name} después de una desconexión inesperada')
                
                # Enviar mensaje de reconexión si hay un canal disponible
                if music_cog.last_interaction and music_cog.last_interaction.channel:
                    await music_cog.last_interaction.channel.send(
                        f'Reconectado al canal: {music_cog.last_channel.name} después de una desconexión inesperada'
                    )
                
                # Continuar reproduciendo si estaba reproduciendo algo
                await music_cog.play_next()
            except Exception as e:
                logger.error(f'Error al reconectar después de desconexión inesperada: {str(e)}')
                if music_cog.last_interaction and music_cog.last_interaction.channel:
                    await music_cog.last_interaction.channel.send(
                        f'Error al reconectar después de desconexión inesperada: {str(e)}'
                    )
    
    # Caso 2: Verificar si el bot se quedó solo en el canal
    if after.channel is not None and music_cog.voice_client is not None and music_cog.voice_client.channel == after.channel:
        # Contar miembros que no son bots en el canal
        members = [m for m in after.channel.members if not m.bot]
        
        if len(members) == 0:
            # El bot está solo en el canal, programar desconexión después de 30 segundos
            logger.info(f'Bot solo en el canal {after.channel.name}, programando desconexión en 30 segundos')
            
            # Enviar mensaje si hay un canal disponible
            if music_cog.last_interaction and music_cog.last_interaction.channel:
                await music_cog.last_interaction.channel.send(
                    f'Me quedaré en el canal por 30 segundos más. Si nadie se une, me desconectaré para ahorrar recursos.'
                )
            
            # Esperar 30 segundos
            await asyncio.sleep(30)
            
            # Verificar nuevamente si sigue solo (por si alguien se unió durante la espera)
            current_channel = music_cog.voice_client.channel if music_cog.voice_client else None
            if current_channel:
                members = [m for m in current_channel.members if not m.bot]
                if len(members) == 0:
                    # Sigue solo, desconectar
                    logger.info(f'Bot sigue solo después de 30 segundos, desconectando del canal {current_channel.name}')
                    
                    # Limpiar la cola y detener reproducción
                    music_cog.queue = []
                    music_cog.is_playing = False
                    await music_cog.voice_client.disconnect()
                    
                    # Enviar mensaje de desconexión
                    if music_cog.last_interaction and music_cog.last_interaction.channel:
                        await music_cog.last_interaction.channel.send(
                            'Me he desconectado automáticamente después de 30 segundos solo en el canal.'
                        )
                else:
                    logger.info(f'Alguien se unió al canal durante la espera, cancelando desconexión automática')

# Run the bot
bot.run(os.getenv('DISCORD_TOKEN'))