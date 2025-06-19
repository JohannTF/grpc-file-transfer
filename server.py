#!/usr/bin/env python3
"""
Servidor RCP con pool de conexiones CORREGIDO
Pool por cliente, no por stream individual
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
    format='[%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

# Agregar src al path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from src.chunk_controller import ChunkController


class Server:
    """Servidor con pool de conexiones por CLIENTE (no por stream)."""
    
    def __init__(self, max_concurrent_clients: int = 10):
        self.max_concurrent_clients = max_concurrent_clients
        self.chunk_controller = None
        self.grpc_server = None
        self.service_impl = None
        self.running = False
        
        # Pool de CLIENTES (no streams)
        self.active_clients = set()
        self.total_clients_served = 0
        self.client_lock = asyncio.Lock()
        
    async def initialize(self, file_path: str, chunk_size: int):
        """Inicializa el servidor."""
        logger.info(f"Initializing server")
        
        self.chunk_controller = ChunkController(chunk_size)
        
        try:
            file_info = await self.chunk_controller.precompute_file(file_path)
            logger.info(f"File ready: {file_info.filename} ({file_info.file_size:,} bytes, {file_info.total_chunks:,} chunks)")
        except Exception as e:
            logger.error(f"Initialization failed: {e}")
            raise
    
    async def start(self, host: str, port: int):
        """Inicia el servidor."""
        if not self.chunk_controller or not self.chunk_controller.precomputed:
            raise RuntimeError("Server not initialized")
        
        try:
            self.grpc_server, self.service_impl = await create_fixed_server(
                host, port, self.chunk_controller, self
            )
            
            await self.grpc_server.start()
            
            self.running = True
            logger.info(f"server started on {host}:{port}")
            logger.info(f"Max concurrent clients: {self.max_concurrent_clients}")
            
            # Monitor de estadísticas
            asyncio.create_task(self._stats_monitor())
            asyncio.create_task(self._memory_monitor())
            
        except Exception as e:
            logger.error(f"Failed to start server: {e}")
            raise
    
    async def can_client_enter(self, client_peer: str) -> bool:
        """Verifica si un cliente puede entrar al pool."""
        async with self.client_lock:
            # Extraer IP del peer para identificar cliente único
            client_ip = client_peer.split(':')[0] if ':' in client_peer else client_peer
            
            if client_ip in self.active_clients:
                return True  # Cliente ya autorizado
            
            if len(self.active_clients) < self.max_concurrent_clients:
                self.active_clients.add(client_ip)
                logger.info(f"Client {client_ip} authorized ({len(self.active_clients)}/{self.max_concurrent_clients})")
                return True
            
            return False  # Pool lleno
    
    async def client_finished(self, client_peer: str):
        """Marca un cliente como terminado."""
        async with self.client_lock:
            client_ip = client_peer.split(':')[0] if ':' in client_peer else client_peer
            
            if client_ip in self.active_clients:
                self.active_clients.remove(client_ip)
                self.total_clients_served += 1
                logger.info(f"Client {client_ip} finished, slot released ({len(self.active_clients)}/{self.max_concurrent_clients})")
    
    async def _stats_monitor(self):
        """Monitor de estadísticas cada 30 segundos."""
        while self.running:
            await asyncio.sleep(30)
            if self.running:
                async with self.client_lock:
                    active_count = len(self.active_clients)
                logger.info(f"Stats: Active clients: {active_count}/{self.max_concurrent_clients}, Total served: {self.total_clients_served}")
    
    async def _memory_monitor(self):
        """Monitor de memoria del proceso."""
        try:
            import psutil
            import os
            
            process = psutil.Process(os.getpid())
            
            while self.running:
                await asyncio.sleep(60)
                if self.running:
                    memory_mb = process.memory_info().rss / 1024 / 1024
                    logger.info(f"Memory usage: {memory_mb:.1f} MB")
                    
                    if memory_mb > 1024:
                        logger.warning(f"High memory usage: {memory_mb:.1f} MB")
        except ImportError:
            logger.warning("psutil not available, memory monitoring disabled")
    
    async def stop(self):
        """Detiene el servidor."""
        if not self.running:
            return
        
        logger.info("Stopping server")
        self.running = False
        
        if self.grpc_server:
            await self.grpc_server.stop(grace=10.0)
        
        logger.info("server stopped")
        
    async def wait_for_termination(self):
        """Espera terminación del servidor."""
        if self.grpc_server:
            await self.grpc_server.wait_for_termination()


async def create_fixed_server(host: str, port: int, chunk_controller, pool_manager):
    """Crea servidor gRPC con pool CORREGIDO."""
    
    # Importar generated code y grpc
    import grpc
    from grpc import aio
    
    try:
        sys.path.insert(0, str(Path(__file__).parent / "src" / "generated"))
        import file_transfer_pb2
        import file_transfer_pb2_grpc
    except ImportError:
        logger.error("Generated gRPC code not found")
        raise
    
    from src.grpc_server import FileTransferService
    
    # Crear servidor con configuración robusta
    server = aio.server(
        options=[
            ('grpc.keepalive_time_ms', 60000),  # 60s keepalive
            ('grpc.keepalive_timeout_ms', 15000),  # 15s timeout
            ('grpc.keepalive_permit_without_calls', True),
            ('grpc.max_concurrent_streams', 100),  # Limitado pero suficiente
            ('grpc.max_receive_message_length', 8 * 1024 * 1024),
            ('grpc.max_send_message_length', 8 * 1024 * 1024),
            ('grpc.so_reuseaddr', 1),
        ]
    )
    
    # Crear service implementation
    service_impl = FileTransferService(chunk_controller, pool_manager)
    
    # Agregar service al servidor
    file_transfer_pb2_grpc.add_FileTransferServiceServicer_to_server(service_impl, server)
    
    # Configurar listen address
    listen_addr = f"{host}:{port}"
    server.add_insecure_port(listen_addr)
    
    logger.info(f"Fixed gRPC server configured: {host}:{port}")
    
    return server, service_impl


async def main():
    """Función principal."""
    parser = argparse.ArgumentParser(description="Server")
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
    
    server = Server(args.max_clients)
    
    try:
        await server.initialize(str(file_path), args.chunk_size)
        await server.start(args.host, args.port)
        logger.info("Server ready - Press Ctrl+C to stop")
        await server.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)
    finally:
        await server.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutdown complete")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
