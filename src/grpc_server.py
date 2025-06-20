"""
Servidor gRPC para RCP con sistema de cola
Implementación optimizada con gestión de clientes activos
"""

import asyncio
import time
import sys
import os
from pathlib import Path

# Configurar importaciones usando el mismo enfoque que el cliente
current_dir = Path(__file__).parent
project_root = current_dir.parent

# Agregar paths necesarios
sys.path.insert(0, str(project_root / "src"))
sys.path.insert(0, str(project_root / "src" / "generated"))

# Agregar también el directorio generated directamente
generated_dir = current_dir / "generated"
if generated_dir.exists():
    sys.path.insert(0, str(generated_dir))

# Importar dependencias con manejo de errores
try:
    import grpc
    from grpc import aio
    import file_transfer_pb2
    import file_transfer_pb2_grpc
except ImportError as e:
    print(f"Import error: {e}")
    print("Make sure grpcio and generated protobuf files are available")
    sys.exit(1)

from typing import Dict, Optional

# Importar módulos internos desde el directorio padre
current_file_dir = Path(__file__).parent
sys.path.insert(0, str(current_file_dir))

from chunk_controller import ChunkController
from queue_manager import QueueManager


class FileTransferService(file_transfer_pb2_grpc.FileTransferServiceServicer):
    """Servicio gRPC con sistema de cola integrado."""
    
    def __init__(self, chunk_controller: ChunkController, queue_manager: QueueManager):
        self.chunk_controller = chunk_controller
        self.queue_manager = queue_manager
    
    async def GetFileInfo(self, request, context):
        """Obtiene información del archivo - acceso libre para todos los clientes."""
        file_info = self.chunk_controller.get_file_info()
        
        if not file_info:
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            context.set_details("File not available")
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
    
    async def JoinQueue(self, request, context):
        """Cliente solicita unirse a la cola de descarga."""
        client_id = request.client_id
        peer_info = context.peer()
        
        if not client_id:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("Client ID is required")
            return file_transfer_pb2.JoinQueueResponse(success=False, message="Client ID is required")
        
        # Solicitar entrada a la cola
        result = await self.queue_manager.join_queue(client_id, peer_info)
        
        return file_transfer_pb2.JoinQueueResponse(
            success=result["success"],
            session_token=result["session_token"],
            queue_position=result["queue_position"],
            estimated_wait_seconds=result["estimated_wait_seconds"],
            message=result["message"]
        )
    
    async def CheckQueueStatus(self, request, context):
        """Cliente verifica su estado en la cola."""
        session_token = request.session_token
        
        if not session_token:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("Session token is required")
            return file_transfer_pb2.QueueStatusResponse(
                authorized=False,
                in_queue=False,
                message="Session token is required"
            )
        
        # Verificar estado en la cola
        result = await self.queue_manager.check_queue_status(session_token)
        
        return file_transfer_pb2.QueueStatusResponse(
            authorized=result["authorized"],
            in_queue=result["in_queue"],
            queue_position=result["queue_position"],
            estimated_wait_seconds=result["estimated_wait_seconds"],
            message=result["message"]
        )
    
    async def GetChunk(self, request, context):
        """Obtiene chunk específico - solo para clientes autorizados."""
        chunk_id = request.chunk_id
        session_token = request.session_token
        
        # Verificar autorización
        is_authorized = await self.queue_manager.is_authorized(session_token)
        
        if not is_authorized:
            context.set_code(grpc.StatusCode.PERMISSION_DENIED)
            context.set_details("Not authorized to download. Check queue status.")
            return file_transfer_pb2.ChunkResponse(
                chunk_id=chunk_id,
                success=False,
                error_message="Not authorized to download"
            )
        
        # Validar chunk_id
        if chunk_id < 0:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(f"Invalid chunk_id: {chunk_id}")
            return file_transfer_pb2.ChunkResponse(
                chunk_id=chunk_id,
                success=False,
                error_message="chunk_id must be >= 0"
            )
        
        # Obtener chunk desde el controlador
        chunk_result = self.chunk_controller.get_chunk(chunk_id)
        
        if not chunk_result or not chunk_result.get('success'):
            return file_transfer_pb2.ChunkResponse(
                chunk_id=chunk_id,
                success=False,
                error_message=f"Chunk {chunk_id} not found"
            )
        
        # Si es el último chunk, marcar cliente como terminado
        if chunk_result['is_last']:
            await self.queue_manager.client_finished(session_token)
        
        # Respuesta exitosa
        return file_transfer_pb2.ChunkResponse(
            chunk_id=chunk_result['chunk_id'],
            data=chunk_result['data'],
            size=chunk_result['size'],
            offset=chunk_result['offset'],
            is_last=chunk_result['is_last'],
            success=True
        )
