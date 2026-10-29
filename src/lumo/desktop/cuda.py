"""Подключение CUDA-библиотек из pip-пакетов nvidia-* для faster-whisper на Windows."""

import importlib.util
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)

_registered = False


def register_nvidia_dlls() -> None:
    """Добавляет каталоги bin пакетов nvidia-cublas/cudnn в поиск DLL (только Windows)."""
    global _registered
    if _registered or sys.platform != "win32":
        return
    _registered = True
    spec = importlib.util.find_spec("nvidia")
    if spec is None or not spec.submodule_search_locations:
        return
    for root in spec.submodule_search_locations:
        for bin_dir in Path(root).glob("*/bin"):
            os.add_dll_directory(str(bin_dir))
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
            log.debug("CUDA DLL: %s", bin_dir)


def cuda_available() -> bool:
    register_nvidia_dlls()
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False
