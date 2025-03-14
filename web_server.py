import os
import logging
import threading
import time
import json
from datetime import datetime
from flask import Flask, render_template, jsonify
from flask_cors import CORS
from collections import defaultdict

# Configuración del logger
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('MalenaDJ-WebPanel')

# Crear la aplicación Flask
app = Flask(__name__)
CORS(app)  # Habilitar CORS para todas las rutas

# Deshabilitar los logs de acceso HTTP de Flask
import logging as logging_flask
log = logging_flask.getLogger('werkzeug')
log.setLevel(logging_flask.ERROR)  # Solo mostrar errores, no logs de acceso

# Variables globales para almacenar datos del bot
bot_stats = {
    'servers': [],
    'voice_channels': [],
    'current_songs': {},
    'listeners': defaultdict(list),
    'queue': {},
    'start_time': datetime.now().isoformat(),
    'last_update': datetime.now().isoformat()
}

# Ruta para la página principal
@app.route('/')
def index():
    return render_template('index.html')

# API para obtener estadísticas del bot
@app.route('/api/stats')
def get_stats():
    return jsonify(bot_stats)

# API para obtener logs del bot
@app.route('/api/logs')
def get_logs():
    log_entries = []
    try:
        with open('bot.log', 'r', encoding='utf-8', errors='replace') as log_file:
            # Leer las últimas 100 líneas del archivo de log
            lines = log_file.readlines()
            last_lines = lines[-100:] if len(lines) > 100 else lines
            
            for line in last_lines:
                log_entries.append(line.strip())
    except Exception as e:
        logger.error(f"Error al leer el archivo de log: {str(e)}")
        return jsonify({'error': str(e)})
    
    return jsonify(log_entries)

# Función para actualizar las estadísticas del bot
def update_bot_stats(stats):
    global bot_stats
    bot_stats.update(stats)
    bot_stats['last_update'] = datetime.now().isoformat()

# Función para iniciar el servidor web
def start_web_server(host='127.0.0.1', port=5000):
    logger.info(f"Iniciando servidor web en http://{host}:{port}")
    app.run(host=host, port=port, debug=False, use_reloader=False)

# Función para iniciar el servidor web en un hilo separado
def start_web_server_thread(host='127.0.0.1', port=5000):
    thread = threading.Thread(target=start_web_server, args=(host, port))
    thread.daemon = True  # El hilo se cerrará cuando el programa principal termine
    thread.start()
    return thread

if __name__ == '__main__':
    # Este bloque solo se ejecuta si se inicia este archivo directamente
    # Para pruebas del servidor web sin el bot
    start_web_server()