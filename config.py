import os

# Configuracion de directorios
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
USERS_DIR = os.path.join(BASE_DIR, "users")

# Crar directorios si no existen
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(USERS_DIR, exist_ok=True)

# Configuracion del modelo
DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_TEMPERATURE = 0.7

# Configuracion de memoria
MAX_VECTOR_RESULTS = 3
MEMORY_CATEGORIES = [
    "personal",
    "profesional",
    "preferencias",
    "hechos_importantes"
]

# Configuracion de la interfaz
PAGE_TITLE = "Arquitectura 360 - Chat Multi-Usuario con memoria Avanzada"
PAGE_ICON = "🤖"