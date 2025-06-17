# RCP: Transferencia de Archivos con Chunks Pre-computados

Sistema de transferencia de archivos gRPC optimizado que utiliza chunks pre-computados de 4MB para máximo rendimiento.

## 📋 Requisitos

- Python 3.8+
- Docker (opcional para testing)
- Archivo de datos en formato MP4, AVI, ZIP, PDF, etc.

## 🚀 Instalación y Ejecución

### **Opción 1: Entorno Local para Desarrollo**

```bash
# 1. Crear entorno virtual
python -m venv env
env\Scripts\activate  # Windows
# source env/bin/activate  # Linux

# 2. Instalar dependencias
pip install --upgrade pip
pip install -r requirements.txt

# 3. Generar código gRPC
mkdir data src\generated 2>nul  # Windows
# mkdir -p data src/generated  # Linux
echo. > src\generated\__init__.py  # Windows
# touch src/generated/__init__.py  # Linux
python -m grpc_tools.protoc --proto_path=protos --python_out=src/generated --grpc_python_out=src/generated protos/file_transfer.proto

# 4. Preparar archivo de datos
# Copiar o mover tu archivo MP4/video a la carpeta data/ (ya sea con comandos o manualmente)
copy "mi_video.mp4" data\  # Windows
# cp mi_video.mp4 data/  # Linux

# 5. Ejecutar servidor (Terminal 1)
python server.py data/mi_video.mp4

# 6. Ejecutar cliente (Terminal 2)
python client.py --output mi_video_descargado.mp4
```

### **Opción 2: Entorno Real (Sin Virtual Environment)**

```bash
# 1. Instalar dependencias globalmente
pip install --upgrade pip
pip install grpcio grpcio-tools protobuf aiofiles psutil structlog colorama click pydantic

# 2. Generar código gRPC
python -m grpc_tools.protoc --proto_path=protos --python_out=src/generated --grpc_python_out=src/generated protos/file_transfer.proto

# 3. Preparar archivo y ejecutar
mkdir data
copy "tu_video.mp4" data\
python server.py data/video_grande.mp4 --host 0.0.0.0
python client.py --host IP_DEL_SERVIDOR --output video_descargado.mp4
```

### **Opción 3: Testing con Docker (Múltiples Clientes)**

```bash
# 1. Preparar archivo de datos (LEER LA SECCIÓN DE CONFIGURACIÓN IMPORTANTE MÁS ABAJO)

# 2. Construir imágenes
docker build -f Dockerfile.server -t rcp-server .
docker build -f Dockerfile.client -t rcp-client .

# 3. Ejecutar 5 clientes simultáneos
docker-compose up --build

# 4. O ejecutar manualmente
# Servidor:
docker run -p 50051:50051 -v "%cd%/data:/app/data:ro" --name rcp-server rcp-server

# Clientes (en terminales separadas):
# Asegurate de asignar el nombre de salida de tu archivo y la extensión correctamente
docker run --link rcp-server:server --name client1 rcp-client python client.py --host server --output NOMBRE_SALIDA.extensión
docker run --link rcp-server:server --name client2 rcp-client python client.py --host server --output NOMBRE_SALIDA.extensión
```

## ⚠️ Configuración Importante

### **Servidor - Especificar Archivo Correcto**

En `Dockerfile.server`, línea 35:
```dockerfile
CMD ["python", "server.py", "/app/data/TU_ARCHIVO.mp4", "--host", "0.0.0.0", "--port", "50051"]
```

Cambiar `TU_ARCHIVO.mp4` por el nombre real de tu archivo que guardaste en la carpeta data

### **Clientes - Especificar Salida Correcta**

Si el servidor comparte un archivo `.mp4`, los clientes deben usar:
```bash
python client.py --output video_descargado.mp4
```

Si es un archivo `.zip`:
```bash
python client.py --output archivo_descargado.zip
```

## 🎯 Formatos Soportados

- **Video**: MP4, AVI, MKV, MOV, WMV, FLV
- **Otros**: ZIP, PDF, EXE, DAT (cualquier archivo binario)

## 🔧 Comandos Útiles

```bash
# Ver ayuda del servidor
python server.py --help

# Servidor con puerto personalizado
python server.py data/mi_video.mp4 --port 8080

# Cliente con más concurrencia
python client.py --concurrent 20 --output mi_descarga.mp4

# Limpiar contenedores
# Para docker-compose
docker-compose down -v
# Para Testing con docker
docker rm rcp-server client1 client2 client3 client4 client5 2>/dev/null
docker rmi rcp-server rcp-client 2>/dev/null
```