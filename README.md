# RCP: Transferencia de Archivos con Chunks Pre-computados

Sistema de transferencia de archivos gRPC optimizado que utiliza chunks pre-computados de 4MB para máximo rendimiento.

## 📋 Requisitos

- Python 3.8+
- Docker (opcional para testing)
- Archivo de datos en formato MP4, AVI, ZIP, PDF, etc.

## 🚀 Instalación y Ejecución

Clonar el repositorio y comenzar:

```bash
# 1. Clona el repositorio
git clone https://github.com/JohannTF/grpc-file-transfer.git

# 2. Ingresa al directorio del proyecto
cd grpc-file-transfer
```

Luego, sigue las instrucciones de las secciones siguientes para configurar el entorno y ejecutar el sistema según tu preferencia.

### **Opción 1: Entorno Local para Desarrollo**

```bash
# 1. Crear entorno virtual
python -m venv env
# Windows:
env\Scripts\activate
# Linux:
source env/bin/activate

# 2. Instalar dependencias
pip install --upgrade pip
pip install -r requirements.txt

# 3. Crear estructura y generar código gRPC
# Windows:
mkdir data
mkdir src\generated
echo . > src\generated\__init__.py
# Linux:
mkdir -p data src/generated && touch src/generated/__init__.py

# 3. Generar código gRPC
python -m grpc_tools.protoc --proto_path=protos --python_out=src/generated --grpc_python_out=src/generated protos/file_transfer.proto

# 4. Preparar archivo de datos
# Copiar o mover tu archivo MP4 a la carpeta 'data/' (ya sea con comandos o manualmente)
# Windows:
copy "mi_video.mp4" data\
# Linux:
cp mi_video.mp4 data/

# 5. Ejecutar servidor (Terminal 1)
python server.py data/mi_video.mp4

# 6. Ejecutar cliente (Terminal 2)
python client.py --output mi_video_descargado.mp4
```

### **Opción 2: Entorno Real (Sin Virtual Environment)**

```bash
# 1. Instalar dependencias
pip install -r requirements.txt

# 2. Crear estructura
# Windows:
mkdir data
mkdir src\generated
echo . > src\generated\__init__.py
# Linux:
mkdir -p data src/generated && touch src/generated/__init__.py

# 3. Generar código gRPC
python -m grpc_tools.protoc --proto_path=protos --python_out=src/generated --grpc_python_out=src/generated protos/file_transfer.proto

# 4. Ejecutar
python server.py tu_archivo.mp4 --host 0.0.0.0
python client.py --host IP_DEL_SERVIDOR --output archivo_descargado.mp4
```

### **Opción 3: Testing con Docker (Múltiples Clientes)**

```bash
# 1. Preparar archivo de datos (LEER LA SECCIÓN DE CONFIGURACIÓN IMPORTANTE MÁS ABAJO)
# Colocar archivo en directorio data/ con el nombre correcto en Dockerfile.server

# 2. Ejecutar con docker-compose
docker-compose up --build

# 3. O ejecutar manualmente
# Servidor:
# Windows:
docker run -p 50051:50051 -v "$(pws)/data:/app/data:ro" --name rcp-server rcp-server
# Linux:
docker run -p 50051:50051 -v "$(pwd)/data:/app/data:ro" --name rcp-server rcp-server

# Clientes:
docker run --link rcp-server:server --name client1 rcp-client
docker run --link rcp-server:server --name client2 rcp-client
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
# Ver ayuda
python server.py --help
python client.py --help

# Servidor con configuraciones específicas
python server.py mi_archivo.mp4 --host 0.0.0.0 --port 8080 --chunk-size 8388608

# Cliente con configuraciones específicas
python client.py --host 192.168.1.100 --port 8080 --concurrent 10 --output mi_descarga.mp4

# Limpiar contenedores Docker
docker-compose down -v
docker container prune -f
docker image prune -f
```