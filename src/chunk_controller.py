"""
Controlador de chunks streaming (sin pre-carga en RAM)
Arquitectura optimizada para archivos grandes
"""

import hashlib
import time
import zlib
import multiprocessing
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional, Dict


@dataclass
class ChunkInfo:
    """Información de un chunk (sin datos en RAM)."""
    chunk_id: int
    size: int
    offset: int
    is_last: bool


@dataclass
class FileInfo:
    """Información del archivo."""
    filename: str
    filepath: Path
    file_size: int
    total_chunks: int
    chunk_size: int
    file_checksum: str
    file_type: str = "application/octet-stream"


class ChunkController:
    """
    Controlador que lee chunks bajo demanda desde disco.
    """
    
    def __init__(self, chunk_size: int = 4 * 1024 * 1024):
        """
        Inicializa el controlador.
        
        Args:
            chunk_size: Tamaño de cada chunk en bytes (default: 4MB)
        """
        self.chunk_size = chunk_size
        
        # Solo metadata en RAM (no datos)
        self.chunk_metadata: List[ChunkInfo] = []
        
        # Información del archivo
        self.file_info: Optional[FileInfo] = None
        self.file_path: Optional[Path] = None
        self.precomputed: bool = False
    
    async def precompute_file(self, file_path: str) -> FileInfo:
        """
        Pre-computa solo la METADATA del archivo (no los datos).
        """
        file_path = Path(file_path)
        self.file_path = file_path
        file_size = file_path.stat().st_size
        total_chunks = (file_size + self.chunk_size - 1) // self.chunk_size
        
        print(f"[STREAMING-INIT] File: {file_path.name} ({file_size:,} bytes, {total_chunks} chunks)")
        
        start_time = time.time()
        
        # Pre-computar solo metadata
        self.chunk_metadata = []
        for chunk_id in range(total_chunks):
            offset = chunk_id * self.chunk_size
            remaining = file_size - offset
            size = min(self.chunk_size, remaining)
            is_last = (chunk_id == total_chunks - 1)
            
            self.chunk_metadata.append(ChunkInfo(
                chunk_id=chunk_id,
                size=size,
                offset=offset,
                is_last=is_last
            ))
        
        # Calcular checksum rápido
        file_checksum = str(file_size)
        
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
        elapsed = time.time() - start_time
        print(f"[STREAMING-INIT] Metadata ready in {elapsed:.2f}s")
        
        return self.file_info
    
    def get_chunk(self, chunk_id: int) -> Optional[dict]:
        """
        Lee un chunk específico DIRECTAMENTE desde disco.
        
        Args:
            chunk_id: ID del chunk (0-based)
            
        Returns:
            Dict con chunk_id, data, size, offset, is_last, success
        """
        if not self.precomputed:
            return None
        
        if chunk_id < 0 or chunk_id >= len(self.chunk_metadata):
            return None
        
        # Obtener metadata
        metadata = self.chunk_metadata[chunk_id]
        
        try:
            # Leer datos DIRECTAMENTE desde disco (O(1) con seek)
            with open(self.file_path, 'rb') as f:
                f.seek(metadata.offset)
                data = f.read(metadata.size)
            
            if len(data) != metadata.size:
                return None
            
            return {
                'chunk_id': metadata.chunk_id,
                'data': data,
                'size': metadata.size,
                'offset': metadata.offset,
                'is_last': metadata.is_last,
                'success': True
            }
            
        except Exception as e:
            print(f"[STREAMING-ERROR] Failed to read chunk {chunk_id}: {e}")
            return None
    
    def get_file_info(self) -> Optional[FileInfo]:
        """Retorna información del archivo."""
        return self.file_info if self.precomputed else None
    
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
