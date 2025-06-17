#!/usr/bin/env python3
"""
Servidor principal RPC 
"""

import asyncio
import sys
import argparse
from pathlib import Path
import logging

# Configurar logging simple
logging.basicConfig(
    level=logging.INFO,
    format='[%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

# Agregar src al path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from src.chunk_controller import PrecomputedChunkController
from src.grpc_server import create_server, cleanup_task


class RCPSimpleServer:
    """Servidor RPC ."""
    
    def __init__(self):
        self.chunk_controller = None
        self.grpc_server = None
        self.service_impl = None
        self.cleanup_task_handle = None
        self.running = False
    async def initialize(self, file_path: str, chunk_size: int = 4 * 1024 * 1024):
        """Inicializa el servidor y pre-computa el archivo."""
        logger.info("Initializing RPC Server")
        
        self.chunk_controller = PrecomputedChunkController(chunk_size)
        
        try:
            file_info = await self.chunk_controller.precompute_file(file_path)
            logger.info(f"File ready: {file_info.filename} ({file_info.file_size:,} bytes, {file_info.total_chunks:,} chunks)")
        except Exception as e:
            logger.error(f"Initialization failed: {e}")
            raise
    
    async def start(self, host: str = "localhost", port: int = 50051):
        """Inicia el servidor gRPC."""
        if not self.chunk_controller or not self.chunk_controller.precomputed:
            raise RuntimeError("Server not initialized")
        
        try:
            self.grpc_server, self.service_impl = await create_server(
                host, port, self.chunk_controller
            )
            
            await self.grpc_server.start()
            
            self.cleanup_task_handle = asyncio.create_task(
                cleanup_task(self.service_impl)
            )
            
            self.running = True
            logger.info(f"Server started on {host}:{port}")
            
        except Exception as e:
            logger.error(f"Failed to start server: {e}")
            raise
    
    async def stop(self):
        """Detiene el servidor."""
        if not self.running:
            return
        
        logger.info("Stopping server")
        
        if self.cleanup_task_handle:
            self.cleanup_task_handle.cancel()
            try:
                await self.cleanup_task_handle
            except asyncio.CancelledError:
                pass
        
        if self.grpc_server:
            await self.grpc_server.stop(grace=5.0)
        
        self.running = False
        logger.info("Server stopped")
    
    async def wait_for_termination(self):
        """Espera hasta que el servidor sea terminado."""
        if self.grpc_server:
            await self.grpc_server.wait_for_termination()


async def main():
    """Función principal."""
    parser = argparse.ArgumentParser(description="RPC Server")
    parser.add_argument("file_path", help="Path to file to serve")
    parser.add_argument("--host", default="localhost", help="Host to bind (default: localhost)")
    parser.add_argument("--port", type=int, default=50051, help="Port to bind (default: 50051)")
    parser.add_argument("--chunk-size", type=int, default=4 * 1024 * 1024, help="Chunk size in bytes (default: 4194304 - 4MB)")
    
    args = parser.parse_args()
    
    # Validar archivo
    file_path = Path(args.file_path)
    if not file_path.exists():
        logger.error(f"File not found: {file_path}")
        sys.exit(1)
    
    if not file_path.is_file():
        logger.error(f"Path is not a file: {file_path}")
        sys.exit(1)
    
    server = RCPSimpleServer()
    
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
