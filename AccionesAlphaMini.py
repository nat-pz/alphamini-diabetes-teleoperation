import asyncio
import mini
import vosk
from mini.apis.api_action import MoveRobot, MoveRobotDirection, MoveRobotResponse
from mini.apis.api_expression import PlayExpression, PlayExpressionResponse
from mini.apis.api_expression import ControlMouthLamp, ControlMouthResponse
from mini.apis.api_expression import SetMouthLamp, SetMouthLampResponse, MouthLampColor, MouthLampMode
from mini.apis.api_sound import StartPlayTTS
import time
import os
from google import genai
from mini.apis.api_sound import PlayAudio
from mini.apis.api_action import PlayAction
import socket
from gtts import gTTS
from mini import AudioStorageType, MiniApiResultType
import speech_recognition as sr
from vosk import Model, KaldiRecognizer
import json
import wave
import re
import aiohttp

NUMEROS_PALABRAS = {
    'cero': 0, 'uno': 1, 'dos': 2, 'tres': 3, 'cuatro': 4, 'cinco': 5,
    'seis': 6, 'siete': 7, 'ocho': 8, 'nueve': 9, 'diez': 10,
    'once': 11, 'doce': 12, 'trece': 13, 'catorce': 14, 'quince': 15,
    'dieciséis': 16, 'diecisiete': 17, 'dieciocho': 18, 'diecinueve': 19,
    'veinte': 20, 'veintiuno': 21, 'veintidós': 22, 'veintitrés': 23, 'veinticuatro': 24,
    'veinticinco': 25, 'veintiséis': 26, 'veintisiete': 27, 'veintiocho': 28, 'veintinueve': 29,
    'treinta': 30, 'cuarenta': 40, 'cincuenta': 50, 'sesenta': 60, 'setenta': 70,
    'ochenta': 80, 'noventa': 90, 'cien': 100
}

class AccionesAlphaMini:
    def __init__(self, port: int, url_get_glucosa: str, url_receptor_comandos: str, language: str = 'es'):
        self.client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
        self.chat = None
        self.language = language
        self.port = port
        self._tts_lock = asyncio.Lock()
        self._stt_lock = asyncio.Lock()
        self.esta_moviendose = False
        self.esta_hablando = False
        self.esta_escuchando = False
        self.device = None
        self.http_server = None
        self.ip_local = None
        self.ruta_modelo_vosk_es = "vosk-model-small-es-0.42"
        self.ruta_modelo_vosk_en = "vosk-model-small-en-us-0.15"  # English Vosk model path
        self.tiempo_escucha = 5000  # 5 segundos
        self.vosk_disponible = False
        # para simulador web
        self.url_get_glucosa = url_get_glucosa
        self.url_receptor_comandos = url_receptor_comandos



    #
    #   GENERAL
    #


    async def inicializar(self):
        if not self.ip_local:
            self.ip_local = await self.get_ip_local()

        self.vosk_disponible = self._verificar_modelo_vosk()
        vosk.SetLogLevel(-1) # comentar si se quiere ver logs de Vosk

        return True

    async def shutdown(self):
        try:
            if self.device:
                await mini.quit_program()
                await mini.release()
                print("Conexión con robot cerrada correctamente")
                self.device = None
        except Exception as e:
            print(f"Error al desconectar: {e}")

    async def get_ip_local(self) -> str:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                return s.getsockname()[0]
        except Exception:
            return "127.0.0.1"

    async def conectar_a_robot(self, robot_name: str, timeout: int = 10) -> bool:
        mini.set_robot_type(mini.RobotType.MINI)
        print(f"Buscando el robot Alpha Mini: {robot_name}...")

        try:
            self.device = await mini.get_device_by_name(robot_name, timeout)
            if not self.device:
                print("Robot no encontrado")
                return False

            if not await mini.connect(self.device):
                print("Fallo en la conexión")
                return False

            if not await mini.enter_program():
                print("No se pudo entrar en modo programa")
                return False

            print("Conectado al robot")
            return True

        except Exception as e:
            print(f"Error de conexión: {e}")
            self.device = None
            return False


    #
    #   GESTIÓN CHATBOT Y TTS
    #

    async def generar_y_reproducir_audio(self, texto: str) -> bool:
        if not texto.strip():
            return False
        if not self.device:
            print("Robot no conectado")
            return False

        async with self._tts_lock:
            try:
                if self.language == 'en':
                    # TTS del SDK para inglés
                    block = StartPlayTTS(text=texto, is_serial=True)
                    (result_type, response) = await block.execute()

                    if result_type == MiniApiResultType.Success and response and response.isSuccess:
                        print("TTS playback successful.")
                        await asyncio.sleep(0.5)
                        return True
                    else:
                        error_code = response.resultCode if response and hasattr(response, 'resultCode') else 'N/A'
                        print(f"TTS playback failed. ResultType: {result_type}. Error code: {error_code}")
                        return False
                else:
                    # gTTS para castellano
                    nombre_archivo_audio = ""
                    try:
                        timestamp = int(time.time())
                        nombre_archivo_audio = f"tts_{timestamp}.mp3"

                        loop = asyncio.get_event_loop()
                        await loop.run_in_executor(
                            None,
                            lambda: gTTS(text=texto, lang=self.language).save(nombre_archivo_audio))

                        if not os.path.exists(nombre_archivo_audio):
                            raise FileNotFoundError("Archivo de audio no generado")

                        return await self._reproducir_audio_gtts(nombre_archivo_audio)

                    finally:
                        if nombre_archivo_audio and os.path.exists(nombre_archivo_audio):
                            try:
                                os.remove(nombre_archivo_audio)
                            except Exception as e:
                                print(f"Error eliminando archivo de audio: {e}")
            except Exception as e:
                print(f"Error en audio: {e}")
                return False

    async def iniciar_chat(self):
        if self.chat:
            print(
                f"Advertencia: El chat de Gemini ({self.language}) ya estaba inicializado. Reiniciando con nuevo contexto.")
        else:
            self.chat = self.client.chats.create(model='gemini-2.0-flash')

        if self.language == 'es':
            contexto = "Tu nombre es Andy, eres un niño que tiene 10 años, tiene diabetes y le encanta jugar al fútbol, películas, videojuegos y jugar con sus amigos. Contesta con una frase o dos, 50 tokens o menos. Nunca más de 200. Tu forma de hablar debe de ser como la de un dibujo animado infantil, como un personaje de una película de Disney. Por ejemplo, como Pepa Pig. Habla sobre tu diabetes. Pregunta al usuario sobre la diabetes. Bajo ninguna circunstancia digas que eres un robot de texto, ni nada parecido, haces lo que podria hacer un niño con diabetes. Nunca uses emoticonos."
        else:  # en
            contexto = "Your name is Andy, you are a 10-year-old child with diabetes who loves to play soccer, watch movies, play video games, and play with your friends. Answer with a sentence or two, 50 tokens or less. Never more than 200. Your way of speaking should be like that of a children's cartoon character, like a character from a Disney movie. For example, like Peppa Pig. Talk about your diabetes. Ask the user about diabetes. Under no circumstances say that you are a text robot, or anything like that, you do what a child with diabetes could do. Never use emojis."


        loop = asyncio.get_event_loop()

        resp = await loop.run_in_executor(
            None,
            lambda: self.chat.send_message(
                message=contexto
            )

        )
        respuesta = resp.text

        # Mostrar respuesta antes de reproducir audio
        print(f"Andy ({self.language.upper()}): {respuesta}")
        await self.generar_y_reproducir_audio(respuesta)

    async def _reproducir_audio_gtts(self, filename: str) -> bool:
        """
        Reproduce un archivo de audio local en el robot (para gTTS).
        """
        try:
            url = f"http://{self.ip_local}:{self.port}/{filename}"
            print(f"Reproduciendo: {url}")

            block = PlayAudio(url=url, is_serial=True)

            try:
                import os
                try:
                    file_size = os.path.getsize(filename)
                    estimated_duration = max(10, (file_size / 10000) + 5)  # mínimo 10 segundos
                except:
                    estimated_duration = 15  # fallback a 15 segundos

                print(f"Esperando reproducción de audio (timeout: {estimated_duration:.0f}s)...")
                (result_type, response_obj) = await asyncio.wait_for(block.execute(), timeout=estimated_duration)

                if result_type == MiniApiResultType.Success and response_obj and response_obj.isSuccess:
                    print(f"Audio reproducido correctamente.")
                    await asyncio.sleep(0.5)
                    return True
                else:
                    error_code = response_obj.resultCode if response_obj and hasattr(response_obj, 'resultCode') else 'N/A'
                    print(f"Reproducción de audio fallida. ResultType: {result_type}. Código de error: {error_code}")
                    return False

            except asyncio.TimeoutError:
                print(f"Timeout al reproducir audio '{filename}' después de {estimated_duration:.0f}s.")
                return False
            except Exception as inner_e:
                print(f"Error durante la ejecución de PlayAudio para '{filename}': {inner_e}")
                return False

        except Exception as e:
            print(f"Error general al intentar reproducir audio '{filename}': {e}")
            return False


    async def enviar_mensaje_y_reproducir_respuesta(self, mensaje: str) -> str:
        """Envía mensaje a Gemini, genera y reproduce audio de respuesta"""
        if not mensaje.strip():
            return "No se ha recibido ningún mensaje" if self.language == 'es' else "No message received"

        if not self.chat:
            error_msg = f"ERROR: El chat de Gemini ({self.language}) no ha sido inicializado. Intentando inicializar ahora."
            print(error_msg)
            await self.iniciar_chat()

            if not self.chat:
                return "Lo siento, no puedo procesar tu mensaje. El sistema de chat no está disponible." if self.language == 'es' else "Sorry, I can't process your message. The chat system is unavailable."

        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self.chat.send_message(mensaje)
            )
            respuesta = response.text

            # Mostrar respuesta antes de reproducir audio
            print(f"Andy ({self.language.upper()}): {respuesta}")

            if self.device:
                # Reproducir audio y esperar a que termine
                audio_success = await self.generar_y_reproducir_audio(respuesta)
                if audio_success:
                    print("Audio completado.")
                else:
                    print("Error al reproducir audio.")
            else:
                print("Robot no conectado - solo texto.")

            return respuesta

        except Exception as e:
            print(f"Error en Gemini: {e}")
            return "Ha ocurrido un error al procesar el mensaje" if self.language == 'es' else "An error occurred while processing the message"


    #
    # RECONOCIMIENTO DE VOZ
    #

    def _verificar_modelo_vosk(self):
        """Verificar si modelo Vosk está disponible y completo"""
        ruta_modelo = self.ruta_modelo_vosk_es if self.language == 'es' else self.ruta_modelo_vosk_en
        try:
            import vosk

            if not os.path.exists(ruta_modelo):
                print(f"AVISO: Modelo Vosk ({self.language}) no encontrado en {ruta_modelo}")
                return False

            required_files = ["am", "conf", "graph", "ivector"]
            if not all(os.path.exists(os.path.join(ruta_modelo, f)) for f in required_files):
                print(f"AVISO: Modelo Vosk ({self.language}) incompleto en {ruta_modelo}")
                return False

            print(f"Modelo Vosk ({self.language}) encontrado en {ruta_modelo}")
            return True
        except ImportError:
            print(f"AVISO: Módulo Vosk no instalado para el idioma '{self.language}'")
            return False
        except Exception as e:
            print(f"Error al verificar modelo Vosk ({self.language}): {e}")
            return False

    async def escuchar(self) -> tuple[bool, str]:
        """Activar micrófono y reconocer palabras"""
        if not self.vosk_disponible:
            error_msg = f"ERROR: Vosk ({self.language}) no está disponible"
            print(error_msg)
            return False, error_msg

        async with self._stt_lock:
            if self.esta_escuchando:
                return False, "Ya estoy escuchando"

            self.esta_escuchando = True
            try:
                archivo_audio = "temp_audio.wav"
                await self._grabar_audio(archivo_audio)
                texto = await self._reconocer_con_vosk(archivo_audio)

                print(f"Texto reconocido ({self.language}): {texto if texto else 'No se detectó voz'}")
                return bool(texto), texto or ""

            except Exception as e:
                error_msg = f"Error en reconocimiento de voz ({self.language}): {e}"
                print(error_msg)
                return False, f"ERROR: {str(e)}"
            finally:
                self.esta_escuchando = False
                if os.path.exists(archivo_audio):
                    try:
                        os.remove(archivo_audio)
                    except Exception as e:
                        print(f"Error eliminando archivo de audio: {e}")

    async def _grabar_audio(self, filename: str):
        """Grabar audio del micrófono"""
        recognizer = sr.Recognizer()

        def grabar():
            with sr.Microphone() as source:
                recognizer.adjust_for_ambient_noise(source)
                print("Micrófono listo, habla ahora..." if self.language == 'es' else "Microphone ready, speak now...")
                audio = recognizer.listen(source, timeout=self.tiempo_escucha / 1000)
                with open(filename, "wb") as f:
                    f.write(audio.get_wav_data())
                print("Audio grabado correctamente" if self.language == 'es' else "Audio recorded successfully")

        await asyncio.get_event_loop().run_in_executor(None, grabar)

    async def _reconocer_con_vosk(self, filename: str) -> str:
        ruta_modelo = self.ruta_modelo_vosk_es if self.language == 'es' else self.ruta_modelo_vosk_en
        if not self.vosk_disponible:
            return ""

        def recognize():
            try:
                model = Model(ruta_modelo)
                wf = wave.open(filename, "rb")
                rec = KaldiRecognizer(model, wf.getframerate())
                rec.SetWords(True)
                results = []
                while True:
                    data = wf.readframes(4000)
                    if len(data) == 0:
                        break
                    if rec.AcceptWaveform(data):
                        results.append(json.loads(rec.Result()))

                results.append(json.loads(rec.FinalResult()))
                wf.close()
                return " ".join(res.get("text", "") for res in results if res.get("text"))
            except Exception as e:
                print(f"Error en reconocimiento Vosk ({self.language}): {e}")
                return ""

        return await asyncio.get_event_loop().run_in_executor(None, recognize)

    async def accion_mini(self, nombre_accion: str) -> bool:
        block = PlayAction(action_name=nombre_accion)
        try:
            tipo_resultado, response = await asyncio.wait_for(block.execute(), timeout=2)
            print(
                f"DEBUG: accion_mini('{nombre_accion}') - Resultado SDK: {tipo_resultado}, isSuccess: {response.isSuccess if response else 'N/A'}")
            if tipo_resultado != MiniApiResultType.Success or not (response and response.isSuccess):
                print(
                    f"Error en acción '{nombre_accion}': {tipo_resultado} / {response.resultCode if response else 'Sin respuesta'}")
                return False
            return True
        except Exception as e:
            print(f"Excepción en accion_mini('{nombre_accion}'): {e}")
            return False

    async def expresion_facial(self, expresion: str) -> bool:
        block: PlayExpression = PlayExpression(express_name=expresion)
        (resultType, response) = await block.execute()

        print(f'test_play_expression result: {response}')

        assert resultType == MiniApiResultType.Success, 'test_play_expression timetout'
        assert response is not None and isinstance(response, PlayExpressionResponse), 'test_play_expression result unavailable'
        assert response.isSuccess, 'play_expression failed'
        await asyncio.sleep(0.2)
        return True

    async def saludar(self, num: int) -> bool:
        if self.esta_moviendose or not self.device:
            return False

        self.esta_moviendose = True

        try:
            config_saludos = [
                ("random_short3", "emo_007", "Hola, encantado de conocerte"),
                ("random_short4", "emo_016", "Hola, siempre es un placer conocer gente nueva"),
                ("Surveillance_001", "emo_007", "Hola, ¿qué tal?"),
                ("017", "emo_016", "Hola, espero que estés teniendo un buen día")
            ]

            if num < 1 or num > 4:
                print(f"Número de saludo inválido: {num}. Usando saludo 1.")
                num = 1

            accion, gesto, mensaje = config_saludos[num - 1]

            # Ejecutar todas las acciones en paralelo
            await asyncio.gather(
                self.accion_mini(accion),
                self.expresion_facial(gesto),
                self.generar_y_reproducir_audio(mensaje),
                return_exceptions=True
            )

            await asyncio.sleep(0.2)
            return True

        except Exception as e:
            print(f"Error en saludo: {e}")
            return False
        finally:
            self.esta_moviendose = False

    async def caminar(self, pasos: int) -> bool:
        if self.esta_moviendose or not self.device:
            return False

        self.esta_moviendose = True

        try:
            # De los ejemplos de código del SDK de alphamini
            block : MoveRobot = MoveRobot(step=pasos, direction=MoveRobotDirection.FORWARD)
            # step: Move a few steps
            # direction: direction, enumeration type
            # block: MoveRobot = MoveRobot(step=10, direction=MoveRobotDirection.LEFTWARD)
            # response : MoveRobotResponse
            (tipo_resultado, response) = await block.execute()

            print(f'test_move_robot result:{response}')

            assert tipo_resultado == MiniApiResultType.Success, 'test_move_robot timetout'
            assert response is not None and isinstance(response, MoveRobotResponse), 'test_move_robot result unavailable'
            assert response.isSuccess, 'move_robot failed'

            await asyncio.sleep(0.2)
            return True

        except Exception as e:
            print(f"Error en caminar: {e}")
            return False
        finally:
            self.esta_moviendose = False

    async def deporte(self) -> bool:
        if self.esta_moviendose or not self.device:
            return False

        self.esta_moviendose = True

        try:
            success = await self.accion_mini("012")
            return True

        except Exception as e:
            print(f"Error en deporte: {e}")
            return False
        finally:
            self.esta_moviendose = False

    async def hipo(self) -> bool:
        if not self.chat:
            self.chat = self.client.chats.create(model='gemini-2.0-flash')

        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self.chat.send_message(
                message = "no uses texto en negrita ni emojis. eres una persona con diabetes y tienes hipoglucemia. di que tienes hipoglucemia y una frase representativa de cómo te sientes que sea fácil de entender para un niño."
            )
        )
        respuesta = response.text.strip()

        if '.' in respuesta:
            r1 = ""
            r2 = ""
            if '.' in respuesta:
                parts = respuesta.split('.', 1)
                r1 = parts[0].strip() + '.'
                if len(parts) > 1 and parts[1].strip():
                    r2 = parts[1].strip()
            else:
                r1 = respuesta

            if r2:
                print(f"Mensaje estado: {r1} {r2}")
            else:
                print(f"Mensaje estado: {r1}")

        if self.esta_moviendose or not self.device:
            print(f"Robot no conectado")
            return False

        self.esta_moviendose = True

        try:
            config_hipo = [
                ("action_004", "emo_019", r1),
                ("038", "codemao1", r2)
            ]


            for accion, gesto, mensaje in config_hipo:
                await asyncio.gather(
                    self.accion_mini(accion),
                    self.expresion_facial(gesto),
                    self.generar_y_reproducir_audio(mensaje),
                    return_exceptions=True
                )

            await asyncio.sleep(0.2)
            return True

        except Exception as e:
            print(f"Error en acción hipoglucemia")
            return False
        finally:
            self.esta_moviendose = False

    async def hiper(self) -> bool:
        if not self.chat:
            self.chat =  self.client.chats.create(model='gemini-2.0-flash')

        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self.chat.send_message(
                message = "no uses texto en negrita ni emojis. eres una persona con diabetes y tienes hiperglucemia. di que tienes hiperglucemia y una frase representativa de cómo te sientes que sea fácil de entender para un niño."
            )
        )
        respuesta = response.text.strip()

        r1 = ""
        r2 = ""
        if '.' in respuesta:
            parts = respuesta.split('.', 1)
            r1 = parts[0].strip() + '.'
            if len(parts) > 1 and parts[1].strip():
                r2 = parts[1].strip()
        else:
            r1 = respuesta

        if r2:
            print(f"Mensaje estado: {r1} {r2}")
        else:
            print(f"Mensaje estado: {r1}")

        if self.esta_moviendose or not self.device:
            print(f"Robot no conectado")
            return False

        self.esta_moviendose = True

        try:
            config_hiper = [
                ("037", "emo_019", r1),
                ("017", "codemao20", r2)
            ]


            for accion, gesto, mensaje in config_hiper:
                await asyncio.gather(
                    self.accion_mini(accion),
                    self.expresion_facial(gesto),
                    self.generar_y_reproducir_audio(mensaje),
                    return_exceptions=True
                )

            await asyncio.sleep(0.2)
            return True

        except Exception as e:
            print(f"Error en acción hiperglucemia: {e}")
            return False
        finally:
            self.esta_moviendose = False

    async def normal(self) -> bool:
        if not self.chat:
            self.chat =  self.client.chats.create(model='gemini-2.0-flash')

        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self.chat.send_message(
                message = "no uses texto en negrita ni emojis. tienes diabetes y tienes normoglucemia. di que tienes un nivel de glucosa en sangre normal y una frase representativa de cómo te sientes que sea fácil de entender para un niño."
            )
        )
        respuesta = response.text.strip()

        r1 = ""
        r2 = ""
        if '.' in respuesta:
            parts = respuesta.split('.', 1)
            r1 = parts[0].strip() + '.'
            if len(parts) > 1 and parts[1].strip():
                r2 = parts[1].strip()
        else:
            r1 = respuesta

        if r2:
            print(f"Mensaje estado: {r1} {r2}")
        else:
            print(f"Mensaje estado: {r1}")

        if self.esta_moviendose or not self.device:
            print(f"Robot no conectado")
            return False

        self.esta_moviendose = True

        try:
            config_normal = [
                ("011", "emo_007", r1),
                ("action_006", "emo_016", r2)
            ]

            for accion, gesto, mensaje in config_normal:
                await asyncio.gather(
                    self.accion_mini(accion),
                    self.expresion_facial(gesto),
                    self.generar_y_reproducir_audio(mensaje),
                    return_exceptions=True
                )

            await asyncio.sleep(0.2)
            return True

        except Exception as e:
            print(f"Error en acción normal: {e}")
            return False
        finally:
            self.esta_moviendose = False

    def convertir_palabras_a_numeros(self, texto):
        """Convertir números en texto a dígitos"""
        texto_lower = texto.lower()
        for palabra, numero in NUMEROS_PALABRAS.items():
            texto_lower = texto_lower.replace(palabra, str(numero))
        return texto_lower

    #
    # SIMULADOR
    #

    async def get_glucosa_simulador(self) -> int | None:
        """
        Obtiene el valor de glucosa del simulador usando la URL inyectada
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.url_get_glucosa, timeout=5) as response:
                    response.raise_for_status()
                    data = await response.json()
                    if data.get("status") == "success":
                        glucose_value = data.get("glucosa")
                        print(f"Glucosa recibida del simulador: {glucose_value}")
                        return int(glucose_value)
                    else:
                        print(f"Error en respuesta del simulador: {data.get('error', 'Error desconocido')}")
                        return None
        except asyncio.TimeoutError:
            print("Timeout al conectar con el simulador.")
            return None
        except aiohttp.ClientError as e:
            print(f"Error de conexión al simulador: {e}")
            return None
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Error procesando la respuesta del simulador: {e}")
            return None

    async def procesar_comando_simulador(self, texto: str) -> bool:
        texto = texto.lower().strip()
        texto = self.convertir_palabras_a_numeros(texto)

        bolus_val = 0
        cho_val = 0
        exercise_intensity_val = 0
        exercise_duration_val = 0
        es_comando = False

        if 'comer' in texto:
            match = re.search(r'(\d+)', texto)
            if match:
                cho_val = int(match.group(1))
                print(f"[SIMULADOR] Comando COMER detectado: {cho_val}g")
                es_comando = True
        elif 'inyectar' in texto or 'pinchar' in texto or 'insulina' in texto:
            match = re.search(r'(\d+)', texto)
            if match:
                bolus_val = int(match.group(1))
                print(f"[SIMULADOR] Comando INYECTAR detectado: {bolus_val} unidades")
                es_comando = True
        elif 'ejercicio' in texto or 'deporte' in texto:
            es_comando = True
            match_duracion = re.search(r'(\d+)', texto)
            exercise_duration_val = int(match_duracion.group(1)) if match_duracion else 30
            exercise_intensity_val = 1
            intensidad_str = "suave"
            if 'medio' in texto:
                exercise_intensity_val = 2
                intensidad_str = "medio"
            elif 'fuerte' in texto or 'intenso' in texto:
                exercise_intensity_val = 3
                intensidad_str = "fuerte"
            print(f"[SIMULADOR] Comando EJERCICIO detectado: {exercise_duration_val} minutos, intensidad {intensidad_str}")

        if es_comando:
            exito, _ = await self.enviar_comando_web(
                bolus=bolus_val,
                cho=cho_val,
                exercise_intensity=exercise_intensity_val,
                exercise_duration=exercise_duration_val
            )
            return exito
        else:
            print("[SIMULADOR] No es un comando de simulador, pasando a chat normal.")
            return False

    async def enviar_comando_web(self,
                                 bolus: int = 0,
                                 cho: int = 0,
                                 exercise_duration: int = 0,
                                 exercise_intensity: int = 0,
                                 text: str = ""
                                 ) -> tuple[bool, dict]:

        payload = {
            "bolus": bolus, "cho": cho,
            "exercise_duration": exercise_duration, "exercise_intensity": exercise_intensity,
            "text": text
        }
        print(f"Enviando a web: {json.dumps(payload)}")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.url_receptor_comandos, json=payload, timeout=10) as response:
                    response.raise_for_status()
                    response_json = await response.json()
                    if response_json.get('status') == 'ok':
                        print(f"Respuesta exitosa del servidor: {response_json.get('message')}")
                        return True, response_json
                    else:
                        print(f"Respuesta del servidor con problemas: {response_json}")
                        return False, response_json
        except asyncio.TimeoutError:
            print(f"Error: La solicitud a {self.url_receptor_comandos} ha tardado demasiado.")
            return False, {"error": "Timeout"}
        except aiohttp.ClientError as e:
            print(f"Error en la solicitud HTTP a {self.url_receptor_comandos}: {e}")
            return False, {"error": str(e)}
        except Exception as e:
            print(f"Error inesperado: {e}")

            return False, {"error": "Unexpected error"}
