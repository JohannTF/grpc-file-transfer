# RCP: Transferencia de Archivos

Sistema de transferencia de archivos gRPC.

## 🚀 Instalación y Ejecución

### **Opción 1: Setup Automático (Ubuntu/Linux)**

```bash
# 1. Clonar el repositorio
git clone https://github.com/JohannTF/grpc-file-transfer.git
cd grpc-file-transfer

# 2. Configurar variables en los scripts
# Editar setup_server.sh y setup_client.sh según tu entorno

# 3. Ejecutar servidor (Terminal 1)
chmod +x setup_server.sh
./setup_server.sh

# 4. Ejecutar cliente (Terminal 2)
chmod +x setup_client.sh
./setup_client.sh
```

**⚠️ Nota**: Si obtienes errores de formato al ejecutar los scripts, ejecuta:
```bash
dos2unix setup_server.sh setup_client.sh
```

### **Opción 2: Configuración Manual (Linux)**

```bash
# 1. Instalar dependencias
sudo apt update
sudo apt install -y python3 python3-pip
pip3 install -r requirements.txt

# 2. Crear estructura y generar código gRPC
mkdir -p data src/generated && touch src/generated/__init__.py
python3 -m grpc_tools.protoc --proto_path=protos --python_out=src/generated --grpc_python_out=src/generated protos/file_transfer.proto

# 3. Ejecutar servidor (Terminal 1)
python3 server.py tu_archivo.mp4 --host 0.0.0.0

# 4. Ejecutar cliente (Terminal 2)
python3 client.py --host IP_DEL_SERVIDOR --output archivo_descargado.mp4
```

## ⚠️ Configuración de Scripts Automáticos

### **Variables del Servidor (setup_server.sh)**

```bash
FILE_TO_SHARE="mi_video.mp4"                  # Archivo a compartir (ruta relativa)
SERVER_HOST="0.0.0.0"                         # Host del servidor
SERVER_PORT="50051"                           # Puerto del servidor
MAX_CLIENTS="10"                              # Máx. clientes simultáneos
CHUNK_SIZE="4194304"                          # Tamaño de chunk (4MB)
```

### **Variables del Cliente (setup_client.sh)**

```bash
SERVER_HOST="192.168.1.100"              # IP del servidor
SERVER_PORT="50051"                      # Puerto del servidor
OUTPUT_FILE="video_descargado.mp4"       # Archivo de salida
MAX_CONCURRENT="20"                      # Concurrencia máxima
CLIENT_ID="mi_cliente"                   # ID del cliente (opcional)
```

## 🎯 Formatos Soportados

- **Video**: MP4, AVI, MKV, MOV, WMV, FLV
- **Otros**: ZIP, PDF, EXE, DAT (cualquier archivo binario)

## 🔧 Comandos Útiles

```bash
# Ver ayuda
python3 server.py --help
python3 client.py --help

# Servidor con configuraciones específicas
python3 server.py mi_archivo.mp4 --host 0.0.0.0 --port 8080 --chunk-size 8388608 --max-clients 15

# Cliente con configuraciones específicas
python3 client.py --host 192.168.1.100 --port 8080 --max-concurrent 20 --output mi_descarga.mp4

# Dar permisos de ejecución a los scripts
chmod +x setup_server.sh setup_client.sh

# Verificar conectividad
nc -z IP_SERVIDOR PUERTO
```