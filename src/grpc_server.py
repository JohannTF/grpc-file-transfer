"""
Servidor gRPC para RCP
Implementación, solo acceso directo a chunks
"""

import asyncio
import time
import grpc
from grpc import aio
from typing import Dict, Optional
import structlog

from .chunk_controller import ChunkController

# Importar generated code al inicio
try:
    from .generated import file_transfer_pb2, file_transfer_pb2_grpc
except ImportError:
    try:
        # Fallback para imports desde directorio raíz
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "generated"))
        import file_transfer_pb2
        import file_transfer_pb2_grpc
    except ImportError:
        raise ImportError("Generated gRPC code not found. Run: python -m grpc_tools.protoc --proto_path=protos --python_out=src/generated --grpc_python_out=src/generated protos/file_transfer.proto")

# Logger estructurado
logger = structlog.get_logger()


class FileTransferService:
        """Servicio con pool CORREGIDO por cliente."""
        
        def __init__(self, chunk_controller, pool_manager):
            self.chunk_controller = chunk_controller
            self.pool_manager = pool_manager
            self.client_download_status = {}  # Track client download status
        
        async def GetFileInfo(self, request, context):
            """Obtiene info del archivo - libre para todos."""
            file_info = self.chunk_controller.get_file_info()
            
            if not file_info:
                context.set_code(grpc.StatusCode.UNAVAILABLE)
                context.set_details("Archivo no disponible")
                return file_transfer_pb2.FileInfoResponse(file_ready=False)
            
            response = file_transfer_pb2.FileInfoResponse(
                filename=file_info.filename,
                file_size=file_info.file_size,
                total_chunks=file_info.total_chunks,
                chunk_size=file_info.chunk_size,
                file_checksum=file_info.file_checksum,
                file_type=file_info.file_type,
                file_ready=True
            )
            
            return response
        
        async def GetChunk(self, request, context):
            """Obtiene chunk con pool CORREGIDO por cliente."""
            chunk_id = request.chunk_id
            peer = context.peer()
            client_ip = peer.split(':')[0] if ':' in peer else peer
            
            # Verificar autorización del cliente (no por stream)
            can_proceed = await self.pool_manager.can_client_enter(peer)
            
            if not can_proceed:
                # Pool lleno - rechazar con tiempo de espera
                await asyncio.sleep(1)  # Pequeña pausa para evitar spam
                context.set_code(grpc.StatusCode.RESOURCE_EXHAUSTED)
                context.set_details("Server pool full")
                return file_transfer_pb2.ChunkResponse(
                    chunk_id=chunk_id,
                    success=False,
                    error_message="Server busy, try again later"
                )
            
            # Validar request
            if chunk_id < 0:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details(f"chunk_id inválido: {chunk_id}")
                return file_transfer_pb2.ChunkResponse(
                    chunk_id=chunk_id,
                    success=False,
                    error_message="chunk_id debe ser >= 0"
                )
            
            # Obtener chunk
            chunk_result = self.chunk_controller.get_chunk(chunk_id)
            
            if not chunk_result or not chunk_result.get('success'):
                return file_transfer_pb2.ChunkResponse(
                    chunk_id=chunk_id,
                    success=False,
                    error_message=f"Chunk {chunk_id} no encontrado"
                )
            
            # Si es el último chunk, marcar cliente como terminado
            if chunk_result['is_last']:
                await self.pool_manager.client_finished(peer)
            
            # Respuesta exitosa
            return file_transfer_pb2.ChunkResponse(
                chunk_id=chunk_result['chunk_id'],
                data=chunk_result['data'],
                size=chunk_result['size'],
                offset=chunk_result['offset'],
                is_last=chunk_result['is_last'],
                success=True
            )