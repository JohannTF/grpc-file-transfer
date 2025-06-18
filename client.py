#!/usr/bin/env python3
"""
Cliente RPC 
"""

import asyncio
import sys
import time
import argparse
from pathlib import Path
import hashlib
import logging

# Configurar logging simple
logging.basicConfig(
    level=logging.INFO,
    format='[%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

# Agregar src al path
sys.path.insert(0, str(Path(__file__).parent / "src"))

import grpc


class RCPSimpleClient:
    """Cliente para descargar archivos."""
    
    def __init__(self, client_id: str = None):
        self.client_id = client_id or f"client_{int(time.time())}"
        self.channel = None
        self.stub = None
        
        # Estadísticas
        self.start_time = None
        self.chunks_downloaded = 0
        self.bytes_downloaded = 0
        self.total_chunks = 0
        self.failed_chunks = 0
    
    async def connect(self, host: str, port: int):
        """Conecta al servidor gRPC."""
        server_address = f"{host}:{port}"
        
        self.channel = grpc.aio.insecure_channel(
            server_address,
            options=[
                ('grpc.keepalive_time_ms', 30000),
                ('grpc.max_receive_message_length', 5 * 1024 * 1024),
            ]
        )
        
        try:
            from src.generated import file_transfer_pb2_grpc
            self.stub = file_transfer_pb2_grpc.FileTransferServiceStub(self.channel)
            logger.info(f"Connected to server at {server_address}")
        except ImportError:
            logger.error("Generated gRPC code not found")
            raise
    
    async def disconnect(self):
        """Desconecta del servidor."""
        if self.channel:
            await self.channel.close()
    
    async def get_file_info(self):
        """Obtiene información del archivo disponible."""
        try:
            from src.generated import file_transfer_pb2
            
            request = file_transfer_pb2.FileInfoRequest()
            response = await self.stub.GetFileInfo(request)
            
            if not response.file_ready:
                logger.error("File not available on server")
                return None
            
            logger.info(f"File info: {response.filename} ({response.file_size:,} bytes, {response.total_chunks:,} chunks)")
            return response
            
        except Exception as e:
            logger.error(f"Error getting file info: {e}")
            return None
    
    async def download_chunk(self, chunk_id: int, max_retries: int = 3):
        """Descarga un chunk específico."""
        for attempt in range(max_retries + 1):
            try:
                from src.generated import file_transfer_pb2
                
                request = file_transfer_pb2.ChunkRequest(
                    chunk_id=chunk_id
                )

                response = await self.stub.GetChunk(request)
                
                if not response.success:
                    if attempt < max_retries:
                        await asyncio.sleep(0.1 * (attempt + 1))
                        continue
                    else:
                        self.failed_chunks += 1
                        return None
                
                return response
                
            except Exception:
                if attempt < max_retries:
                    await asyncio.sleep(0.2 * (attempt + 1))
                else:
                    self.failed_chunks += 1
                    return None
        
        return None
    
    async def download_file(self, output_path: str, max_concurrent: int = 10) -> bool:
        """Descarga el archivo completo."""
        file_info = await self.get_file_info()
        if not file_info:
            return False
        
        self.total_chunks = file_info.total_chunks
        self.start_time = time.time()
        
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"Starting download: {file_info.filename} -> {output_path}")
        
        # Semáforo para controlar concurrencia
        semaphore = asyncio.Semaphore(max_concurrent)
        chunks_data = [None] * self.total_chunks
        
        async def download_chunk_worker(chunk_id: int):
            async with semaphore:
                chunk_response = await self.download_chunk(chunk_id)
                if chunk_response:
                    chunks_data[chunk_id] = chunk_response.data
                    self.chunks_downloaded += 1
                    self.bytes_downloaded += len(chunk_response.data)
                    
                    # Progress cada 200 chunks (con chunks de 4MB es más frecuente)
                    if (chunk_id + 1) % 200 == 0 or chunk_response.is_last:
                        progress = (self.chunks_downloaded / self.total_chunks) * 100
                        logger.info(f"Progress: {progress:.0f}% ({self.chunks_downloaded}/{self.total_chunks})")
                    
                    return True
                return False
        
        # Crear tareas para todos los chunks
        tasks = [
            asyncio.create_task(download_chunk_worker(chunk_id))
            for chunk_id in range(self.total_chunks)
        ]
        
        # Ejecutar todas las tareas
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Verificar resultados
        successful_chunks = sum(1 for result in results if result is True)
        
        if successful_chunks != self.total_chunks:
            logger.error(f"Download incomplete: {successful_chunks}/{self.total_chunks} chunks")
            return False
        
        # Escribir archivo completo
        logger.info("Writing file...")
        
        try:
            with open(output_file, 'wb') as f:
                for chunk_data in chunks_data:
                    if chunk_data:
                        f.write(chunk_data)
            
            # Validar integridad del archivo descargado
            logger.info("Validating file integrity...")
            downloaded_checksum = self._calculate_file_checksum(output_file)
            
            if downloaded_checksum != file_info.file_checksum:
                logger.error(f"File integrity check failed! Expected: {file_info.file_checksum}, Got: {downloaded_checksum}")
                return False
            
            # Estadísticas finales
            elapsed = time.time() - self.start_time
            avg_speed = (self.bytes_downloaded / 1024 / 1024) / elapsed if elapsed > 0 else 0
            
            logger.info(f"Download completed: {self.bytes_downloaded:,} bytes in {elapsed:.2f}s ({avg_speed:.2f} MB/s)")
            logger.info("File integrity verified")
            return True
                
        except Exception as e:
            logger.error(f"Error writing file: {e}")
            return False
    
    def _calculate_file_checksum(self, file_path: Path) -> str:
        """Calcula el checksum SHA-256 del archivo descargado."""
        hash_sha256 = hashlib.sha256()
        
        with open(file_path, 'rb') as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                hash_sha256.update(chunk)
        
        return hash_sha256.hexdigest()


async def main():
    """Función principal del cliente."""
    parser = argparse.ArgumentParser(description="RPC Client")
    parser.add_argument("--host", default="localhost", help="Server host (default: localhost)")
    parser.add_argument("--port", type=int, default=50051, help="Server port (default: 50051)")
    parser.add_argument("--output", required=True, help="Output file")
    parser.add_argument("--client-id", help="Client ID (default: auto-generated)")
    parser.add_argument("--concurrent", type=int, default=5, help="Concurrent chunks (default: 5")
    
    args = parser.parse_args()
    
    client = RCPSimpleClient(args.client_id)
    
    try:
        await client.connect(args.host, args.port)
        success = await client.download_file(args.output, args.concurrent)
        
        if success:
            logger.info("Download successful!")
            sys.exit(0)
        else:
            logger.error("Download failed")
            sys.exit(1)
            
    except KeyboardInterrupt:
        logger.info("Download cancelled")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)
    finally:
        await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nCancelled")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
