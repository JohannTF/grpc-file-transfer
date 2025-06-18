#!/usr/bin/env python3
"""
Cliente RPC optimizado para descarga de archivos grandes
"""

import argparse
import asyncio
import hashlib
import logging
import os
import sys
import time
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Optional

import grpc
from grpc import aio

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

# Agregar src al path
sys.path.insert(0, str(Path(__file__).parent / "src"))

try:
    from generated import file_transfer_pb2
    from generated import file_transfer_pb2_grpc
except ImportError:
    logger.error("Generated gRPC code not found. Run: python generate_grpc.py")
    sys.exit(1)


class RPCFileClient:
    """Cliente RPC optimizado para descarga de archivos grandes."""
    
    def __init__(
        self,
        server_host: str,
        server_port: int,
        output_dir: str = "downloads",
        max_workers: int = 10,
        chunk_size: int = 4 * 1024 * 1024
    ):
        """
        Inicializa el cliente.
        
        Args:
            server_host: Host del servidor
            server_port: Puerto del servidor
            output_dir: Directorio de descarga
            max_workers: Máximo descargas concurrentes
            chunk_size: Tamaño de chunk esperado
        """
        self.server_host = server_host
        self.server_port = server_port
        self.output_dir = Path(output_dir)
        self.max_workers = max_workers
        self.chunk_size = chunk_size
        self.channel = None
        self.stub = None
        self.file_info = None
        self.session_id = None
        self.client_id = f"client_{os.getpid()}_{int(time.time())}"
        self.download_stats = defaultdict(int)
        self.chunk_queue = asyncio.Queue()
        self.semaphore = asyncio.Semaphore(max_workers)
        self.file_handle = None
        self.start_time = time.time()
    
    async def connect(self):
        """Establece conexión con el servidor."""
        target = f"{self.server_host}:{self.server_port}"
        self.channel = aio.insecure_channel(
            target,
            options=[
                ('grpc.max_receive_message_length', -1),  # Sin límite
                ('grpc.max_send_message_length', -1),     # Sin límite
                ('grpc.keepalive_time_ms', 30000),
            ]
        )
        self.stub = file_transfer_pb2_grpc.FileTransferServiceStub(self.channel)
        
        # Registrar cliente
        response = await self.stub.RegisterClient(
            file_transfer_pb2.ClientRegisterRequest(
                client_id=self.client_id,
                client_info=f"RPC Client v2.0 (workers={self.max_workers})"
            )
        )
        self.session_id = response.session_id
        logger.info(f"Conexión establecida. Session ID: {self.session_id}")
    
    async def get_file_info(self):
        """Obtiene información del archivo disponible."""
        request = file_transfer_pb2.FileInfoRequest(client_id=self.client_id)
        response = await self.stub.GetFileInfo(request)

        if not response.file_ready:
            logger.error("Archivo no disponible en el servidor")
            return False

        self.file_info = {
            "filename": response.filename,
            "file_size": response.file_size,
            "total_chunks": response.total_chunks,
            "chunk_size": response.chunk_size,
            "file_checksum": response.file_checksum,
            "file_type": response.file_type
        }

        logger.info(
            f"Archivo disponible: {self.file_info['filename']} "
            f"({self.file_info['file_size']:,} bytes, "
            f"{self.file_info['total_chunks']} chunks)"
        )

        # Preparar archivo de salida
        self.output_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.output_dir / self.file_info['filename']
        self.file_handle = open(output_path, 'wb')
        self.file_handle.truncate(self.file_info['file_size'])

        return True

    
    async def download_chunk(self, chunk_id: int):
        """Descarga un chunk específico."""
        async with self.semaphore:
            request = file_transfer_pb2.ChunkRequest(
                client_id=self.client_id,
                chunk_id=chunk_id
            )
            
            try:
                start_time = time.time()
                response = await self.stub.GetChunk(request)
                
                if not response.success:
                    logger.error(f"Error en chunk {chunk_id}: {response.error_message}")
                    return False
                
                # Escribir chunk en posición correcta
                self.file_handle.seek(response.offset)
                self.file_handle.write(response.data)
                
                # Calcular estadísticas
                latency = (time.time() - start_time) * 1000
                self.download_stats['chunks_downloaded'] += 1
                self.download_stats['bytes_downloaded'] += response.size
                self.download_stats['total_latency'] += latency
                
                # Log cada 50 chunks
                if self.download_stats['chunks_downloaded'] % 50 == 0:
                    self._log_progress()
                
                return True
            except grpc.aio.AioRpcError as e:
                logger.error(f"Error gRPC en chunk {chunk_id}: {e.details()}")
                return False
    
    async def download_file(self):
        """Descarga el archivo completo usando workers concurrentes."""
        if not self.file_info:
            logger.error("Información de archivo no disponible")
            return False
        
        total_chunks = self.file_info['total_chunks']
        logger.info(f"Iniciando descarga con {self.max_workers} workers...")
        
        # Crear tareas de descarga
        tasks = []
        for chunk_id in range(total_chunks):
            task = asyncio.create_task(self.download_chunk(chunk_id))
            tasks.append(task)
        
        # Esperar a que todas las descargas completen
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Verificar errores
        success_count = sum(1 for r in results if r is True)
        error_count = total_chunks - success_count
        
        if error_count > 0:
            logger.error(f"Descarga completa con errores: {error_count}/{total_chunks} chunks fallidos")
            return False
        
        # Finalizar descarga
        self.file_handle.close()
        await self._verify_download()
        self._log_final_stats()
        
        return True
    
    async def _verify_download(self):
        """Verifica la integridad del archivo descargado."""
        if not self.file_info or not self.file_info['file_checksum']:
            return
        
        file_path = self.output_dir / self.file_info['filename']
        logger.info("Verificando integridad del archivo...")
        
        # Calcular checksum
        sha256 = hashlib.sha256()
        with open(file_path, 'rb') as f:
            while chunk := f.read(8192):
                sha256.update(chunk)
        
        file_checksum = sha256.hexdigest()
        
        if file_checksum == self.file_info['file_checksum']:
            logger.info("✓ Integridad verificada: checksum coincide")
        else:
            logger.error("✗ Error de integridad: checksum no coincide")
            logger.error(f"Esperado: {self.file_info['file_checksum']}")
            logger.error(f"Obtenido: {file_checksum}")
    
    def _log_progress(self):
        """Registra el progreso de la descarga."""
        downloaded = self.download_stats['chunks_downloaded']
        total = self.file_info['total_chunks']
        percent = (downloaded / total) * 100
        
        # Calcular velocidad
        elapsed = time.time() - self.start_time
        speed = (self.download_stats['bytes_downloaded'] / 1024 / 1024) / elapsed
        
        # Calcular latencia promedio
        avg_latency = self.download_stats['total_latency'] / downloaded if downloaded > 0 else 0
        
        logger.info(
            f"Progreso: {percent:.1f}% | "
            f"Chunks: {downloaded}/{total} | "
            f"Velocidad: {speed:.1f} MB/s | "
            f"Latencia: {avg_latency:.1f} ms"
        )
    
    def _log_final_stats(self):
        """Registra estadísticas finales de la descarga."""
        total_time = time.time() - self.start_time
        file_size_mb = self.download_stats['bytes_downloaded'] / 1024 / 1024
        avg_speed = file_size_mb / total_time
        
        logger.info("Descarga completada exitosamente!")
        logger.info(f"Archivo: {self.file_info['filename']}")
        logger.info(f"Tamaño: {file_size_mb:.2f} MB")
        logger.info(f"Tiempo total: {total_time:.2f} segundos")
        logger.info(f"Velocidad promedio: {avg_speed:.2f} MB/s")
        logger.info(f"Chunks descargados: {self.download_stats['chunks_downloaded']}")
    
    async def close(self):
        """Cierra la conexión y libera recursos."""
        if self.file_handle:
            self.file_handle.close()
        if self.channel:
            await self.channel.close()
        logger.info("Conexión cerrada")


async def main():
    parser = argparse.ArgumentParser(description="Cliente RPC para descarga de archivos")
    parser.add_argument("--host", default="localhost", help="Servidor host (default: localhost)")
    parser.add_argument("--port", type=int, default=50051, help="Servidor port (default: 50051)")
    parser.add_argument("--output", default="downloads", help="Directorio de descarga (default: downloads)")
    parser.add_argument("--workers", type=int, default=10, help="Máximo workers concurrentes (default: 10)")
    
    args = parser.parse_args()
    
    client = RPCFileClient(
        server_host=args.host,
        server_port=args.port,
        output_dir=args.output,
        max_workers=args.workers
    )
    
    try:
        await client.connect()
        
        if not await client.get_file_info():
            return
        
        await client.download_file()
        
    except KeyboardInterrupt:
        logger.info("Descarga cancelada por el usuario")
    except Exception as e:
        logger.exception(f"Error crítico: {e}")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())