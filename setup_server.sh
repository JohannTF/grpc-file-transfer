#!/bin/bash

#################################################################
# SETUP AUTOMÁTICO PARA SERVIDOR - Ubuntu
# Sistema de transferencia de archivos gRPC
#################################################################

# ===== VARIABLES DE CONFIGURACIÓN =====

# Archivo a compartir (ruta relativa al directorio actual)
FILE_TO_SHARE="video.mp4"

# Configuración del servidor
SERVER_HOST="10.147.20.4"
SERVER_PORT="50051"

# Configuración de chunks (en bytes)
CHUNK_SIZE="4194304"  # 4MB por defecto

# Máximo de clientes concurrentes
MAX_CLIENTS="10"

# Directorio del proyecto (si es diferente al actual)
PROJECT_DIR="$(pwd)"

#################################################################

# Colores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  CONFIGURACIÓN AUTOMÁTICA SERVIDOR   ${NC}"
echo -e "${BLUE}========================================${NC}"

# Función para verificar errores
check_error() {
    if [ $? -ne 0 ]; then
        echo -e "${RED}Error: $1${NC}"
        exit 1
    fi
}

# Verificar que el archivo existe
if [ ! -f "$FILE_TO_SHARE" ]; then
    echo -e "${RED}Error: El archivo $FILE_TO_SHARE no existe${NC}"
    echo -e "${YELLOW}Modifica la variable FILE_TO_SHARE en este script${NC}"
    exit 1
fi

echo -e "${YELLOW}Configurando entorno...${NC}"

# Cambiar al directorio del proyecto
cd "$PROJECT_DIR"
check_error "No se pudo acceder al directorio del proyecto"

# Verificar/instalar Python 3 y pip
if ! command -v python3 &> /dev/null; then
    echo -e "${YELLOW}Instalando Python 3...${NC}"
    sudo apt-get update > /dev/null 2>&1
    sudo apt-get install -y python3 python3-pip > /dev/null 2>&1
    check_error "No se pudo instalar Python 3"
fi

if ! command -v pip3 &> /dev/null; then
    sudo apt-get install -y python3-pip > /dev/null 2>&1
    check_error "No se pudo instalar pip"
fi

# Instalar dependencias
echo -e "${YELLOW}Descargando dependencias...${NC}"
pip3 install --upgrade pip > /dev/null 2>&1
pip3 install -r requirements.txt > /dev/null 2>&1
check_error "Error instalando dependencias"

# Crear estructura y generar código gRPC
echo -e "${YELLOW}Generando código gRPC...${NC}"
mkdir -p data src/generated
touch src/generated/__init__.py
python3 -m grpc_tools.protoc \
    --proto_path=protos \
    --python_out=src/generated \
    --grpc_python_out=src/generated \
    protos/file_transfer.proto > /dev/null 2>&1
check_error "Error generando código gRPC"

# Mostrar información del archivo
FILE_SIZE=$(stat -c%s "$FILE_TO_SHARE")
FILE_SIZE_MB=$((FILE_SIZE / 1024 / 1024))

# Ejecutar servidor
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  INICIANDO SERVIDOR                   ${NC}"
echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}Archivo: $FILE_TO_SHARE (${FILE_SIZE_MB} MB)${NC}"
echo -e "${GREEN}Host: $SERVER_HOST:$SERVER_PORT${NC}"
echo -e "${GREEN}Clientes máximos: $MAX_CLIENTS${NC}"
echo ""
echo -e "${YELLOW}Presiona Ctrl+C para detener el servidor${NC}"
echo ""

python3 server.py "$FILE_TO_SHARE" \
    --host "$SERVER_HOST" \
    --port "$SERVER_PORT" \
    --max-clients "$MAX_CLIENTS" \
    --chunk-size "$CHUNK_SIZE"
