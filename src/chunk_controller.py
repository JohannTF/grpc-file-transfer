"""
Controlador de chunks pre-computados para RPC
Arquitectura simplificada sin productor-consumidor
"""

import hashlib
import time
import multiprocessing
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional, Dict
from concurrent.futures import ProcessPoolExecutor
import psutil


@dataclass
class ChunkInfo:
    """Información de un chunk pre-computado."""
    chunk_id: int
    data: bytes
    checksum: str
    size: int
    offset: int
    is_last: bool


@dataclass
class FileInfo:
    """Información del archivo pre-computado."""
    filename: str
    filepath: Path
    file_size: int
    total_chunks: int
    chunk_size: int
    file_checksum: str
    file_type: str = "application/octet-stream"


class PrecomputedChunkController:
    """
    Controlador  que pre-computa todos los chunks en memoria.
    
    Ventajas:
    - Acceso O(1) a cualquier chunk
    - Sin locks (arrays inmutables)
    - Latencia consistente <2ms
    - Eliminación de I/O durante operación
    """
    
    def __init__(self, chunk_size: int = 4 * 1024 * 1024):
        """
        Inicializa el controlador.
        
        Args:
            chunk_size: Tamaño de cada chunk en bytes (default: 4MB)
        """
        self.chunk_size = chunk_size
        
        # Arrays inmutables (sin locks necesarios)
        self.chunks: List[bytes] = []
        self.checksums: List[str] = []
        
        # Información del archivo
        self.file_info: Optional[FileInfo] = None
        self.precomputed: bool = False
        
        # Estadísticas
        self.total_requests: int = 0
        self.start_time: float = time.time()
        self.client_stats: Dict[str, Dict] = {}
    
    async def precompute_file(self, file_path: str) -> FileInfo:
        """
        Pre-computa todo el archivo dividido en chunks.
        
        Args:
            file_path: Ruta al archivo
            
        Returns:
            Información del archivo pre-computado
            
        Raises:
            FileNotFoundError: Si el archivo no existe
            MemoryError: Si no hay suficiente memoria
        """
        file_path = Path(file_path)
        
        if not file_path.exists():
            raise FileNotFoundError(f"Archivo no encontrado: {file_path}")
        
        file_size = file_path.stat().st_size
        total_chunks = (file_size + self.chunk_size - 1) // self.chunk_size
        
        print(f"[INIT] Loading file: {file_path.name} ({file_size:,} bytes, {total_chunks} chunks)")
        
        # Verificar memoria disponible
        available_memory = psutil.virtual_memory().available
        required_memory = file_size + (total_chunks * 100)  # +overhead
        
        if required_memory > available_memory * 0.8:
            raise MemoryError(
                f"Insufficient memory. Required: {required_memory:,}, Available: {available_memory:,}"
            )
        
        # Decidir estrategia de pre-cómputo
        num_cores = multiprocessing.cpu_count()
        if file_size > 100 * 1024 * 1024 and num_cores > 2:  # >100MB y múltiples cores
            print(f"[INIT] Using parallel precomputation with {num_cores} cores")
            await self._precompute_parallel(file_path, file_size, total_chunks)
        else:
            print("[INIT] Using sequential precomputation")
            await self._precompute_sequential(file_path, file_size, total_chunks)
        
        # Calcular checksum del archivo completo
        file_checksum = await self._calculate_file_checksum(file_path)
        
        # Crear información del archivo
        self.file_info = FileInfo(
            filename=file_path.name,
            filepath=file_path,
            file_size=file_size,
            total_chunks=total_chunks,
            chunk_size=self.chunk_size,
            file_checksum=file_checksum,
            file_type=self._detect_file_type(file_path)
        )
        
        self.precomputed = True
        
        print(f"[INIT] Precomputation completed - Memory: {required_memory / 1024 / 1024:.1f} MB")
        
        return self.file_info
    
    async def _precompute_sequential(self, file_path: Path, file_size: int, total_chunks: int):
        """Pre-cómputo secuencial."""
        start_time = time.time()
        
        with open(file_path, 'rb') as f:
            for chunk_id in range(total_chunks):
                data = f.read(self.chunk_size)
                if not data:
                    break
                
                checksum = hashlib.md5(data).hexdigest()
                
                self.chunks.append(data)
                self.checksums.append(checksum)
                
                # # Progress cada 50 chunks (con chunks de 4MB)
                # if (chunk_id + 1) % 50 == 0 or chunk_id == total_chunks - 1:
                #     progress = (chunk_id + 1) / total_chunks * 100
                #     print(f"[PRECOMP] Progress: {progress:.0f}% ({chunk_id + 1}/{total_chunks})")
    
    async def _precompute_parallel(self, file_path: Path, file_size: int, total_chunks: int):
        """Pre-cómputo paralelo usando múltiples procesos."""
        start_time = time.time()
        num_cores = multiprocessing.cpu_count()
        
        # Dividir trabajo entre procesos
        chunks_per_process = total_chunks // num_cores
        tasks = []
        
        for process_id in range(num_cores):
            start_chunk = process_id * chunks_per_process
            end_chunk = start_chunk + chunks_per_process
            
            if process_id == num_cores - 1:  # Último proceso toma chunks restantes
                end_chunk = total_chunks
            
            tasks.append((str(file_path), start_chunk, end_chunk, self.chunk_size))
        
         # Ejecutar en paralelo
        with ProcessPoolExecutor(max_workers=num_cores) as executor:
            print(f"[PRECOMP] Processing {len(tasks)} segments in parallel")
            
            results = list(executor.map(_process_file_segment, tasks))
        
        # Combinar resultados en orden
        self.chunks = [None] * total_chunks
        self.checksums = [None] * total_chunks
        
        for segment_results in results:
            for chunk_id, data, checksum in segment_results:
                self.chunks[chunk_id] = data
                self.checksums[chunk_id] = checksum
        
        elapsed = time.time() - start_time
        print(f"[PRECOMP] Parallel processing completed in {elapsed:.2f}s")
    
    def get_chunk(self, chunk_id: int, client_id: str = "unknown") -> Optional[ChunkInfo]:
        """
        Obtiene un chunk específico por ID.
        
        Args:
            chunk_id: ID del chunk (0-based)
            client_id: ID del cliente para estadísticas
            
        Returns:
            Información del chunk o None si no es válido
        """
        if not self.precomputed:
            return None
        
        if chunk_id < 0 or chunk_id >= len(self.chunks):
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
        stats["bytes_sent"] += len(self.chunks[chunk_id])
        stats["last_request"] = time.time()
        
        # Acceso directo O(1) - SIN LOCKS
        return ChunkInfo(
            chunk_id=chunk_id,
            data=self.chunks[chunk_id],
            checksum=self.checksums[chunk_id],
            size=len(self.chunks[chunk_id]),
            offset=chunk_id * self.chunk_size,
            is_last=(chunk_id == len(self.chunks) - 1)
        )
    
    def get_file_info(self) -> Optional[FileInfo]:
        """Retorna información del archivo pre-computado."""
        return self.file_info if self.precomputed else None
    
    def get_server_stats(self) -> Dict:
        """Retorna estadísticas del servidor."""
        uptime = time.time() - self.start_time
        
        return {
            "precomputed": self.precomputed,
            "total_chunks": len(self.chunks) if self.precomputed else 0,
            "total_requests": self.total_requests,
            "active_clients": len(self.client_stats),
            "uptime_seconds": uptime,
            "chunks_per_second": self.total_requests / uptime if uptime > 0 else 0,
            "memory_usage_mb": self._calculate_memory_usage(),
            "client_stats": dict(self.client_stats)
        }
    
    def _calculate_memory_usage(self) -> float:
        """Calcula el uso de memoria en MB."""
        if not self.precomputed:
            return 0.0
        
        chunks_memory = sum(len(chunk) for chunk in self.chunks)
        checksums_memory = sum(len(checksum.encode()) for checksum in self.checksums)
        
        return (chunks_memory + checksums_memory) / 1024 / 1024
    
    async def _calculate_file_checksum(self, file_path: Path) -> str:
        """Calcula el checksum SHA-256 del archivo completo."""
        hash_sha256 = hashlib.sha256()
        
        with open(file_path, 'rb') as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                hash_sha256.update(chunk)
        
        return hash_sha256.hexdigest()
    
    def _detect_file_type(self, file_path: Path) -> str:
        """Detecta el tipo de archivo basado en la extensión."""
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


def _process_file_segment(args):
    """
    Función para procesar un segmento del archivo en proceso separado.
    
    Args:
        args: Tupla (file_path, start_chunk, end_chunk, chunk_size)
        
    Returns:
        Lista de tuplas (chunk_id, data, checksum)
    """
    file_path, start_chunk, end_chunk, chunk_size = args
    results = []
    
    with open(file_path, 'rb') as f:
        f.seek(start_chunk * chunk_size)
        
        for chunk_id in range(start_chunk, end_chunk):
            data = f.read(chunk_size)
            if not data:
                break
            
            checksum = hashlib.md5(data).hexdigest()
            results.append((chunk_id, data, checksum))
    
    return results
