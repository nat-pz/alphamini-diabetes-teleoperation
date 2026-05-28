import asyncio
from dotenv import load_dotenv
import HTTPserver
from AccionesAlphaMini import AccionesAlphaMini
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import json

robot_id = "20256"


# Proxy para el simulador
class ProxyHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _set_headers(self):
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()

    def do_OPTIONS(self):
        self._set_headers()

    def do_GET(self):
        self._set_headers()
        self.wfile.write(b'{"mensaje": "hola"}')

    def do_POST(self):
        try:
            content_len = int(self.headers.get('content-length', 0))
            post_body = self.rfile.read(content_len)
            json_data = json.loads(post_body.decode('utf-8'))

            print(
                f"\n[Proxy] Comando recibido: {json_data.get('accion', 'N/A')} (Glucosa: {json_data.get('glucosa', 'N/A')})")

            if hasattr(self.server, 'servidor_principal'):
                self.server.servidor_principal.agregar_comando_simulador(json_data)
                self._set_headers()
                self.wfile.write(json.dumps({"status": "OK", "message": "Comando recibido"}).encode('utf-8'))
            else:
                self._set_headers()
                self.wfile.write(json.dumps({"status": "ERROR", "message": "Servidor no disponible"}).encode('utf-8'))

        except Exception as e:
            print(f"\n[Proxy ERROR] Error procesando comando del simulador: {e}")
            self._set_headers()
            error_response = json.dumps({"status": "ERROR", "message": str(e)})
            self.wfile.write(error_response.encode('utf-8'))


# Estados de la máquina
class EstadoGlucosa:
    INICIAL = "inicial"
    NORMAL = "normal"
    HIPO = "hipoglucemia"
    HIPER = "hiperglucemia"


# Manejador del robot
class ServidorAlphaMini:
    def __init__(self, language: str):
        load_dotenv("keys.env")
        self.port = 9090
        self.local_ip = None
        self.http_server = None
        self.idioma = language

        # Variables para el simulador-------------
        self.modo_simulador = False
        self.servidor_proxy = None
        self.puerto_proxy = 8000
        self.cola_comandos = asyncio.Queue()
        self.estado_glucosa_actual = EstadoGlucosa.INICIAL

        self.url_get_glucosa = "http://localhost:8000/get_glucosa"
        self.url_receptor_comandos = "http://localhost:8000/com_simulador"  # Cambiar si se usa otro puerto o IP
        self.valor_glucosa = None  # Almacenar el ultimo valor de glucosa
        self.tarea_monitoreo_glucosa = None
        self.intervalo_monitoreo = 5
        # ----------------------------------------
        self.ac = AccionesAlphaMini(self.port, self.url_get_glucosa, self.url_receptor_comandos, self.idioma)
        self.robot_connected = False

    async def initialize(self):
        try:
            self.local_ip = await HTTPserver.start_http_server(self)
            self.ac.ip_local = self.local_ip
            await self.ac.inicializar()
            #await self.ac.iniciar_chat()

            if self.robot_connected:
                welcome_msg = "Módulo iniciado correctamente." if self.idioma == 'es' else "Module started correctly."
                await self.ac.generar_y_reproducir_audio(welcome_msg)
            return True
        except Exception as e:
            print(f"Error en inicialización: {e}")
            return False

    async def connect_to_robot(self, robot_name: str) -> bool:
        success = await self.ac.conectar_a_robot(robot_name)
        self.robot_connected = success
        return success

    def agregar_comando_simulador(self, comando):
        """Agregar comando del simulador a la cola"""
        try:
            self.cola_comandos.put_nowait(comando)
        except asyncio.QueueFull:
            print("Cola de comandos llena, descartando comando")

    async def procesar_comandos_simulador(self):
        """Procesar comandos del simulador en el loop principal"""
        while self.modo_simulador:
            try:
                comando = await asyncio.wait_for(self.cola_comandos.get(), timeout=0.1)
                await self._ejecutar_comando_simulador(comando)
                self.cola_comandos.task_done()
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                print(f"Error procesando comando del simulador: {e}")

    async def _ejecutar_comando_simulador(self, data):
        try:
            accion = data.get("accion", "")
            print(f"Ejecutando comando '{accion}' del simulador...")

            if accion == "hablar":
                texto = data.get("data", "")
                print(f"Robot hablando: {texto}")
                await self.ac.generar_y_reproducir_audio(texto)

            elif accion == "medir_glucosa":
                glucosa_val = data.get("glucosa")
                if glucosa_val is not None:
                    try:
                        glucosa_int = int(glucosa_val)
                        print(f"Robot midiendo glucosa: {glucosa_int}")
                        await self._transicionar_estado_glucosa(glucosa_int)
                    except ValueError as e:
                        print(f"Error: valor de glucosa no es numérico: {glucosa_val}")
                else:
                    print("Comando medir_glucosa recibido sin valor de glucosa.")

            elif accion == "ejercicio":
                print("Robot haciendo ejercicio")
                await self.ac.deporte()

            elif accion == "comer":
                print("Robot simulando acción de comer.")
                await self.ac.generar_y_reproducir_audio("Voy a comer algo")

            else:
                print(f"Acción no reconocida: {accion}")

        except Exception as e:
            print(f"Error ejecutando comando '{accion}': {e}")

    async def _transicionar_estado_glucosa(self, glucosa: int):
        nuevo_estado = None
        if glucosa < 70:
            nuevo_estado = EstadoGlucosa.HIPO
        elif 70 <= glucosa <= 180:
            nuevo_estado = EstadoGlucosa.NORMAL
        else:  # glucosa > 180
            nuevo_estado = EstadoGlucosa.HIPER

        if nuevo_estado != self.estado_glucosa_actual:
            estado_anterior = self.estado_glucosa_actual
            self.estado_glucosa_actual = nuevo_estado

            if estado_anterior == EstadoGlucosa.INICIAL:
                print(f"Primera medición de glucosa: {glucosa} -> Estado: {self.estado_glucosa_actual}")
            else:
                print(f"Transición de estado de glucosa: {estado_anterior} -> {self.estado_glucosa_actual}")
            if self.robot_connected:
                await self._ejecutar_accion_por_estado_glucosa()
            else:
                print("=Robot no conectado, no se ejecutan acciones por estado de glucosa.")

    async def _ejecutar_accion_por_estado_glucosa(self):
        if self.estado_glucosa_actual == EstadoGlucosa.INICIAL:
            print("Estado inicial - esperando primera medición de glucosa")
        elif self.estado_glucosa_actual == EstadoGlucosa.HIPO:
            print("Nivel bajo - ejecutando acción hipoglucemia")
            if self.robot_connected:
                await self.ac.hipo()
        elif self.estado_glucosa_actual == EstadoGlucosa.NORMAL:
            print("Nivel normal - ejecutando acción normal")
            if self.robot_connected:
                await self.ac.normal()
        elif self.estado_glucosa_actual == EstadoGlucosa.HIPER:
            print("Nivel alto - ejecutando acción hiperglucemia")
            if self.robot_connected:
                await self.ac.hiper()

    async def _bucle_monitoreo_glucosa(self):
        print(
            f"Iniciando monitoreo periódico de glucosa desde {self.url_get_glucosa} cada {self.intervalo_monitoreo}s...")
        while self.modo_simulador:
            try:
                glucose = await self.ac.get_glucosa_simulador()
                if glucose is not None:
                    await self._transicionar_estado_glucosa(int(glucose))
            except asyncio.CancelledError:
                print("Tarea de monitoreo de glucosa cancelada.")
                break
            except Exception as e:
                print(f"Error inesperado en el bucle de monitoreo: {e}")
            await asyncio.sleep(self.intervalo_monitoreo)
        print("Bucle de monitoreo de glucosa finalizado.")

    async def iniciar_simulador(self):
        if self.modo_simulador:
            print("El simulador ya está activo")
            return True

        try:
            print("\nIniciando modo simulador...")

            # Iniciar servidor proxy
            self.servidor_proxy = HTTPServer((self.local_ip, self.puerto_proxy), ProxyHandler)
            self.servidor_proxy.servidor_principal = self  # Referencia al servidor principal

            # Ejecutar en hilo separado
            hilo_proxy = threading.Thread(target=self.servidor_proxy.serve_forever, daemon=True)
            hilo_proxy.start()

            self.modo_simulador = True

            self.tarea_monitoreo_glucosa = asyncio.create_task(self._bucle_monitoreo_glucosa())

            print("\n" + "=" * 60)
            print("PROXY SIMULADOR INICIADO")
            print("=" * 60)
            print(f"IP a configurar como robot en simulador: {self.local_ip}")
            print(f"Puerto: {self.puerto_proxy}\n")
            print("Para detener el simulador, escribe 'salirsim' en la consola")
            print("=" * 60)

            return True

        except Exception as e:
            print(f"Error al iniciar simulador: {e}")
            await self.detener_simulador()
            return False

    async def detener_simulador(self):
        if not self.modo_simulador:
            print("El simulador no está activo")
            return

        try:
            print("\nDeteniendo simulador...")

            if self.tarea_monitoreo_glucosa and not self.tarea_monitoreo_glucosa.done():
                self.tarea_monitoreo_glucosa.cancel()
                try:
                    await self.tarea_monitoreo_glucosa
                except asyncio.CancelledError:
                    pass
                print("Monitoreo de glucosa detenido.")
            self.tarea_monitoreo_glucosa = None

            self.modo_simulador = False

            if self.servidor_proxy:
                await asyncio.get_event_loop().run_in_executor(None, self.servidor_proxy.shutdown)
                self.servidor_proxy = None

            while not self.cola_comandos.empty():
                try:
                    self.cola_comandos.get_nowait()
                    self.cola_comandos.task_done()
                except asyncio.QueueEmpty:
                    break

            print("Simulador detenido correctamente")

        except Exception as e:
            print(f"Error al detener simulador: {e}")

    async def controlar_simulador(self, modo):
        """Controlar simulador por voz o texto"""
        if modo == 'v':  # controlar por voz
            if not self.ac.vosk_disponible:
                print("ERROR: Reconocimiento de voz no disponible")
                return False
            print("Escuchando comando para el simulador...")
            print("Ejemplos: 'comer 20', 'inyectar 5', 'ejercicio 30'")
            exito, texto = await self.ac.escuchar()
            if not exito:
                print("No se pudo reconocer el audio")
                return False

            print(f"Comando reconocido: {texto}")
            resultado = await self.ac.procesar_comando_simulador(texto)
            if not resultado:
                print("No se reconoció como comando de simulador")
                return False
            return True

        elif modo == 't':  # controlar por texto
            print("Introduce el comando para el simulador:")
            print("Ejemplos: 'comer 20', 'inyectar 5', 'ejercicio 30'")
            try:
                comando = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: input("Comando: ").strip()
                )
                if not comando:
                    print("Comando vacío")
                    return False

                print(f"Procesando comando: {comando}")
                resultado = await self.ac.procesar_comando_simulador(comando)
                if not resultado:
                    print("No se reconoció como comando de simulador")
                    return False
                return True
            except Exception as e:
                print(f"Error leyendo comando: {e}")
                return False
        else:
            print("Modo no válido. Usa 'v' para voz o 't' para texto")
            return False

    async def shutdown(self):
        if self.modo_simulador:
            await self.detener_simulador()

        if self.robot_connected:
            await self.ac.shutdown()
            self.robot_connected = False

        if self.http_server:
            print("Cerrando servidor HTTP...")
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self.http_server.shutdown)
            print("Servidor HTTP cerrado")

    # MODOS CONTINUOS
    async def modo_texto_continuo(self):
        exit_command = 'salir' if self.idioma == 'es' else 'exit'

        print("\n" + "=" * 60)
        if self.idioma == 'es':
            print("MODO TEXTO CONTINUO ACTIVADO")
            print("Escribe mensajes para hablar con Andy")
            print(f"Escribe '{exit_command}' para salir del modo texto")
        else:
            print("CONTINUOUS TEXT MODE ACTIVATED")
            print("Type messages to talk to Andy")
            print(f"Type '{exit_command}' to exit text mode")
        print("=" * 60)
        await self.ac.iniciar_chat()

        while True:
            try:
                prompt = "\nTú: " if self.idioma == 'es' else "\nYou: "
                mensaje = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: input(prompt).strip()
                )

                if mensaje.lower() == exit_command:
                    print("\nSaliendo del modo texto..." if self.idioma == 'es' else "\nExiting text mode...")
                    break

                if mensaje:
                    await self.ac.enviar_mensaje_y_reproducir_respuesta(mensaje)
                    await asyncio.sleep(0.2)

            except EOFError:
                print(
                    "\n\nEOF detectado. Saliendo del modo texto..." if self.idioma == 'es' else "\n\nEOF detected. Exiting text mode...")
                break
            except Exception as e:
                print(f"Error en modo texto: {e}")
                await asyncio.sleep(0.1)

        print(
            "Modo texto desactivado. Volviendo al menú principal." if self.idioma == 'es' else "Text mode deactivated. Returning to main menu.")

    async def modo_voz_continuo(self):
        comandosalir = 'salir' if self.idioma == 'es' else 'exit'

        print("\n" + "=" * 60)
        if self.idioma == 'es':
            print("MODO VOZ CONTINUO ACTIVADO")
            print("Habla con Andy usando el micrófono")
            print(f"Di '{comandosalir}' para salir del modo voz")
        else:
            print("CONTINUOUS VOICE MODE ACTIVATED")
            print("Speak to Andy using the microphone")
            print(f"Say '{comandosalir}' to exit voice mode")
        print("=" * 60)

        if not self.ac.vosk_disponible:
            msj_error = "Lo siento, el reconocimiento de voz no está disponible." if self.idioma == 'es' else "Sorry, voice recognition is not available."
            print(f"ERROR: {msj_error}")
            if self.robot_connected:
                await self.ac.generar_y_reproducir_audio(msj_error)
            return

        msg_inicio = "Modo de voz activado. Puedes hablar conmigo ahora." if self.idioma == 'es' else "Voice mode activated. You can talk to me now."
        if self.robot_connected:
            await self.ac.generar_y_reproducir_audio(msg_inicio)

        while True:
            try:
                print(
                    f"(Di '{comandosalir}' para terminar)" if self.idioma == 'es' else f"(Say '{comandosalir}' to finish)")

                exito, texto = await self.ac.escuchar()

                if not exito:
                    msj_error = "No te he entendido, ¿puedes repetir?" if self.idioma == 'es' else "I didn't understand, can you repeat?"
                    print(msj_error)
                    if self.robot_connected:
                        await self.ac.generar_y_reproducir_audio(msj_error)
                    continue

                prompt = "Tú" if self.idioma == 'es' else "You"
                print(f"{prompt}: {texto}")

                if texto.lower() == comandosalir:
                    msj_salir = "Adiós, hasta pronto!" if self.idioma == 'es' else "Goodbye, see you soon!"
                    print("\nSaliendo del modo voz..." if self.idioma == 'es' else "\nExiting voice mode...")
                    if self.robot_connected:
                        await self.ac.generar_y_reproducir_audio(msj_salir)
                    break

                await self.ac.enviar_mensaje_y_reproducir_respuesta(texto)
                await asyncio.sleep(0.5)

            except Exception as e:
                msj_error = "Ha ocurrido un error, pero continuamos." if self.idioma == 'es' else "An error has occurred, but we continue."
                print(f"Error en modo voz: {e}")
                if self.robot_connected:
                    await self.ac.generar_y_reproducir_audio(msj_error)
                await asyncio.sleep(1)

        print(
            "Modo voz desactivado. Volviendo al menú principal." if self.idioma == 'es' else "Voice mode deactivated. Returning to main menu.")


def mostrar_ayuda(language):
    print("\n" + "=" * 60)
    if language == 'es':
        print("Comandos disponibles:")
        print("  texto              - Chat de texto continuo con el robot (escribe 'salir' para finalizar)")
        print("  voz                - Reconocimiento de voz continuo (di 'salir' para finalizar)")
        print("  caminar [x]        - Hacer caminar al robot x pasos")
        print("  saludo [1-4]       - Ejecutar saludo (tipos 1-4)")
        print("  accion             - Ejecutar accion SDK Alpha Mini")
        print("  expresion          - Ejecutar expresion SDK Alpha Mini")
        print("  hipo               - Ejecutar estado hipoglucemia")
        print("  normal             - Ejecutar estado normoglucemia")
        print("  hiper              - Ejecutar estado hiperglucemia")
        print("  deporte            - Hacer ejercicio")
        print("  simular            - Iniciar modo simulador")
        print("  salirsim           - Detener modo simulador")
        print("  ayuda              - Lista de comandos disponibles")
        print("  salir              - Cerrar aplicación")
        print("\nNOTA: Para salir de los modos 'texto' y 'voz', usa el comando 'salir'.")
    else:
        print("Available commands:")
        print("  text               - Continuous text chat with the robot (type 'exit' to finish)")
        print("  voice              - Continuous voice recognition (say 'exit' to finish)")
        print("  walk [x]           - Make the robot walk x steps")
        print("  greet [1-4]        - Execute greeting (types 1-4), Spanish only")
        print("  action             - Execute Alpha Mini SDK action")
        print("  expression         - Execute Alpha Mini SDK expression")
        print("  hipo               - Execute hypoglycemia state, Spanish only")
        print("  normal             - Execute normoglycemia state, Spanish only")
        print("  hiper              - Execute hyperglycemia state, Spanish only")
        print("  exercise           - Perform an exercise action")
        print("  simulate           - Start simulator mode")
        print("  exitsim            - Stop simulator mode")
        print("  help               - List available commands")
        print("  exit               - Close application")
        print("\nNOTE: To exit 'text' and 'voice' modes, use the 'exit' command.")
    print("=" * 60)


async def main():
    while True:
        lang_choice = input("Selecciona el idioma / Select language (es/en): ").lower().strip()
        if lang_choice in ['es', 'en']:
            break
        else:
            print("Opción no válida. Por favor, escribe 'es' para español o 'en' para inglés.")
            print("Invalid option. Please type 'es' for Spanish or 'en' for English.")

    servidor = ServidorAlphaMini(language=lang_choice)

    try:
        if not await servidor.initialize():
            print("Error inicializando el programa")
            return

        robot_connected = await servidor.connect_to_robot(robot_id)
        if not robot_connected:
            print("Sin conexión al robot" if servidor.idioma == 'es' else "No connection to the robot")
        else:
            print("Robot conectado correctamente" if servidor.idioma == 'es' else "Robot connected successfully")

        print("\n" + "=" * 60)
        print("APLICACION ALPHA MINI")
        print(f"Idioma seleccionado: {servidor.idioma.upper()}")
        print("=" * 60)

        mostrar_ayuda(servidor.idioma)

        tarea_simulador = None

        while True:
            if servidor.modo_simulador:
                if tarea_simulador is None or tarea_simulador.done():
                    tarea_simulador = asyncio.create_task(servidor.procesar_comandos_simulador())

            try:
                prompt_text = "\nComando: " if servidor.idioma == 'es' else "\nCommand: "
                if servidor.modo_simulador:
                    prompt_text = "\n[SIMULADOR ACTIVO] Comando [salirsim/salir]: " if servidor.idioma == 'es' else "\n[SIMULATOR ACTIVE] Command [exitsim/exit]: "

                comando = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: input(prompt_text).strip().lower()
                )
            except Exception as e:
                print(f"Error leyendo input: {e}")
                await asyncio.sleep(0.1)
                continue

            # Comando de salida
            if comando in ['salir', 'exit']:
                print("Cerrando aplicación..." if servidor.idioma == 'es' else "Closing application...")
                break

            # Coamndos del simulador
            elif comando in ['simular', 'simulate']:
                if servidor.modo_simulador:
                    print("El simulador ya está activo" if servidor.idioma == 'es' else "Simulator is already active")
                else:
                    await servidor.iniciar_simulador()
                continue
            elif comando in ['salirsim', 'exitsim']:
                if servidor.modo_simulador:
                    await servidor.detener_simulador()
                else:
                    print("El simulador no está activo" if servidor.idioma == 'es' else "Simulator is not active")
                continue

            # Block other commands if simulator is active
            elif servidor.modo_simulador:
                print(
                    "Modo simulador activo. Comandos limitados." if servidor.idioma == 'es' else "Simulator mode active. Limited commands.")
                continue

            # Comandos principales
            elif comando in ['texto', 'text']:
                await servidor.modo_texto_continuo()

            elif comando in ['voz', 'voice']:
                await servidor.modo_voz_continuo()

            elif comando.startswith(('saludo', 'greet')):
                try:
                    partes = comando.split()
                    num_saludo = 1  # valor por defecto
                    if len(partes) > 1:
                        num_saludo = int(partes[1])
                        if num_saludo < 1 or num_saludo > 4:
                            print("Número de saludo debe ser entre 1 y 4. Usando saludo 1." if servidor.idioma == 'es' else "Greeting number must be between 1 and 4. Using greeting 1.")
                            num_saludo = 1

                    if not servidor.robot_connected:
                        print("Intentando reconectar al robot..." if servidor.idioma == 'es' else "Reconnecting to robot...")
                        servidor.robot_connected = await servidor.connect_to_robot(robot_id)
                        if not servidor.robot_connected:
                            print("No se pudo conectar al robot para saludar" if servidor.idioma == 'es' else "Couldn't connect to the robot for greeting")
                            continue

                    print(f"Ejecutando saludo tipo {num_saludo}..." if servidor.idioma == 'es' else f"Executing greeting {num_saludo}")
                    exito = await servidor.ac.saludar(num_saludo)
                    if not exito:
                        print("No se pudo completar el comando de saludar" if servidor.idioma == 'es' else "Couldn't finish greet command")

                except ValueError:
                    print("El número de saludo debe ser un valor entre 1 y 4" if servidor.idioma == 'es' else "Greeting must be between 1 and 4")
                except Exception as e:
                    print(f"Error al procesar el comando saludar: {e}" if servidor.idioma == 'es' else f"Error processing greet command: {e}")

            elif comando.startswith(('caminar', 'walk')):
                try:
                    partes = comando.split()
                    if len(partes) != 2:
                        print("Formato incorrecto. Uso: caminar [número de pasos]" if servidor.idioma == 'es' else "Incorrect format. Usage: walk [number of steps]")
                        continue

                    pasos = int(partes[1])

                    if not servidor.robot_connected:
                        print("Intentando reconectar al robot..." if servidor.idioma == 'es' else "Trying to reconnect to robot...")
                        servidor.robot_connected = await servidor.connect_to_robot(robot_id)
                        if not servidor.robot_connected:
                            print("No se pudo conectar al robot para caminar" if servidor.idioma == 'es' else "Couln't connect to the robot for walking")
                            continue

                    print(f"Robot caminando {pasos} pasos..." if servidor.idioma == 'es' else f"Robot walking {pasos} steps...")
                    exito = await servidor.ac.caminar(pasos)
                    if not exito:
                        print("No se pudo completar el comando de caminar" if servidor.idioma == 'es' else "Couldn't finish walk command")

                except ValueError:
                    print("El número de pasos debe ser un valor numérico" if servidor.idioma == 'es' else "Number of steps must be a number")
                except Exception as e:
                    print(f"Error al procesar el comando caminar: {e}" if servidor.idioma == 'es' else f"Error processing walk command: {e}")

            elif comando.startswith(("accion", "action")):
                try:
                    partes = comando.split()
                    if len(partes) < 2:
                        print("Formato incorrecto. Uso: accion [tipo]" if servidor.idioma == 'es' else "Incorrect format. Usage: action [type]")
                        continue

                    tipo_accion = partes[1]

                    if not servidor.robot_connected:
                        print("Intentando reconectar al robot..." if servidor.idioma == 'es' else "Trying to reconnect to robot...")
                        servidor.robot_connected = await servidor.connect_to_robot(robot_id)
                        if not servidor.robot_connected:
                            print("No se pudo conectar al robot para ejecutar acción" if servidor.idioma == 'es' else "Couln't connect to the robot for executing action")
                            continue

                    print(f"Ejecutando acción: {tipo_accion}..." if servidor.idioma == 'es' else f"Executing action {tipo_accion}...")
                    exito = await servidor.ac.accion_mini(tipo_accion)
                    if not exito:
                        print("No se pudo completar el comando de acción" if servidor.idioma == 'es' else "Couln't complete action command")

                except Exception as e:
                    print(f"Error al procesar el comando acción: {e}" if servidor.idioma == 'es' else f"Error processing action command: {e}")

            elif comando.startswith(("expresion","expression")):
                try:
                    partes = comando.split()
                    if len(partes) < 2:
                        print("Formato incorrecto. Uso: expresion [tipo]" if servidor.idioma == 'es' else "Incorrect format. Usage: expression [type]")
                        continue

                    tipo_expresion = partes[1]

                    if not servidor.robot_connected:
                        print("Intentando reconectar al robot..." if servidor.idioma == 'es' else "Trying to reconnect to robot...")
                        servidor.robot_connected = await servidor.connect_to_robot(robot_id)
                        if not servidor.robot_connected:
                            print("No se pudo conectar al robot para mostrar expresión" if servidor.idioma == 'es' else "Couln't connect to the robot for executing expression")
                            continue

                    print(f"Mostrando expresión: {tipo_expresion}..." if servidor.idioma == 'es' else f"Displaying expression {tipo_expresion}")
                    exito = await servidor.ac.expresion_facial(tipo_expresion)
                    if not exito:
                        print("No se pudo completar el comando de expresión" if servidor.idioma == 'es' else "Couln't complete expression command")

                except Exception as e:
                    print(f"Error al procesar el comando expresion: {e}" if servidor.idioma == 'es' else f"Error processing expression command: {e}")

            elif comando == "hipo":
                if not servidor.robot_connected:
                    print("Intentando reconectar al robot...")
                    servidor.robot_connected = await servidor.connect_to_robot(robot_id)
                print("Estado: hipoglucemia")
                await servidor.ac.hipo()
                print("Ejecución terminada")

            elif comando == "hiper":
                if not servidor.robot_connected:
                    print("Intentando reconectar al robot...")
                    servidor.robot_connected = await servidor.connect_to_robot(robot_id)
                print("Estado: hiperglucemia")
                await servidor.ac.hiper()
                print("Ejecución terminada")

            elif comando == "normal":
                if not servidor.robot_connected:
                    print("Intentando reconectar al robot...")
                    servidor.robot_connected = await servidor.connect_to_robot(robot_id)
                print("Estado: normal")
                await servidor.ac.normal()
                print("Ejecución terminada")

            elif comando in ["deporte","exercise"]:
                if not servidor.robot_connected:
                    print("Intentando reconectar al robot...")
                    servidor.robot_connected = await servidor.connect_to_robot(robot_id)
                print("Ejecutando ejercicio")
                await servidor.ac.deporte()
                print("Ejecución terminada")

            elif comando in ['ayuda', 'help']:
                mostrar_ayuda(servidor.idioma)

            else:
                print("Comando no reconocido." if servidor.idioma == 'es' else "Command not recognized.")

    except KeyboardInterrupt:
        print("\nInterrupción detectada...")
    except Exception as e:
        print(f"Error inesperado: {e}")
    finally:
        if 'tarea_simulador' in locals() and tarea_simulador and not tarea_simulador.done():
            tarea_simulador.cancel()

        print(
            "Iniciando proceso de apagado..." if 'servidor' in locals() and servidor.idioma == 'es' else "Initiating shutdown process...")
        await servidor.shutdown()
        print(
            "Sistema apagado correctamente" if 'servidor' in locals() and servidor.idioma == 'es' else "System shut down correctly")


if __name__ == "__main__":
    asyncio.run(main())