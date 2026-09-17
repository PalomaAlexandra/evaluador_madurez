import io
import os
import re
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

SERVICE_ACCOUNT_FILE = 'credentials.json'
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']

def extraer_id_carpeta(url_o_id: str) -> str:
    """Extrae el ID de una carpeta de Google Drive a partir de una URL o devuelve el mismo ID si ya es plano."""
    if not url_o_id:
        return ""
    match = re.search(r'folders/([a-zA-Z0-9_-]+)', url_o_id)
    if match:
        return match.group(1)
    return url_o_id.strip()

def obtener_servicio_drive():
    """Autentica y devuelve el servicio de Google Drive API soportando Secrets y local."""
    SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
    # 1. Buscar en Streamlit Cloud Secrets
    if "gcp_service_account" in st.secrets:
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    # 2. Buscar en archivo local
    elif os.path.exists('credentials.json'):
        creds = Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
    else:
        raise FileNotFoundError("No se encontraron las credenciales de Google Service Account.")
        
    return build('drive', 'v3', credentials=creds)

def buscar_archivos_recursivo(folder_id: str, service, ruta_acumulada=""):
    """
    Bucea en todas las subcarpetas del Repositorio de EA 
    y devuelve una lista con la ruta y nombre de todos los archivos.
    """
    archivos = []
    if not folder_id:
        return archivos

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
                sub_ruta = f"{ruta_acumulada}/{item['name']}" if ruta_acumulada else item['name']
                archivos.extend(buscar_archivos_recursivo(item['id'], service, sub_ruta))
            else:
                archivos.append({
                    "id": item['id'],
                    "nombre": item['name'],
                    "ruta": f"{ruta_acumulada}/{item['name']}" if ruta_acumulada else item['name']
                })
    except Exception as e:
        print(f"Error buceando en carpeta {folder_id}: {str(e)}")
        
    return archivos

def descargar_excel_desde_drive(file_id: str) -> io.BytesIO:
    """Descarga el archivo Excel de entrevistas desde Google Drive en memoria."""
    service = obtener_servicio_drive()
    request = service.files().get_media(fileId=file_id)
    file_stream = io.BytesIO()
    downloader = MediaIoBaseDownload(file_stream, request)
    
    done = False
    while not done:
        _, done = downloader.next_chunk()
    
    file_stream.seek(0)
    return file_stream

def existe_evidencia_en_repositorio(pregunta_texto, archivos_repositorio):
    """
    Busca si en el Repositorio de EA existe algún archivo cuyo nombre coincida 
    con las palabras clave principales de la pregunta evaluada.
    """
    if not archivos_repositorio:
        return False, None
    
    # Extraer palabras clave de más de 4 letras excluyendo stop-words comunes
    palabras = [p.lower() for p in re.findall(r'\b\w{4,}\b', str(pregunta_texto)) 
                if p.lower() not in ['existe', 'como', 'sobre', 'para', 'está', 'este', 'esta', 'estos', 'estas', 'donde', 'tiene', 'tienen']]
    
    for archivo in archivos_repositorio:
        nombre_lower = archivo['nombre'].lower()
        coincidencias = sum(1 for p in palabras if p in nombre_lower)
        if coincidencias >= 2:  # Si coincide al menos 2 palabras clave
            return True, archivo['ruta']
            
    return False, None

def procesar_evaluacion_madurez_auditada(excel_file_id: str, repositorio_ea_folder_id: str = "") -> dict:
    """
    Procesa las entrevistas y valida las evidencias cruzando la columna del Excel 
    con el buceo recursivo en el Repositorio de EA.
    """
    excel_stream = descargar_excel_desde_drive(excel_file_id)
    xls = pd.ExcelFile(excel_stream)
    
    service = obtener_servicio_drive()
    repo_id_limpio = extraer_id_carpeta(repositorio_ea_folder_id)
    
    archivos_repositorio = []
    if repo_id_limpio:
        archivos_repositorio = buscar_archivos_recursivo(repo_id_limpio, service)

    hallazgos_auditoria = []
    
    def auditar_hoja(sheet_name, col_pregunta_name, col_puntaje_name, col_evidencia_name):
        excel_stream.seek(0)
        df = pd.read_excel(excel_stream, sheet_name=sheet_name, skiprows=2)
        headers = df.iloc[0].values
        df.columns = [str(h).strip() if pd.notna(h) else f"col_{i}" for i, h in enumerate(headers)]
        df = df.iloc[1:].copy()
        
        col_p = [c for c in df.columns if col_puntaje_name in c][0]
        col_q = [c for c in df.columns if col_pregunta_name in c][0]
        col_ev = [c for c in df.columns if col_evidencia_name in c]
        col_ev_str = col_ev[0] if col_ev else None
        
        df[col_p] = pd.to_numeric(df[col_p], errors='coerce')
        
        puntajes_declarados = []
        puntajes_auditados = []
        
        for idx, row in df.iterrows():
            p_declarado = row[col_p]
            pregunta = row[col_q]
            evidencia_excel = str(row[col_ev_str]) if col_ev_str and pd.notna(row[col_ev_str]) else ""
            
            if pd.isna(p_declarado):
                continue
                
            puntajes_declarados.append(p_declarado)
            
            # Verificar si hay evidencia en la columna del Excel o en el Repositorio
            tiene_link_excel = len(evidencia_excel.strip()) > 5 and ("http" in evidencia_excel or "sharepoint" in evidencia_excel or "drive" in evidencia_excel)
            encontrado_en_repo, ruta_archivo = existe_evidencia_en_repositorio(pregunta, archivos_repositorio)
            
            p_auditado = p_declarado
            
            # REGLAS DE AUDITORÍA
            if p_declarado >= 2 and not tiene_link_excel and not encontrado_en_repo:
                # Castigo de puntaje por falta de evidencia sustentada
                p_auditado = 1.0
                hallazgos_auditoria.append({
                    "hoja": sheet_name,
                    "pregunta": pregunta[:80] + "...",
                    "tipo": "Castigo por Falta de Evidencia",
                    "puntaje_declarado": p_declarado,
                    "puntaje_auditado": 1.0,
                    "detalle": "El entrevistado declaró nivel medio/alto pero no adjuntó link ni se encontró el documento en el Repositorio de EA."
                })
            elif p_declarado <= 1 and (tiene_link_excel or encontrado_en_repo):
                # Detección de brecha de comunicación/conocimiento
                p_auditado = 2.0
                hallazgos_auditoria.append({
                    "hoja": sheet_name,
                    "pregunta": pregunta[:80] + "...",
                    "tipo": "Brecha de Conocimiento/Desinformación",
                    "puntaje_declarado": p_declarado,
                    "puntaje_auditado": 2.0,
                    "detalle": f"El entrevistado indicó que no existe, pero SÍ consta evidencia documental ({ruta_archivo if encontrado_en_repo else 'Link Excel'})."
                })
            else:
                if encontrado_en_repo:
                    hallazgos_auditoria.append({
                        "hoja": sheet_name,
                        "pregunta": pregunta[:80] + "...",
                        "tipo": "Evidencia Confirmada en Repositorio EA",
                        "puntaje_declarado": p_declarado,
                        "puntaje_auditado": p_declarado,
                        "detalle": f"Documento verificado en Repositorio: {ruta_archivo}"
                    })

            puntajes_auditados.append(p_auditado)

        prom_declarado = pd.Series(puntajes_declarados).mean() if puntajes_declarados else 0.0
        prom_auditado = pd.Series(puntajes_auditados).mean() if puntajes_auditados else 0.0
        
        return round(prom_declarado, 2), round(prom_auditado, 2)

    # Evaluar pestañas
    acmm_dec, acmm_aud = auditar_hoja('1. ACMM', 'Pregunta', 'Nivel', 'Evidencia') if '1. ACMM' in xls.sheet_names else (0,0)
    gao_dec, gao_aud = auditar_hoja('2. GAO EAMMF', 'Práctica', 'Puntaje', 'Evidencia') if '2. GAO EAMMF' in xls.sheet_names else (0,0)
    nascio_dec, nascio_aud = auditar_hoja('3. NASCIO', 'Pregunta', 'Nivel', 'Evidencia') if '3. NASCIO' in xls.sheet_names else (0,0)
    mod_dec, mod_aud = auditar_hoja('4. Módulo D10–D13', 'Pregunta', 'Nivel', 'Evidencia') if '4. Módulo D10–D13' in xls.sheet_names else (0,0)

    # Triangulación Ponderada
    triang_declarada = (acmm_dec * 0.30) + (gao_dec * 0.25) + (nascio_dec * 0.25) + (mod_dec * 0.20)
    triang_auditada = (acmm_aud * 0.30) + (gao_aud * 0.25) + (nascio_aud * 0.25) + (mod_aud * 0.20)

    return {
        "resumen_triangulado": {
            "nivel_declarado_entrevistas": round(triang_declarada, 2),
            "nivel_auditado_real": round(triang_auditada, 2),
            "diferencia_desviacion": round(triang_declarada - triang_auditada, 2),
            "total_archivos_encontrados_en_repositorio_ea": len(archivos_repositorio)
        },
        "desglose_por_marco": {
            "ACMM": {"declarado": acmm_dec, "auditado": acmm_aud},
            "GAO": {"declarado": gao_dec, "auditado": gao_aud},
            "NASCIO": {"declarado": nascio_dec, "auditado": nascio_aud},
            "D10_D13": {"declarado": mod_dec, "auditado": mod_aud}
        },
        "hallazgos_y_desviaciones": hallazgos_auditoria[:15]  # Top 15 hallazgos principales
    }