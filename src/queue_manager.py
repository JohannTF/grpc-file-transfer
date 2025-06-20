"""
Gestor de cola de clientes para evitar saturación del servidor
"""
import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional
from collections import deque


@dataclass
class ClientSession:
    """Información de sesión de cliente."""
    client_id: str
    session_token: str
    join_time: float
    peer_info: str
    status: str  # "waiting", "active", "completed"


class QueueManager:
    """
    Gestor de cola que limita el número de clientes activos simultáneos.
    """
    
    def __init__(self, max_active_clients: int = 10):
        """
        Inicializa el gestor de cola.
        
        Args:
            max_active_clients: Número máximo de clientes activos simultáneos
        """
        self.max_active_clients = max_active_clients
        
        # Cola de espera (FIFO)
        self.waiting_queue: deque[ClientSession] = deque()
        
        # Clientes activos (descargando)
        self.active_clients: Dict[str, ClientSession] = {}
        
        # Todas las sesiones (para validación de tokens)
        self.all_sessions: Dict[str, ClientSession] = {}
        
        # Lock para operaciones thread-safe
        self.lock = asyncio.Lock()
        
        # Estadísticas
        self.total_clients_served = 0
        self.average_download_time = 0
    
    async def join_queue(self, client_id: str, peer_info: str) -> Dict:
        """
        Un cliente solicita unirse a la cola.
        
        Returns:
            Dict con success, session_token, queue_position, estimated_wait_seconds, message
        """
        async with self.lock:
            # Generar token único
            session_token = str(uuid.uuid4())
            
            # Crear sesión
            session = ClientSession(
                client_id=client_id,
                session_token=session_token,
                join_time=time.time(),
                peer_info=peer_info,
                status="waiting"
            )
            
            # Verificar si puede entrar directamente
            if len(self.active_clients) < self.max_active_clients:
                # Puede empezar inmediatamente
                self.active_clients[session_token] = session
                session.status = "active"
                self.all_sessions[session_token] = session
                
                return {
                    "success": True,
                    "session_token": session_token,
                    "queue_position": 0,
                    "estimated_wait_seconds": 0,
                    "message": "Download authorized. You can start immediately."
                }
            else:
                # Debe esperar en cola
                self.waiting_queue.append(session)
                self.all_sessions[session_token] = session
                
                queue_position = len(self.waiting_queue)
                estimated_wait = queue_position * self.average_download_time
                
                return {
                    "success": True,
                    "session_token": session_token,
                    "queue_position": queue_position,
                    "estimated_wait_seconds": int(estimated_wait),
                    "message": f"Added to queue. Position: {queue_position}"
                }
    
    async def check_queue_status(self, session_token: str) -> Dict:
        """
        Verifica el estado de un cliente en la cola.
        
        Returns:
            Dict con authorized, in_queue, queue_position, estimated_wait_seconds, message
        """
        async with self.lock:
            if session_token not in self.all_sessions:
                return {
                    "authorized": False,
                    "in_queue": False,
                    "queue_position": 0,
                    "estimated_wait_seconds": 0,
                    "message": "Invalid session token"
                }
            
            session = self.all_sessions[session_token]
            
            if session.status == "active":
                return {
                    "authorized": True,
                    "in_queue": False,
                    "queue_position": 0,
                    "estimated_wait_seconds": 0,
                    "message": "Download authorized. You can proceed."
                }
            elif session.status == "waiting":
                # Buscar posición en cola
                queue_position = 0
                for i, queued_session in enumerate(self.waiting_queue):
                    if queued_session.session_token == session_token:
                        queue_position = i + 1
                        break
                
                estimated_wait = queue_position * self.average_download_time
                
                return {
                    "authorized": False,
                    "in_queue": True,
                    "queue_position": queue_position,
                    "estimated_wait_seconds": int(estimated_wait),
                    "message": f"Waiting in queue. Position: {queue_position}"
                }
            else:  # completed
                return {
                    "authorized": False,
                    "in_queue": False,
                    "queue_position": 0,
                    "estimated_wait_seconds": 0,
                    "message": "Download already completed"
                }
    
    async def is_authorized(self, session_token: str) -> bool:
        """
        Verifica si un cliente está autorizado para descargar.
        """
        async with self.lock:
            if session_token not in self.all_sessions:
                return False
            
            session = self.all_sessions[session_token]
            return session.status == "active"
    
    async def client_finished(self, session_token: str):
        """
        Marca un cliente como completado y promociona el siguiente en cola.
        """
        async with self.lock:
            if session_token not in self.active_clients:
                return
            
            # Remover cliente activo
            session = self.active_clients.pop(session_token)
            session.status = "completed"
            
            # Actualizar estadísticas
            download_time = time.time() - session.join_time
            self.total_clients_served += 1
            
            # Calcular promedio móvil del tiempo de descarga
            if self.average_download_time == 0:
                self.average_download_time = download_time
            else:
                # Promedio móvil exponencial (factor 0.3)
                self.average_download_time = (0.7 * self.average_download_time + 
                                            0.3 * download_time)
            
            # Promocionar siguiente cliente en cola
            await self._promote_next_client()
    
    async def _promote_next_client(self):
        """
        Promociona el siguiente cliente en cola a activo.
        """
        if self.waiting_queue and len(self.active_clients) < self.max_active_clients:
            next_session = self.waiting_queue.popleft()
            next_session.status = "active"
            self.active_clients[next_session.session_token] = next_session
    
    async def get_stats(self) -> Dict:
        """
        Obtiene estadísticas del gestor de cola.
        """
        async with self.lock:
            return {
                "active_clients": len(self.active_clients),
                "waiting_clients": len(self.waiting_queue),
                "total_served": self.total_clients_served,
                "average_download_time": self.average_download_time,
                "max_capacity": self.max_active_clients
            }
    
    async def cleanup_expired_sessions(self, max_age_hours: int = 24):
        """
        Limpia sesiones expiradas.
        """
        async with self.lock:
            current_time = time.time()
            expired_tokens = []
            
            for token, session in self.all_sessions.items():
                age_hours = (current_time - session.join_time) / 3600
                if age_hours > max_age_hours and session.status == "completed":
                    expired_tokens.append(token)
            
            for token in expired_tokens:
                del self.all_sessions[token]
