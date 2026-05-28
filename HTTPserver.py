from http.server import SimpleHTTPRequestHandler, HTTPServer
import socket
import threading
import os

class AlphaMiniHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=os.getcwd(), **kwargs)


def get_ip_local():
    """
    Obtiene la dirección IP local de la máquina.
    Intenta conectarse a un servidor externo para obtener la IP,
    si falla, devuelve '127.0.0.1'.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80)) # Conexión a un servidor externo (Google DNS)
            return s.getsockname()[0] # Devuelve la IP local usada para la conexión
    except Exception:
        return "127.0.0.1" # IP de fallback si no se puede determinar la IP local


async def start_http_server(server_instance):
    """
    Inicia el servidor HTTP en un hilo separado para no bloquear el hilo principal.
    Integra el servidor con la instancia principal del servidor AlphaMini.

    Args:
        server_instance: La instancia de la clase ServidorAlphaMini que contiene
                         los atributos necesarios (port, http_server, ip_local).
    Returns:
        La dirección IP local en la que se ha iniciado el servidor.
    """
    ip = get_ip_local()
    port = server_instance.port

    handler = AlphaMiniHandler
    # Se crea el servidor HTTP y se asigna a ServidorAlphaMini
    server_instance.http_server = HTTPServer((ip, port), handler)
    server_instance.ip_local = ip

    server_thread = threading.Thread(
        target=server_instance.http_server.serve_forever,
        daemon=True
    )

    server_thread.start()
    print(f"Servidor HTTP iniciado en http://{ip}:{port}")
    return ip