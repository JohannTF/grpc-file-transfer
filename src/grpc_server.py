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
    Implementación simplificada del servicio de transferencia.
    
    Características:
    - Acceso directo a chunks pre-computados
    - Sin locks (arrays inmutables)
    - Latencia consistente <2ms por chunk
    - Soporte para 100+ clientes concurrentes
    """
    
    def __init__(self, chunk_controller: PrecomputedChunkController):
        """
        Inicializa el servicio.
        
        Args:
            chunk_controller: Controlador de chunks pre-computados
        """
        self.chunk_controller = chunk_controller
        self.active_clients: Dict[str, Dict] = {}
        self.start_time = time.time()
    
    async def GetFileInfo(self, request, context):
        """
        Obtiene información del archivo disponible.
        
        Args:
            request: FileInfoRequest
            context: Context de gRPC
            
        Returns:
            FileInfoResponse con información del archivo
        """
        client_id = request.client_id or f"client_{int(time.time())}"
        
        # Registrar cliente activo
        self._register_active_client(client_id)
        
        file_info = self.chunk_controller.get_file_info()
        
        if not file_info:
            context.set_code(grpc.StatusCode.UNAVAILABLE)
            context.set_details("Archivo no disponible. Servidor no inicializado.")
            
            # Crear response vacía pero válida
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
        client_id = request.client_id
        chunk_id = request.chunk_id
        
        start_time = time.time()
        
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
        chunk_info = self.chunk_controller.get_chunk(chunk_id, client_id)
        
        if not chunk_info:
            # Chunk no encontrado
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
            checksum=chunk_info.checksum,
            size=chunk_info.size,
            offset=chunk_info.offset,
            is_last=chunk_info.is_last,
            success=True
        )
        
        # Calcular latencia
        latency_ms = (time.time() - start_time) * 1000
        
        # Actualizar estadísticas de cliente
        self._update_client_stats(client_id, chunk_info.size, latency_ms)
        
        return response
    
    async def RegisterClient(self, request, context):
        """
        Registra un cliente para estadísticas.
        
        Args:
            request: ClientRegisterRequest
            context: Context de gRPC
            
        Returns:
            ClientRegisterResponse
        """
        client_id = request.client_id
        client_info = request.client_info
        
        # Generar session ID único
        session_id = f"session_{client_id}_{int(time.time())}"
        
        # Registrar cliente
        self._register_active_client(client_id, client_info)
        from generated import file_transfer_pb2
        response = file_transfer_pb2.ClientRegisterResponse(
            accepted=True,
            session_id=session_id,
            server_info="RPC Server v1.0 - Pre-computed chunks"
        )
        
        return response
    
    async def GetServerStats(self, request, context):
        """
        Obtiene estadísticas del servidor.
        
        Args:
            request: ServerStatsRequest
            context: Context de gRPC
            
        Returns:
            ServerStatsResponse
        """
        detailed = request.detailed
        
        # Obtener estadísticas del controlador
        controller_stats = self.chunk_controller.get_server_stats()
        
        # Calcular estadísticas del servidor
        uptime = time.time() - self.start_time
        active_clients = len(self.active_clients)
        
        from generated import file_transfer_pb2
        response = file_transfer_pb2.ServerStatsResponse(
            active_clients=active_clients,
            total_bytes_sent=sum(
                client["bytes_sent"] for client in self.active_clients.values()
            ),
            total_chunks_sent=controller_stats["total_requests"],
            avg_chunks_per_second=controller_stats["chunks_per_second"],
            server_uptime_seconds=uptime,
            cache_hits=0,  # No hay cache, siempre hit
            cache_misses=0  # No hay cache misses
        )
        
        # Agregar estadísticas detalladas si se solicita
        if detailed:
            for client_id, stats in self.active_clients.items():
                client_stat = file_transfer_pb2.ClientStats(
                    client_id=client_id,
                    chunks_requested=stats["chunks_requested"],
                    bytes_transferred=stats["bytes_sent"],
                    avg_latency_ms=stats["avg_latency_ms"],
                    status=stats["status"]
                )
                response.client_stats.append(client_stat)
        
        return response
    
    def _register_active_client(self, client_id: str, client_info: str = ""):
        """Registra un cliente como activo."""
        if client_id not in self.active_clients:
            self.active_clients[client_id] = {
                "client_info": client_info,
                "first_seen": time.time(),
                "last_activity": time.time(),
                "chunks_requested": 0,
                "bytes_sent": 0,
                "total_latency": 0.0,
                "avg_latency_ms": 0.0,
                "status": "active"
            }
        else:
            self.active_clients[client_id]["last_activity"] = time.time()
    
    def _update_client_stats(self, client_id: str, bytes_sent: int, latency_ms: float):
        """Actualiza estadísticas de un cliente."""
        if client_id not in self.active_clients:
            self._register_active_client(client_id)
        
        stats = self.active_clients[client_id]
        stats["last_activity"] = time.time()
        stats["chunks_requested"] += 1
        stats["bytes_sent"] += bytes_sent
        stats["total_latency"] += latency_ms
        stats["avg_latency_ms"] = stats["total_latency"] / stats["chunks_requested"]
    
    def cleanup_inactive_clients(self, max_idle_seconds: int = 300):
        """Limpia clientes inactivos (llamar periódicamente)."""
        current_time = time.time()
        inactive_clients = []
        
        for client_id, stats in self.active_clients.items():
            if current_time - stats["last_activity"] > max_idle_seconds:
                inactive_clients.append(client_id)
                stats["status"] = "inactive"
        
        for client_id in inactive_clients:
            del self.active_clients[client_id]


async def create_server(host: str, port: int, chunk_controller: PrecomputedChunkController):
    """
    Crea y configura el servidor gRPC.
    
    Args:
        host: Host para bind
        port: Puerto para bind
        chunk_controller: Controlador de chunks
        
    Returns:
        Servidor gRPC configurado
    """
    # Importar generated code
    try:
        from generated import file_transfer_pb2_grpc
    except ImportError:
        logger.error("Generated gRPC code not found. Run: python generate_grpc.py")
        raise
    
    # Crear servidor con configuración optimizada
    server = aio.server(
        options=[
            ('grpc.keepalive_time_ms', 30000),
            ('grpc.keepalive_timeout_ms', 5000),
            ('grpc.keepalive_permit_without_calls', True),
            ('grpc.http2.max_pings_without_data', 0),
            ('grpc.http2.min_time_between_pings_ms', 10000),
            ('grpc.http2.min_ping_interval_without_data_ms', 300000),
            ('grpc.max_concurrent_streams', 1000),
            ('grpc.max_receive_message_length', 64 * 1024 * 1024),  # 64MB
            ('grpc.max_send_message_length', 64 * 1024 * 1024),     # 64MB
        ]
    )
    
    # Crear service implementation
    service_impl = FileTransferServiceImpl(chunk_controller)
    
    # Agregar service al servidor
    file_transfer_pb2_grpc.add_FileTransferServiceServicer_to_server(service_impl, server)
    
    # Configurar listen address
    listen_addr = f"{host}:{port}"
    server.add_insecure_port(listen_addr)
    
    logger.info("server_configured", host=host, port=port)
    
    return server, service_impl


async def cleanup_task(service_impl: FileTransferServiceImpl):
    """Tarea periódica para limpiar clientes inactivos."""
    while True:
        await asyncio.sleep(60)  # Cada minuto
        service_impl.cleanup_inactive_clients()
        
        # Log estadísticas periódicas
        stats = service_impl.chunk_controller.get_server_stats()
        logger.info(
            "periodic_stats",
            active_clients=len(service_impl.active_clients),
            total_requests=stats["total_requests"],
            chunks_per_second=f"{stats['chunks_per_second']:.1f}",
            memory_usage_mb=f"{stats['memory_usage_mb']:.1f}"
        )
