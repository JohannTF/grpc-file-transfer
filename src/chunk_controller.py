"""
Controlador de chunks optimizado para archivos grandes
Lectura bajo demanda con caché LRU y control de concurrencia
"""

import os
import asyncio
import hashlib
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Dict, List
from collections import OrderedDict
import logging

logger = logging.getLogger(__name__)

@dataclass
class ChunkInfo:
    """Información de un chunk."""
    chunk_id: int
    data: bytes
    checksum: str
    size: int
    offset: int
    is_last: bool

@dataclass
class FileInfo:
    """Información del archivo."""
    filename: str
    file_size: int
    total_chunks: int
    chunk_size: int
    file_checksum: str
    file_type: str = "application/octet-stream"

class PrecomputedChunkController:
    """
    Controlador optimizado para archivos grandes:
    - Lectura bajo demanda con os.pread
    - Caché LRU para chunks frecuentes
    - Control de concurrencia con semáforos
    - Sin precarga de archivos completos
    """
    
    def __init__(
        self,
        chunk_size: int = 4 * 1024 * 1024,
        max_cache_size: int = 50,
        max_concurrent_reads: int = 20
    ):
        """
        Inicializa el controlador.
        
        Args:
            chunk_size: Tamaño de chunk en bytes
            max_cache_size: Máximo chunks en caché
            max_concurrent_reads: Máximo lecturas concurrentes
        """
        self.chunk_size = chunk_size
        self.max_cache_size = max_cache_size
        self.max_concurrent_reads = max_concurrent_reads
        
        # Estado del controlador
        self.precomputed: bool = False
        self.file_info: Optional[FileInfo] = None
        self.fd: Optional[int] = None  # File descriptor
        
        # Caché LRU para chunks
        self.cache: OrderedDict[int, ChunkInfo] = OrderedDict()
        self.cache_lock = asyncio.Lock()
        
        # Control de concurrencia
        self.read_semaphore = asyncio.Semaphore(max_concurrent_reads)
        
        # Estadísticas
        self.total_requests: int = 0
        self.start_time: float = time.time()
        self.client_stats: Dict[str, Dict] = {}
        self.cache_hits: int = 0
        self.cache_misses: int = 0
    
    async def precompute_file(self, file_path: str) -> FileInfo:
        """
        Precomputa metadatos del archivo SIN cargarlo en memoria.
        
        Args:
            file_path: Ruta al archivo
            
        Returns:
            FileInfo con metadatos
        """
        try:
            path = Path(file_path)
            self.fd = os.open(file_path, os.O_RDONLY)
            
            # Obtener metadatos del archivo
            file_size = os.fstat(self.fd).st_size
            total_chunks = (file_size + self.chunk_size - 1) // self.chunk_size
            
            # Calcular checksum en segundo plano (opcional)
            file_checksum = "not_computed"
            
            self.file_info = FileInfo(
                filename=path.name,
                file_size=file_size,
                total_chunks=total_chunks,
                chunk_size=self.chunk_size,
                file_checksum=file_checksum,
                file_type=self._detect_file_type(path)
            )
            
            self.precomputed = True
            logger.info(
                f"Archivo listo: {self.file_info.filename} "
                f"({file_size:,} bytes, {total_chunks} chunks)"
            )
            
            return self.file_info
            
        except Exception as e:
            logger.error(f"Error en precomputación: {e}")
            self.close()
            raise
    
    def close(self):
        """Libera recursos del archivo."""
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
    
    async def get_chunk(self, chunk_id: int, client_id: str = "unknown") -> Optional[ChunkInfo]:
        """
        Obtiene un chunk con gestión eficiente de recursos.
        
        Args:
            chunk_id: ID del chunk (0-based)
            client_id: ID para estadísticas
            
        Returns:
            ChunkInfo o None si es inválido
        """
        if not self.precomputed or self.fd is None:
            return None
        
        # Validar ID de chunk
        if chunk_id < 0 or chunk_id >= self.file_info.total_chunks:
            return None
        
        # Actualizar estadísticas
        self.total_requests += 1
        if client_id not in self.client_stats:
            self.client_stats[client_id] = {
                "requests": 0,
                "bytes_sent": 0,
                "first_request": time.time()
            }
        
        stats = self.client_stats[client_id]
        stats["requests"] += 1
        
        # Intentar obtener del caché
        async with self.cache_lock:
            if chunk_id in self.cache:
                chunk_info = self.cache[chunk_id]
                self.cache.move_to_end(chunk_id)  # Actualizar LRU
                self.cache_hits += 1
                stats["bytes_sent"] += len(chunk_info.data)
                return chunk_info
        
        # Leer desde disco si no está en caché
        self.cache_misses += 1
        chunk_info = await self._read_chunk_from_disk(chunk_id)
        
        if chunk_info:
            # Actualizar caché
            async with self.cache_lock:
                self.cache[chunk_id] = chunk_info
                if len(self.cache) > self.max_cache_size:
                    self.cache.popitem(last=False)  # Eliminar LRU
            
            stats["bytes_sent"] += len(chunk_info.data)
        
        return chunk_info
    
    async def _read_chunk_from_disk(self, chunk_id: int) -> Optional[ChunkInfo]:
        """Lee un chunk directamente del disco."""
        start = chunk_id * self.chunk_size
        end = min(start + self.chunk_size, self.file_info.file_size)
        length = end - start
        
        # Control de concurrencia para evitar sobrecarga
        async with self.read_semaphore:
            try:
                # Lectura atómica con pread (no afecta offset)
                data = await asyncio.to_thread(
                    os.pread, self.fd, length, start
                )
                
                checksum = hashlib.md5(data).hexdigest()
                
                return ChunkInfo(
                    chunk_id=chunk_id,
                    data=data,
                    checksum=checksum,
                    size=len(data),
                    offset=start,
                    is_last=(chunk_id == self.file_info.total_chunks - 1)
                )
            except Exception as e:
                logger.error(f"Error leyendo chunk {chunk_id}: {e}")
                return None
    
    def get_file_info(self) -> Optional[FileInfo]:
        return self.file_info if self.precomputed else None
    
    def get_server_stats(self) -> Dict:
        """Estadísticas del servidor."""
        uptime = time.time() - self.start_time
        cache_size = len(self.cache)
        cache_efficiency = self.cache_hits / (self.cache_hits + self.cache_misses) * 100 if (self.cache_hits + self.cache_misses) > 0 else 0
        
        return {
            "precomputed": self.precomputed,
            "total_chunks": self.file_info.total_chunks if self.precomputed else 0,
            "total_requests": self.total_requests,
            "active_clients": len(self.client_stats),
            "uptime_seconds": uptime,
            "requests_per_second": self.total_requests / uptime if uptime > 0 else 0,
            "cache_size": cache_size,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "cache_efficiency": f"{cache_efficiency:.1f}%",
            "concurrent_reads": self.max_concurrent_reads
        }
    
    def _detect_file_type(self, file_path: Path) -> str:
        """Detecta tipo MIME por extensión."""
        extension = file_path.suffix.lower()
        type_map = {
            '.mp4': 'video/mp4',
            '.avi': 'video/avi',
            '.mkv': 'video/x-matroska',
            '.mov': 'video/quicktime',
            '.wmv': 'video/x-ms-wmv',
            '.flv': 'video/x-flv',
            '.pdf': 'application/pdf',
            '.zip': 'application/zip',
            '.exe': 'application/octet-stream'
        }
        return type_map.get(extension, 'application/octet-stream')