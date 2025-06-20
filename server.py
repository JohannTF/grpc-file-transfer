#!/usr/bin/env python3
"""
Servidor RCP con sistema de cola
Evita saturación limitando clientes concurrentes activos
"""

import asyncio
import sys
import argparse
import time
from pathlib import Path
import logging

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(message)s'
)
logger = logging.getLogger(__name__)

# Agregar src al path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from src.chunk_controller import ChunkController
from src.queue_manager import QueueManager


class Server:
    """Servidor con sistema de cola para evitar saturación."""
    
    def __init__(self, max_concurrent_clients: int = 10):
        self.max_concurrent_clients = max_concurrent_clients
        self.chunk_controller = None
        self.queue_manager = None
        self.grpc_server = None
        self.service_impl = None
        self.running = False
        
    async def initialize(self, file_path: str, chunk_size: int):
        """Inicializa el servidor."""
        logger.info("Initializing server")
        
        # Inicializar controlador de chunks
        self.chunk_controller = ChunkController(chunk_size)
        
        try:
            file_info = await self.chunk_controller.precompute_file(file_path)
            logger.info(f"File ready: {file_info.filename} ({file_info.file_size:,} bytes, {file_info.total_chunks:,} chunks)")
        except Exception as e:
            logger.error(f"Failed to load file: {e}")
            raise
          # Inicializar gestor de cola
        self.queue_manager = QueueManager(self.max_concurrent_clients)
        logger.info(f"Queue manager initialized (max concurrent clients: {self.max_concurrent_clients})")
        
        # Inicializar servidor gRPC
        await self._create_grpc_server()
        
    async def _create_grpc_server(self):
        """Crea el servidor gRPC con configuración optimizada."""
        # Usar el mismo enfoque de importación que el cliente
        current_dir = Path(__file__).parent
        generated_dir = current_dir / "src" / "generated"
        if generated_dir.exists():
            sys.path.insert(0, str(generated_dir))
        
        try:
            from grpc import aio
            import file_transfer_pb2_grpc
        except ImportError as e:
            logger.error(f"gRPC import failed: {e}")
            logger.error("Make sure grpcio and generated protobuf files are available")
            raise
        
        from src.grpc_server import FileTransferService
        
        # Crear servidor con configuración robusta
        self.grpc_server = aio.server(
            options=[
                ('grpc.keepalive_time_ms', 60000),
                ('grpc.keepalive_timeout_ms', 15000),
                ('grpc.keepalive_permit_without_calls', True),
                ('grpc.max_concurrent_streams', 100),
                ('grpc.max_receive_message_length', 8 * 1024 * 1024),
                ('grpc.max_send_message_length', 8 * 1024 * 1024),
                ('grpc.so_reuseaddr', 1),
            ]
        )
        
        # Crear service implementation
        self.service_impl = FileTransferService(self.chunk_controller, self.queue_manager)
        
        # Agregar service al servidor
        file_transfer_pb2_grpc.add_FileTransferServiceServicer_to_server(
            self.service_impl, self.grpc_server
        )
        
    async def start(self, host: str, port: int):
        """Inicia el servidor."""
        listen_addr = f"{host}:{port}"
        self.grpc_server.add_insecure_port(listen_addr)
        
        await self.grpc_server.start()
        self.running = True
        
        logger.info(f"Server started on {listen_addr}")
        logger.info("Server ready to accept connections")
        
        # Iniciar tareas de monitoreo
        asyncio.create_task(self._stats_monitor())
        asyncio.create_task(self._cleanup_monitor())
        
    async def _stats_monitor(self):
        """Monitorea estadísticas del servidor."""
        while self.running:
            try:
                stats = await self.queue_manager.get_stats()
                
                if stats["active_clients"] > 0 or stats["waiting_clients"] > 0:
                    logger.info(
                        f"Active clients: {stats['active_clients']}/{stats['max_capacity']}, "
                        f"Waiting: {stats['waiting_clients']}, "
                        f"Total served: {stats['total_served']}"
                    )
                
                await asyncio.sleep(30)  # Estadísticas cada 30 segundos
            except Exception as e:
                logger.error(f"Stats monitor error: {e}")
                await asyncio.sleep(5)
    
    async def _cleanup_monitor(self):
        """Limpia sesiones expiradas periódicamente."""
        while self.running:
            try:
                await asyncio.sleep(3600)  # Cada hora
                await self.queue_manager.cleanup_expired_sessions()
            except Exception as e:
                logger.error(f"Cleanup monitor error: {e}")
    
    async def stop(self):
        """Detiene el servidor."""
        logger.info("Stopping server")
        self.running = False
        
        if self.grpc_server:
            await self.grpc_server.stop(grace=5)
        
        logger.info("Server stopped")
        
    async def wait_for_termination(self):
        """Espera hasta que el servidor termine."""
        if self.grpc_server:
            await self.grpc_server.wait_for_termination()


async def main():
    """Función principal."""
    parser = argparse.ArgumentParser(description="File Transfer Server with Queue System")
    parser.add_argument("file_path", help="Path to file to serve")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=50051, help="Port to bind (default: 50051)")
    parser.add_argument("--max-clients", type=int, default=10, help="Max concurrent clients (default: 10)")
    parser.add_argument("--chunk-size", type=int, default=4 * 1024 * 1024, help="Chunk size (default: 4MB)")
    
    args = parser.parse_args()
    
    # Validar archivo
    file_path = Path(args.file_path)
    if not file_path.exists():
        logger.error(f"File not found: {file_path}")
        sys.exit(1)
    
    if not file_path.is_file():
        logger.error(f"Path is not a file: {file_path}")
        sys.exit(1)
    
    server = Server(args.max_clients)
    
    try:
        # Inicializar servidor
        await server.initialize(str(file_path), args.chunk_size)
        
        # Iniciar servidor
        await server.start(args.host, args.port)
        
        # Esperar terminación
        await server.wait_for_termination()
        
    except KeyboardInterrupt:
        logger.info("Received shutdown signal")
    except Exception as e:
        logger.error(f"Server error: {e}")
        sys.exit(1)
    finally:
        await server.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Server interrupted")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)
