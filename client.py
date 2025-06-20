#!/usr/bin/env python3
"""
Cliente RCP con sistema de cola
Cliente que espera su turno antes de comenzar la descarga
"""

import asyncio
import sys
import argparse
import time
import uuid
import logging
from pathlib import Path

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(message)s'
)
logger = logging.getLogger(__name__)

# Agregar src al path
sys.path.insert(0, str(Path(__file__).parent / "src"))
sys.path.insert(0, str(Path(__file__).parent / "src" / "generated"))

# Importar generated code
current_dir = Path(__file__).parent
generated_dir = current_dir / "src" / "generated"
if generated_dir.exists():
    sys.path.insert(0, str(generated_dir))

try:
    import grpc
    import file_transfer_pb2
    import file_transfer_pb2_grpc
except ImportError as e:
    logger.error(f"Import error: {e}")
    logger.error("Make sure grpcio and generated protobuf files are available")
    sys.exit(1)


class Client:
    """Cliente optimizado con sistema de cola."""
    
    def __init__(self, client_id: str = None, concurrent_chunks: int = 3):
        self.client_id = client_id or f"client_{int(time.time())}"
        self.concurrent_chunks = concurrent_chunks
        self.channel = None
        self.stub = None
        self.session_token = None
        
        # Tiempos para estadísticas
        self.connection_start_time = None
        self.queue_wait_start_time = None
        self.download_start_time = None
        self.download_end_time = None
        
        # Estadísticas de descarga
        self.chunks_downloaded = 0
        self.bytes_downloaded = 0
        self.total_chunks = 0
        self.failed_chunks = 0
        
        # Control de estado
        self.download_active = False
    
    async def connect(self, host: str, port: int):
        """Conecta al servidor."""
        self.connection_start_time = time.time()
        server_address = f"{host}:{port}"
        
        logger.info("Connecting to server...")
        
        self.channel = grpc.aio.insecure_channel(
            server_address,
            options=[
                ('grpc.keepalive_time_ms', 30000),
                ('grpc.keepalive_timeout_ms', 10000),
                ('grpc.keepalive_permit_without_calls', True),
                ('grpc.max_receive_message_length', 8 * 1024 * 1024),
                ('grpc.max_send_message_length', 1024 * 1024),
                ('grpc.http2.max_pings_without_data', 0),
                ('grpc.http2.min_time_between_pings_ms', 10000),
                ('grpc.enable_retries', 1),
                ('grpc.max_retry_attempts', 3),
            ]
        )
        
        self.stub = file_transfer_pb2_grpc.FileTransferServiceStub(self.channel)
        logger.info("Connected to server")
    
    async def disconnect(self):
        """Desconecta del servidor."""
        if self.channel:
            await self.channel.close()
    
    async def join_queue(self):
        """Se une a la cola de descarga."""
        try:
            request = file_transfer_pb2.JoinQueueRequest(client_id=self.client_id)
            response = await self.stub.JoinQueue(request)
            
            if response.success:
                self.session_token = response.session_token
                
                if response.queue_position == 0:
                    logger.info("Download authorized. Starting immediately.")
                    return True
                else:
                    self.queue_wait_start_time = time.time()
                    logger.info(f"Waiting in queue. Position: {response.queue_position}")
                    if response.estimated_wait_seconds > 0:
                        minutes = response.estimated_wait_seconds // 60
                        seconds = response.estimated_wait_seconds % 60
                        logger.info(f"Estimated wait time: {minutes}m {seconds}s")
                    return False
            else:
                logger.error(f"Failed to join queue: {response.message}")
                return None
                
        except grpc.RpcError as e:
            logger.error(f"Failed to join queue: {e.details()}")
            return None
    
    async def wait_for_authorization(self):
        """Espera hasta ser autorizado para descargar."""
        logger.info("Waiting in the queue to be attended")
        
        while True:
            try:
                request = file_transfer_pb2.QueueStatusRequest(session_token=self.session_token)
                response = await self.stub.CheckQueueStatus(request)
                
                if response.authorized:
                    if self.queue_wait_start_time:
                        wait_time = time.time() - self.queue_wait_start_time
                        logger.info(f"Time waited in the Queue: {wait_time:.1f}s")
                    return True
                elif response.in_queue:
                    await asyncio.sleep(2)
                else:
                    logger.error(f"Queue status error: {response.message}")
                    return False
                    
            except grpc.RpcError as e:
                logger.error(f"Failed to check queue status: {e.details()}")
                await asyncio.sleep(5)
    
    async def get_file_info(self):
        """Obtiene información del archivo."""
        try:
            request = file_transfer_pb2.FileInfoRequest()
            response = await self.stub.GetFileInfo(request)
            
            if response.file_ready:
                self.total_chunks = response.total_chunks
                return {
                    'filename': response.filename,
                    'file_size': response.file_size,
                    'total_chunks': response.total_chunks,
                    'chunk_size': response.chunk_size,
                    'file_checksum': response.file_checksum
                }
            else:
                return None
                
        except grpc.RpcError as e:
            logger.error(f"Failed to get file info: {e.details()}")
            return None
    
    async def download_chunk(self, chunk_id: int, max_retries: int = 2):
        """Descarga un chunk específico."""
        for attempt in range(max_retries + 1):
            try:
                request = file_transfer_pb2.ChunkRequest(
                    chunk_id=chunk_id,
                    session_token=self.session_token
                )
                response = await self.stub.GetChunk(request)
                
                if response.success:
                    self.chunks_downloaded += 1
                    self.bytes_downloaded += response.size
                    return response
                else:
                    if attempt == max_retries:
                        logger.error(f"Chunk {chunk_id} failed after {max_retries + 1} attempts: {response.error_message}")
                        self.failed_chunks += 1
                    await asyncio.sleep(0.1 * (attempt + 1))
                    
            except grpc.RpcError as e:
                if attempt == max_retries:
                    logger.error(f"Chunk {chunk_id} failed: {e.details()}")
                    self.failed_chunks += 1
                await asyncio.sleep(0.1 * (attempt + 1))
        
        return None
    
    async def download_file(self, output_path: str) -> bool:
        """Descarga el archivo completo."""
        # Obtener información del archivo
        file_info = await self.get_file_info()
        if not file_info:
            logger.error("Failed to get file information")
            return False
        
        # Unirse a la cola
        queue_result = await self.join_queue()
        if queue_result is None:
            return False
        
        # Si no está autorizado inmediatamente, esperar en cola
        if not queue_result:
            authorized = await self.wait_for_authorization()
            if not authorized:
                return False
        
        # Comenzar descarga
        self.download_start_time = time.time()
        logger.info("Starting download...")
        
        # Iniciar monitor de progreso
        progress_task = asyncio.create_task(self._progress_monitor())
        
        try:
            # Crear archivo de salida
            with open(output_path, 'wb') as output_file:
                # Preallocar archivo
                output_file.seek(file_info['file_size'] - 1)
                output_file.write(b'\0')
                output_file.seek(0)
                
                # Crear semáforo para controlar concurrencia
                semaphore = asyncio.Semaphore(self.concurrent_chunks)
                
                # Crear tasks para descargar chunks
                tasks = []
                for chunk_id in range(file_info['total_chunks']):
                    task = asyncio.create_task(
                        self._download_chunk_with_semaphore(semaphore, chunk_id, output_file)
                    )
                    tasks.append(task)
                
                # Esperar a que todos los chunks se descarguen
                await asyncio.gather(*tasks, return_exceptions=True)
            
            self.download_end_time = time.time()
            progress_task.cancel()
            
            # Verificar si se descargaron todos los chunks
            if self.failed_chunks > 0:
                logger.error(f"Download incomplete. Failed chunks: {self.failed_chunks}")
                return False
            
            logger.info("Download complete.")
            self._print_final_stats()
            return True
            
        except Exception as e:
            progress_task.cancel()
            logger.error(f"Download failed: {e}")
            return False
    
    async def _download_chunk_with_semaphore(self, semaphore, chunk_id, output_file):
        """Descarga un chunk con control de concurrencia."""
        async with semaphore:
            chunk_response = await self.download_chunk(chunk_id)
            if chunk_response:
                # Escribir chunk en la posición correcta
                output_file.seek(chunk_response.offset)
                output_file.write(chunk_response.data)
    
    async def _progress_monitor(self):
        """Monitorea el progreso de descarga."""
        self.download_active = True
        
        while self.download_active:
            try:
                await asyncio.sleep(5)
                
                if self.total_chunks > 0:
                    progress = (self.chunks_downloaded / self.total_chunks) * 100
                    speed_mbps = (self.bytes_downloaded / (1024 * 1024)) / max(1, time.time() - self.download_start_time)
                    
                    logger.info(f"Progress: {progress:.1f}% ({self.chunks_downloaded}/{self.total_chunks} chunks, {speed_mbps:.1f} MB/s)")
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Progress monitor error: {e}")
        
        self.download_active = False
    
    def _print_final_stats(self):
        """Imprime estadísticas finales."""
        total_time = self.download_end_time - self.connection_start_time
        
        if self.queue_wait_start_time:
            queue_wait_time = self.download_start_time - self.queue_wait_start_time
        else:
            queue_wait_time = 0
        
        download_time = self.download_end_time - self.download_start_time
        avg_speed = (self.bytes_downloaded / (1024 * 1024)) / download_time
        
        print("\n" + "="*50)
        print("DOWNLOAD STATISTICS")
        print("="*50)
        print(f"Total wait time to be attended: {queue_wait_time:.1f}s")
        print(f"Download time: {download_time:.1f}s")
        print(f"Total time from connection to completion: {total_time:.1f}s")
        print(f"Average download speed: {avg_speed:.1f} MB/s")
        print(f"Total bytes downloaded: {self.bytes_downloaded:,} bytes")
        print("="*50)


async def main():
    """Función principal del cliente."""
    parser = argparse.ArgumentParser(description="Queue-based File Transfer Client")
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
            logger.info("File downloaded successfully")
        else:
            logger.error("Download failed")
            sys.exit(1)
            
    except KeyboardInterrupt:
        logger.info("Download interrupted by user")
    except Exception as e:
        logger.error(f"Client error: {e}")
        sys.exit(1)
    finally:
        await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Client interrupted")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)
