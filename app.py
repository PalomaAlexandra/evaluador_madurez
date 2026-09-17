import os
import json
import socket
from datetime import datetime
from pathlib import Path

import streamlit as st
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# Fuerza IPv4 deshabilitando IPv6 para conexiones de red estables
socket.has_ipv6 = False
original_getaddrinfo = socket.getaddrinfo

def ipv4_only_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return original_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)

socket.getaddrinfo = ipv4_only_getaddrinfo

# Importaciones del proyecto
from memory_manager import ModernMemoryManager, UserManager
from chatbot import ChatbotManager
from evaluador_madurez import procesar_evaluacion_madurez_auditada, extraer_id_carpeta
from utils import (
    format_timestamp,
    truncate_text,
    validate_user_id,
    get_memory_category_icon
)
from config import PAGE_TITLE, PAGE_ICON

# IDs por defecto de Google Drive
FOLDER_ID_BASE_CONOCIMIENTO = "1ifvN0roOVTzQHo31T7wQZhXB57pv5c5s"
EXCEL_FILE_ID = "1IDc4m9YXBfDI28FSSbMssD-vwJivxPH-"

# Configuración inicial de la página de Streamlit
st.set_page_config(
    page_title=PAGE_TITLE,
    page_icon=PAGE_ICON,
    layout="wide",
    initial_sidebar_state="expanded"
)

def obtener_servicio_drive():
    """Obtiene el cliente de la API de Google Drive soportando autenticación híbrida (Streamlit Secrets o credentials.json local)."""
    SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
    
    # 1. Prioridad: Leer de Streamlit Cloud Secrets
    if "gcp_service_account" in st.secrets:
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    # 2. Respaldo local: Leer de credentials.json
    elif os.path.exists("credentials.json"):
        creds = Credentials.from_service_account_file("credentials.json", scopes=SCOPES)
    else:
        raise FileNotFoundError("No se encontraron credenciales válidas de Google Cloud Service Account.")
        
    return build('drive', 'v3', credentials=creds)

def obtener_archivos_recursivo(folder_id, service, ruta_padre="Base de conocimiento"):
    """Recorre recursivamente todas las subcarpetas y extrae ÚNICAMENTE archivos finales."""
    archivos_totales = []
    try:
        query = f"'{folder_id}' in parents and trashed = false"
        results = service.files().list(
            q=query,
            pageSize=1000,
            fields="files(id, name, mimeType)"
        ).execute()
        
        items = results.get('files', [])
        for item in items:
            if item['mimeType'] == 'application/vnd.google-apps.folder':
                nueva_ruta = f"{ruta_padre} / {item['name']}"
                sub_archivos = obtener_archivos_recursivo(item['id'], service, ruta_padre=nueva_ruta)
                archivos_totales.extend(sub_archivos)
            else:
                item['ruta_ubicacion'] = ruta_padre
                archivos_totales.append(item)
    except Exception as e:
        print(f"Error recorriendo folder {folder_id}: {e}")
            
    return archivos_totales

def obtener_conteo_y_archivos(folder_id=FOLDER_ID_BASE_CONOCIMIENTO):
    """Consulta la API de Google Drive y formatea la lista de documentos normativos."""
    try:
        service = obtener_servicio_drive()
        files = obtener_archivos_recursivo(folder_id, service)
        total_archivos = len(files)

        if not files:
            return 0, "No se encontraron documentos dentro de la Base de conocimiento ni en sus subcarpetas."

        lista_formateada = [
            f"- Documento: '{f['name']}' | Ubicación: [{f['ruta_ubicacion']}]" 
            for f in files
        ]
        return total_archivos, "\n".join(lista_formateada)
    except Exception as e:
        return 0, f"Error al conectar con Google Drive: {str(e)}"

def init_session_state():
    """Inicializa el estado de la sesión de Streamlit"""
    if 'current_user' not in st.session_state:
        st.session_state.current_user = None
    if 'current_chat' not in st.session_state:
        st.session_state.current_chat = None
    if 'chatbot' not in st.session_state:
        st.session_state.chatbot = None
    if 'memory_manager' not in st.session_state:
        st.session_state.memory_manager = None
    if 'chat_history' not in st.session_state:
        st.session_state.chat_history = []
    if 'show_memories' not in st.session_state:
        st.session_state.show_memories = False
    if 'repo_ea_url' not in st.session_state:
        st.session_state.repo_ea_url = ""

def user_selection_sidebar():
    """Sidebar para selección/creación de usuarios y configuración de repositorios"""
    st.sidebar.header("👤 Usuario")
    
    existing_users = UserManager.get_users()
    
    if existing_users:
        selected_user = st.sidebar.selectbox(
            "Seleccionar usuario:",
            [""] + existing_users,
            key="user_selector"
        )
        if selected_user and selected_user != st.session_state.current_user:
            st.session_state.current_user = selected_user
            st.session_state.chatbot = ChatbotManager.get_chatbot(selected_user)
            st.session_state.memory_manager = ModernMemoryManager(selected_user)
            st.session_state.current_chat = None
            st.session_state.chat_history = []
            st.rerun()
    else:
        st.sidebar.info("No hay usuarios creados")
    
    with st.sidebar.expander("Crear nuevo usuario", expanded=not existing_users):
        new_user_id = st.text_input(
            "ID de usuario:",
            placeholder="usuario123",
            help="Solo letras, números, - y _",
            key="new_user_input"
        )
        if st.button("Crear Usuario", type="primary", key="create_user_btn"):
            if not new_user_id:
                st.error("Ingresa un ID de usuario")
            elif not validate_user_id(new_user_id):
                st.error("ID inválido. Solo letras, números, - y _")
            elif UserManager.user_exists(new_user_id):
                st.error("El usuario ya existe")
            else:
                if UserManager.create_user(new_user_id):
                    st.session_state.current_user = new_user_id
                    st.session_state.chatbot = ChatbotManager.get_chatbot(new_user_id)
                    st.session_state.memory_manager = ModernMemoryManager(new_user_id)
                    st.session_state.current_chat = None
                    st.session_state.chat_history = []
                    st.success(f"Usuario '{new_user_id}' creado")
                    st.rerun()
                else:
                    st.error("Error creando usuario")

    # SECCIÓN DE REPOSITORIO DINÁMICO EN EL SIDEBAR
    st.sidebar.markdown("---")
    st.sidebar.header("📁 Repositorio Evidencias EA")
    url_input = st.sidebar.text_input(
        "URL/ID carpeta Evidencias EA:",
        value=st.session_state.get('repo_ea_url', ''),
        placeholder="https://drive.google.com/drive/folders/...",
        key="repo_ea_input"
    )
    if url_input != st.session_state.repo_ea_url:
        st.session_state.repo_ea_url = url_input

def chat_history_sidebar():
    """Sidebar estilo ChatGPT con historial de conversaciones"""
    if not st.session_state.current_user:
        return
    
    st.sidebar.header("💬 Chats")
    memory_manager = st.session_state.memory_manager
    
    if st.sidebar.button("➕ Nuevo Chat", type="primary", use_container_width=True):
        new_chat_id = memory_manager.create_new_chat()
        st.session_state.current_chat = new_chat_id
        st.session_state.chat_history = []
        st.rerun()
    
    st.sidebar.markdown("---")
    
    chats = memory_manager.get_user_chats()
    if chats:
        st.sidebar.subheader("Historial")
        for chat in chats:
            chat_id = chat['chat_id']
            title = chat['title']
            message_count = chat.get('message_count', 0)
            updated_at = format_timestamp(chat['updated_at'])
            
            chat_container = st.sidebar.container()
            with chat_container:
                col1, col2 = st.columns([4, 1])
                with col1:
                    is_active = st.session_state.current_chat == chat_id
                    button_args = {
                        "label": f"💬 {truncate_text(title, 25)}",
                        "key": f"chat_{chat_id}",
                        "help": f"Mensajes: {message_count} | Actualizado: {updated_at}",
                        "use_container_width": True
                    }
                    if is_active:
                        button_args["type"] = "secondary"
                    if st.button(**button_args):
                        if st.session_state.current_chat != chat_id:
                            st.session_state.current_chat = chat_id
                            st.session_state.chat_history = st.session_state.chatbot.get_conversation_history(chat_id)
                            st.rerun()
                with col2:
                    if st.button("🗑️", key=f"delete_{chat_id}", help="Eliminar chat"):
                        if memory_manager.delete_chat(chat_id):
                            if st.session_state.chatbot:
                                st.session_state.chatbot.delete_chat_from_langgraph(chat_id)
                            if st.session_state.current_chat == chat_id:
                                st.session_state.current_chat = None
                                st.session_state.chat_history = []
                            st.rerun()
        
        st.sidebar.markdown(f"**Total de chats:** {len(chats)}")
    else:
        st.sidebar.info("No hay chats todavía.\nHaz clic en 'Nuevo Chat' para comenzar.")

def process_user_message(user_input: str):
    """Procesa el mensaje del usuario integrando Base de Conocimiento, Repositorio EA y Auditoría del Excel."""
    
    with st.chat_message("user"):
        st.write(user_input)
        st.caption(f"📅 {format_timestamp(datetime.now().isoformat())}")
    
    palabras_clave_drive = ["drive", "carpeta", "archivos", "documentos", "base de conocimiento"]
    palabras_clave_madurez = ["madurez", "evaluación", "evaluacion", "puntaje", "acmm", "gao", "nascio", "diagnóstico", "diagnostico", "nivel", "auditoría", "auditoria"]
    
    prompt_final = user_input

    # 1. EVALUACIÓN Y AUDITORÍA INTEGRAL DE MADUREZ
    if any(p in user_input.lower() for p in palabras_clave_madurez):
        with st.spinner("Descargando Excel, analizando Base de Conocimiento y buceando en el Repositorio de EA..."):
            try:
                # A. Obtener normativas de la Base de Conocimiento
                total_normas, lista_normas = obtener_conteo_y_archivos(FOLDER_ID_BASE_CONOCIMIENTO)
                
                # B. Obtener auditoría del Excel cruzado con la carpeta dinámica de EA
                repo_id_dinamico = st.session_state.get('repo_ea_url', '')
                reporte_auditado = procesar_evaluacion_madurez_auditada(EXCEL_FILE_ID, repo_id_dinamico)
                
                contexto_completo = (
                    f"\n\n[1. MARCOS NORMATIVOS Y BASE DE CONOCIMIENTO DISPONIBLE]\n"
                    f"Total de documentos teóricos: {total_normas}\n"
                    f"Normativas cargadas:\n{lista_normas}\n\n"
                    f"[2. RESULTADOS Y AUDITORÍA DE ENTREVISTAS DE MADUREZ EA]\n"
                    f"{json.dumps(reporte_auditado, indent=2, ensure_ascii=False)}\n"
                    f"[FIN DEL CONTEXTO INTEGRAL]\n"
                )
                prompt_final = f"{user_input}\n{contexto_completo}"
            except Exception as e:
                st.error(f"Error al procesar la auditoría de madurez: {str(e)}")

    # 2. CONSULTA ESPECÍFICA DE CARPETAS EN DRIVE
    elif any(p in user_input.lower() for p in palabras_clave_drive):
        with st.spinner("Consultando la Base de Conocimiento en Google Drive..."):
            total, lista_archivos = obtener_conteo_y_archivos(FOLDER_ID_BASE_CONOCIMIENTO)
            contexto_drive = (
                f"\n\n[CONTEXTO DE LA BASE DE CONOCIMIENTO]\n"
                f"Archivos normativos encontrados: {total}\n"
                f"Documentos disponibles:\n{lista_archivos}\n"
                f"[FIN DEL CONTEXTO]\n"
            )
            prompt_final = f"{user_input}\n{contexto_drive}"

    # 3. CONSULTA AL MODELO Y RESPUESTA
    with st.spinner("Generando diagnóstico y análisis cruzado..."):
        response = st.session_state.chatbot.chat(prompt_final, st.session_state.current_chat)
    
    if response['success']:
        respuesta_texto = response['response']
        timestamp_actual = datetime.now().isoformat()

        with st.chat_message("assistant"):
            st.write(respuesta_texto)
            caption_parts = [f"📅 {format_timestamp(timestamp_actual)}"]
            if response.get('memories_used', 0) > 0:
                caption_parts.append(f"🧠 {response['memories_used']} memorias")
            if response.get('context_optimized'):
                caption_parts.append("⚡ Optimizado")
            st.caption(" | ".join(caption_parts))

        st.session_state.chat_history.append({
            "role": "user",
            "content": user_input,
            "timestamp": timestamp_actual
        })
        st.session_state.chat_history.append({
            "role": "assistant",
            "content": respuesta_texto,
            "timestamp": timestamp_actual
        })

        st.session_state.memory_manager.update_chat_metadata(
            st.session_state.current_chat,
            increment_messages=True
        )
        
        st.session_state.chat_history = st.session_state.chatbot.get_conversation_history(
            st.session_state.current_chat
        )
        st.rerun()
    else:
        st.error(f"Error: {response['error']}")

def main_chat_interface():
    """Interfaz principal de conversación"""
    if not st.session_state.current_user:
        st.title(PAGE_TITLE)
        st.info("👈 Selecciona tu usuario en la barra lateral para recuperar tu sesión")
        return
    
    chatbot = st.session_state.chatbot
    if not chatbot:
        st.error("Error inicializando chatbot")
        return
    
    if not st.session_state.current_chat:
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.markdown("<div style='text-align: center;'>", unsafe_allow_html=True)
            st.title("🤖 Asistente de Madurez EA")
            st.markdown(f"**Hola, {st.session_state.current_user}!**")
            st.markdown("¿En qué puedo ayudarte hoy?")
            st.markdown("### Puedes preguntarme sobre:")
            st.markdown("""
            - 📊 **Nivel de madurez auditado (TOGAF, GAO, NASCIO)**
            - 📁 **Evidencias del Repositorio de EA**
            - 📚 **Documentos normativos de la Base de Conocimiento**
            - 🎯 **Pautas para pasar al siguiente nivel de madurez**
            """)
            st.markdown("</div>", unsafe_allow_html=True)
        
        user_input = st.chat_input("Comienza una nueva conversación...")
        if user_input:
            memory_manager = st.session_state.memory_manager
            new_chat_id = memory_manager.create_new_chat(user_input)
            st.session_state.current_chat = new_chat_id
            process_user_message(user_input)
        return
    
    current_chat_info = st.session_state.memory_manager.get_chat_info(st.session_state.current_chat)
    if not current_chat_info:
        st.error("Chat no encontrado")
        return
    
    st.title(f"💬 {current_chat_info['title']}")
    st.caption(f"Usuario: {st.session_state.current_user}")
    
    if not st.session_state.chat_history:
        st.session_state.chat_history = chatbot.get_conversation_history(st.session_state.current_chat)
    
    chat_container = st.container()
    with chat_container:
        if st.session_state.chat_history:
            for message in st.session_state.chat_history:
                timestamp = format_timestamp(message.get('timestamp', ''))
                if message['role'] == 'user':
                    with st.chat_message("user"):
                        st.write(message['content'])
                        if timestamp:
                            st.caption(f"📅 {timestamp}")
                else:
                    with st.chat_message("assistant"):
                        st.write(message['content'])
                        if timestamp:
                            st.caption(f"📅 {timestamp}")
        else:
            st.info("Comienza la conversación escribiendo un mensaje.")
    
    user_input = st.chat_input("Escribe tu mensaje aquí...")
    if user_input:
        process_user_message(user_input)

def show_memory_interface(container=st):
    """Interfaz para inspeccionar memorias vectoriales persistentes"""
    container.subheader("🧠 Memoria Vectorial")
    if container.button("Cerrar", key="close_memories"):
        st.session_state.show_memories = False
        st.rerun()
    if not st.session_state.memory_manager:
        container.error("No hay gestor de memoria disponible")
        return
    
    memories = st.session_state.memory_manager.get_all_vector_memories()
    if not memories:
        container.info("No hay memorias guardadas todavía.")
        return
    
    col1, col2, col3 = container.columns(3)
    with col1:
        st.metric("Total Memorias", len(memories))
    with col2:
        categories = [mem['metadata'].get('category', 'sin_categoria') for mem in memories]
        st.metric("Categorías", len(set(categories)))
    with col3:
        high_importance = sum(1 for mem in memories if mem['metadata'].get('importance', 0) >= 4)
        st.metric("Alta Importancia", high_importance)
    
    categories = list(set(mem['metadata'].get('category', 'sin_categoria') for mem in memories))
    selected_category = container.selectbox("Filtrar por categoría:", ["Todas"] + sorted(categories))
    
    filtered_memories = memories
    if selected_category != "Todas":
        filtered_memories = [mem for mem in memories if mem['metadata'].get('category') == selected_category]
    
    filtered_memories.sort(
        key=lambda x: (x['metadata'].get('importance', 0), x['metadata'].get('timestamp', '')),
        reverse=True
    )
    
    container.write(f"Mostrando {len(filtered_memories)} de {len(memories)} memorias")
    for memory in filtered_memories:
        category = memory['metadata'].get('category', 'sin_categoria')
        timestamp = memory['metadata'].get('timestamp', '')
        importance = memory['metadata'].get('importance', 0)
        
        title_parts = [get_memory_category_icon(category), truncate_text(memory['content'], 60)]
        if importance > 0:
            title_parts.append(f"({'⭐' * importance})")
        title = " ".join(title_parts)
        
        with container.expander(title, expanded=False):
            st.write(memory['content'])
            col1, col2, col3 = st.columns(3)
            with col1:
                st.caption(f"**Categoría:** {category}")
            with col2:
                if importance > 0:
                    st.caption(f"**Importancia:** {'⭐' * importance}")
            with col3:
                st.caption(f"**Fecha:** {format_timestamp(timestamp)}")

def main():
    """Función principal de arranque de la app"""
    init_session_state()
    user_selection_sidebar()
    
    if st.session_state.current_user:
        chat_history_sidebar()
        st.sidebar.markdown("---")
        st.sidebar.info(f"**Usuario activo:** {st.session_state.current_user}")
        if st.sidebar.button("🧠 Ver Todas las Memorias", use_container_width=True):
            st.session_state.show_memories = True
    
    if st.session_state.show_memories:
        chat_col, mem_col = st.columns([3, 2])
        with chat_col:
            main_chat_interface()
        with mem_col:
            show_memory_interface(container=mem_col)
    else:
        main_chat_interface()

if __name__ == "__main__":
    main()