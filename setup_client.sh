#!/bin/bash

#################################################################
# SETUP AUTOMÁTICO PARA CLIENTE - Ubuntu
# Sistema de transferencia de archivos gRPC
#################################################################

# ===== VARIABLES DE CONFIGURACIÓN =====

# Configuración del servidor
SERVER_HOST="10.147.20.4"  # Cambiar por la IP del servidor
SERVER_PORT="50051"

# Archivo de salida
OUTPUT_FILE="archivo_descargado.mp4"  # Cambiar la extensión según el archivo del servidor

# ID del cliente (opcional, se genera automáticamente si no se especifica)
CLIENT_ID=""  # Dejar vacío para auto-generar

# Concurrencia máxima (chunks simultáneos)
MAX_CONCURRENT="100"

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
echo -e "${BLUE}  CONFIGURACIÓN AUTOMÁTICA CLIENTE    ${NC}"
echo -e "${BLUE}========================================${NC}"

# Función para verificar errores
check_error() {
    if [ $? -ne 0 ]; then
        echo -e "${RED}Error: $1${NC}"
        exit 1
    fi
}

# Cambiar al directorio del proyecto
cd "$PROJECT_DIR"
check_error "No se pudo acceder al directorio del proyecto"

echo -e "${YELLOW}Configurando entorno...${NC}"

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

# Crear estructura y generar código gRPC si es necesario
echo -e "${YELLOW}Preparando estructura...${NC}"
mkdir -p data src/generated
touch src/generated/__init__.py

if [ ! -f "src/generated/file_transfer_pb2.py" ]; then
    echo -e "${YELLOW}Generando código gRPC...${NC}"
    python3 -m grpc_tools.protoc \
        --proto_path=protos \
        --python_out=src/generated \
        --grpc_python_out=src/generated \
        protos/file_transfer.proto > /dev/null 2>&1
    check_error "Error generando código gRPC"
fi

# Verificar conectividad con el servidor
echo -e "${YELLOW}Verificando servidor...${NC}"
if command -v nc &> /dev/null; then
    nc -z "$SERVER_HOST" "$SERVER_PORT" 2>/dev/null
    if [ $? -ne 0 ]; then
        echo -e "${YELLOW}⚠ No se puede conectar al servidor en $SERVER_HOST:$SERVER_PORT${NC}"
        echo -e "${YELLOW}  Verifica que el servidor esté ejecutándose${NC}"
    fi
fi

# Verificar si el archivo de salida ya existe
if [ -f "$OUTPUT_FILE" ]; then
    echo -e "${YELLOW}⚠ El archivo $OUTPUT_FILE ya existe${NC}"
    read -p "¿Deseas sobrescribirlo? (y/n): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo -e "${RED}Descarga cancelada${NC}"
        exit 1
    fi
    rm -f "$OUTPUT_FILE"
fi

# Mostrar información de la descarga
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  INICIANDO DESCARGA                   ${NC}"
echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}Servidor: $SERVER_HOST:$SERVER_PORT${NC}"
echo -e "${GREEN}Archivo de salida: $OUTPUT_FILE${NC}"
echo -e "${GREEN}Concurrencia máxima: $MAX_CONCURRENT chunks${NC}"
if [ -n "$CLIENT_ID" ]; then
    echo -e "${GREEN}ID del cliente: $CLIENT_ID${NC}"
fi
echo ""
echo -e "${YELLOW}Presiona Ctrl+C para cancelar la descarga${NC}"
echo ""

# Construir comando del cliente
CLIENT_CMD="python3 client.py --host $SERVER_HOST --port $SERVER_PORT --output $OUTPUT_FILE --max-concurrent $MAX_CONCURRENT"

if [ -n "$CLIENT_ID" ]; then
    CLIENT_CMD="$CLIENT_CMD --client-id $CLIENT_ID"
fi

# Ejecutar cliente
eval $CLIENT_CMD
