"""
Servidor gRPC  para RPC
Implementación, solo acceso directo a chunks
"""

import asyncio
import time
import grpc
from grpc import aio
from typing import Dict, Optional
import structlog

from .chunk_controller import PrecomputedChunkController


# Logger estructurado
logger = structlog.get_logger()


class FileTransferServiceImpl:
    """
    Implementación del servicio de transferencia.
    """
    
    def __init__(self, chunk_controller: PrecomputedChunkController):
        """
        Inicializa el servicio.
        
        Args:
            chunk_controller: Controlador de chunks pre-computados
        """
        self.chunk_controller = chunk_controller
    
    async def GetFileInfo(self, request, context):
        """
        Obtiene información del archivo disponible.
        
        Args:
            request: FileInfoRequest
            context: Context de gRPC
            
        Returns:
            FileInfoResponse con información del archivo
        """
        file_info = self.chunk_controller.get_file_info()
        
        if not file_info:
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            context.set_details("Archivo no disponible. Servidor no inicializado.")
            
            from generated import file_transfer_pb2
            return file_transfer_pb2.FileInfoResponse(file_ready=False)
        
        # Crear response con información completa
        from generated import file_transfer_pb2
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
        """
        Obtiene un chunk específico por ID.
        
        Args:
            request: ChunkRequest
            context: Context de gRPC
            
        Returns:
            ChunkResponse con datos del chunk
        """
        chunk_id = request.chunk_id
        
        # Validar request
        if chunk_id < 0:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(f"chunk_id inválido: {chunk_id}")
            
            from generated import file_transfer_pb2
            return file_transfer_pb2.ChunkResponse(
                chunk_id=chunk_id,
                success=False,
                error_message="chunk_id debe ser >= 0"
            )
        
        # Obtener chunk (acceso directo O(1))
        chunk_info = self.chunk_controller.get_chunk(chunk_id)
        
        if not chunk_info:
            from generated import file_transfer_pb2
            response = file_transfer_pb2.ChunkResponse(
                chunk_id=chunk_id,
                success=False,
                error_message=f"Chunk {chunk_id} no encontrado o fuera de rango"
            )
            return response
        
        # Crear response exitosa
        from generated import file_transfer_pb2
        response = file_transfer_pb2.ChunkResponse(
            chunk_id=chunk_info.chunk_id,
            data=chunk_info.data,
            size=chunk_info.size,
            offset=chunk_info.offset,
            is_last=chunk_info.is_last,
            success=True
        )
        
        return response


async def create_server(host: str, port: int, chunk_controller: PrecomputedChunkController):
    """
    Crea y configura el servidor gRPC.
    """
    # Importar generated code
    try:
        from generated import file_transfer_pb2_grpc
    except ImportError:
        logger.error("[GRPC_SERVER] Generated gRPC code not found")
        raise
    
    # Crear servidor con configuración para chunks de 4MB
    server = aio.server(
        options=[
            ('grpc.keepalive_time_ms', 10000),
            ('grpc.keepalive_timeout_ms', 5000),
            ('grpc.keepalive_permit_without_calls', True),
            ('grpc.max_concurrent_streams', 400),  # Optimizado para 50 clientes
            ('grpc.max_receive_message_length', 5 * 1024 * 1024),  # 5MB (4MB + overhead)
            ('grpc.max_send_message_length', 5 * 1024 * 1024),     # 5MB (4MB + overhead)
        ]
    )
    
    # Crear service implementation
    service_impl = FileTransferServiceImpl(chunk_controller)
    
    # Agregar service al servidor
    file_transfer_pb2_grpc.add_FileTransferServiceServicer_to_server(service_impl, server)
    
    # Configurar listen address
    listen_addr = f"{host}:{port}"
    server.add_insecure_port(listen_addr)
    
    logger.info("[GRPC_SERVER] server_configured", host=host, port=port)
    
    return server, service_impl
