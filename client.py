#!/usr/bin/env python3
"""
Cliente RCP híbrido optimizado
Combina pool de conexiones con concurrencia interna controlada
"""

import asyncio
import sys
import time
import argparse
from pathlib import Path
import logging

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

# Agregar src al path
sys.path.insert(0, str(Path(__file__).parent / "src"))
sys.path.insert(0, str(Path(__file__).parent / "src" / "generated"))

import grpc

# Importar generated code con fallbacks
try:
    from src.generated import file_transfer_pb2, file_transfer_pb2_grpc
except ImportError:
    try:
        import file_transfer_pb2
        import file_transfer_pb2_grpc
    except ImportError:
        logger.error("Generated gRPC code not found")
        sys.exit(1)


class Client:
    """Cliente optimizado para pool con concurrencia controlada."""
    
    def __init__(self, client_id: str = None, concurrent_chunks: int = 3):
        self.client_id = client_id or f"client_{int(time.time())}"
        self.concurrent_chunks = concurrent_chunks
        self.channel = None
        self.stub = None
        
        # Estadísticas
        self.start_time = None
        self.chunks_downloaded = 0
        self.bytes_downloaded = 0
        self.total_chunks = 0
        self.failed_chunks = 0
        
        # Control de estado
        self.download_active = False
        self.last_progress_time = 0
    
    async def connect(self, host: str, port: int):
        """Conecta al servidor con configuración optimizada."""
        server_address = f"{host}:{port}"
        
        # Configuración agresiva para velocidad
        self.channel = grpc.aio.insecure_channel(
            server_address,
            options=[
                ('grpc.keepalive_time_ms', 30000),  # 30s - más agresivo
                ('grpc.keepalive_timeout_ms', 10000),  # 10s timeout
                ('grpc.keepalive_permit_without_calls', True),
                ('grpc.max_receive_message_length', 8 * 1024 * 1024),  # 8MB
                ('grpc.max_send_message_length', 1024 * 1024),
                ('grpc.http2.max_pings_without_data', 0),
                ('grpc.http2.min_time_between_pings_ms', 10000),
                ('grpc.enable_retries', 1),
                ('grpc.max_retry_attempts', 3),
            ]
        )
        
        self.stub = file_transfer_pb2_grpc.FileTransferServiceStub(self.channel)
        logger.info(f"[{self.client_id}] Connected to server at {server_address}")
    
    async def disconnect(self):
        """Desconecta del servidor."""
        if self.channel:
            await self.channel.close()
    
    async def get_file_info(self):
        """Obtiene información del archivo con timeout."""
        try:
            request = file_transfer_pb2.FileInfoRequest()
            response = await asyncio.wait_for(
                self.stub.GetFileInfo(request),
                timeout=30  # 30s timeout
            )
            
            if not response.file_ready:
                logger.error(f"[{self.client_id}] File not available on server")
                return None
            
            logger.info(f"[{self.client_id}] File: {response.filename} ({response.file_size:,} bytes, {response.total_chunks:,} chunks)")
            return response
            
        except Exception as e:
            logger.error(f"[{self.client_id}] Error getting file info: {e}")
            return None
    
    async def download_chunk(self, chunk_id: int, max_retries: int = 2):
        """Descarga un chunk con timeout corto y reintentos mínimos."""
        for attempt in range(max_retries + 1):
            try:
                request = file_transfer_pb2.ChunkRequest(chunk_id=chunk_id)
                
                # Timeout corto para detectar problemas rápido
                response = await asyncio.wait_for(
                    self.stub.GetChunk(request),
                    timeout=45  # 45s timeout
                )
                
                if not response.success:
                    if "Server busy" in response.error_message:
                        # Pool lleno - esperar menos tiempo
                        logger.warning(f"[{self.client_id}] Pool busy for chunk {chunk_id}")
                        await asyncio.sleep(5 + attempt)
                        continue
                    else:
                        logger.warning(f"[{self.client_id}] Chunk {chunk_id} failed: {response.error_message}")
                        self.failed_chunks += 1
                        return None
                
                return response
                
            except asyncio.TimeoutError:
                logger.warning(f"[{self.client_id}] Timeout chunk {chunk_id}, attempt {attempt + 1}")
                if attempt < max_retries:
                    await asyncio.sleep(2)
                else:
                    self.failed_chunks += 1
                    return None
            except Exception as e:
                logger.warning(f"[{self.client_id}] Error chunk {chunk_id}: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(1)
                else:
                    self.failed_chunks += 1
                    return None
        
        return None
    
    async def download_file(self, output_path: str) -> bool:
        """Descarga con concurrencia controlada optimizada."""
        file_info = await self.get_file_info()
        if not file_info:
            return False
        
        self.total_chunks = file_info.total_chunks
        self.start_time = time.time()
        self.download_active = True
        
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"[{self.client_id}] Starting download")
        
        # Semáforo para controlar concurrencia
        semaphore = asyncio.Semaphore(self.concurrent_chunks)
        chunks_data = [None] * self.total_chunks
        download_status = [False] * self.total_chunks
        
        # Monitor de progreso
        progress_task = asyncio.create_task(self._progress_monitor())
        
        async def download_chunk_worker(chunk_id: int):
            async with semaphore:
                if not self.download_active:
                    return False
                    
                chunk_response = await self.download_chunk(chunk_id)
                
                if chunk_response:
                    chunks_data[chunk_id] = chunk_response.data
                    download_status[chunk_id] = True
                    self.chunks_downloaded += 1
                    self.bytes_downloaded += len(chunk_response.data)
                    return True
                else:
                    download_status[chunk_id] = False
                    return False
        
        try:
            # Crear tareas para todos los chunks
            tasks = [
                asyncio.create_task(download_chunk_worker(chunk_id))
                for chunk_id in range(self.total_chunks)
            ]
            
            # Ejecutar con timeout global
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=1800  # 30 minutos máximo
            )
            
            self.download_active = False
            progress_task.cancel()
            
            # Verificar resultados
            successful_chunks = sum(1 for result in results if result is True)
            
            if successful_chunks != self.total_chunks:
                logger.error(f"[{self.client_id}] Download incomplete: {successful_chunks}/{self.total_chunks} chunks")
                
                # Mostrar chunks fallidos para debug
                failed_chunks = [i for i, status in enumerate(download_status) if not status]
                if failed_chunks:
                    logger.error(f"[{self.client_id}] Failed chunks: {failed_chunks[:10]}..." if len(failed_chunks) > 10 else f"Failed chunks: {failed_chunks}")
                
                return False
            
            # Escribir archivo
            logger.info(f"[{self.client_id}] Writing file...")
            
            with open(output_file, 'wb') as f:
                for chunk_data in chunks_data:
                    if chunk_data:
                        f.write(chunk_data)
            
            # Verificar tamaño
            downloaded_size = output_file.stat().st_size
            expected_size = file_info.file_size
            
            if downloaded_size != expected_size:
                logger.error(f"[{self.client_id}] Size mismatch! Expected: {expected_size:,}, Got: {downloaded_size:,}")
                return False
            
            # Estadísticas finales
            elapsed = time.time() - self.start_time
            avg_speed = (self.bytes_downloaded / 1024 / 1024) / elapsed if elapsed > 0 else 0
            
            logger.info(f"[{self.client_id}] ✅ Download completed: {self.bytes_downloaded:,} bytes in {elapsed:.1f}s ({avg_speed:.1f} MB/s)")
            return True
                
        except asyncio.TimeoutError:
            self.download_active = False
            progress_task.cancel()
            logger.error(f"[{self.client_id}] Download timeout after 30 minutes")
            return False
        except Exception as e:
            self.download_active = False
            progress_task.cancel()
            logger.error(f"[{self.client_id}] Download error: {e}")
            return False
    
    async def _progress_monitor(self):
        """Monitor de progreso cada 10 segundos."""
        try:
            while self.download_active:
                await asyncio.sleep(10)
                if self.download_active and self.total_chunks > 0:
                    progress = (self.chunks_downloaded / self.total_chunks) * 100
                    elapsed = time.time() - self.start_time
                    speed = (self.bytes_downloaded / 1024 / 1024) / elapsed if elapsed > 0 else 0
                    
                    logger.info(f"[{self.client_id}] Progress: {progress:.1f}% ({self.chunks_downloaded}/{self.total_chunks}) - {speed:.1f} MB/s")
                    
                    # Detectar si está atascado
                    if time.time() - self.last_progress_time > 120:  # 2 minutos sin progreso
                        if self.chunks_downloaded == getattr(self, '_last_chunks', 0):
                            logger.warning(f"[{self.client_id}] Download seems stuck, continuing...")
                    
                    self._last_chunks = self.chunks_downloaded
                    self.last_progress_time = time.time()
        except asyncio.CancelledError:
            pass


async def main():
    """Función principal del cliente."""
    parser = argparse.ArgumentParser(description="Optimized Pool Client")
    parser.add_argument("--host", default="localhost", help="Server host")
    parser.add_argument("--port", type=int, default=50051, help="Server port")
    parser.add_argument("--output", required=True, help="Output file")
    parser.add_argument("--client-id", help="Client ID")
    parser.add_argument("--concurrent", type=int, default=3, help="Concurrent chunks (default: 3)")
    
    args = parser.parse_args()
    
    client = Client(args.client_id, args.concurrent)
    
    try:
        await client.connect(args.host, args.port)
        success = await client.download_file(args.output)
        
        if success:
            logger.info(f"[{client.client_id}] ✅ Download successful!")
            sys.exit(0)
        else:
            logger.error(f"[{client.client_id}] ❌ Download failed")
            sys.exit(1)
            
    except KeyboardInterrupt:
        logger.info(f"[{client.client_id}] Download cancelled")
        sys.exit(1)
    except Exception as e:
        logger.error(f"[{client.client_id}] Fatal error: {e}")
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
