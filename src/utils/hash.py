import hashlib
import pickle
from pathlib import Path
from typing import Union, Any

def calculate_md5(*args: Any, read_path_object: bool = False) -> str:
    """
    Calculates MD5 hash of given arguments.
    """
    m = hashlib.md5()
    for arg in args:
        if read_path_object and isinstance(arg, Path):
            if arg.exists():
                arg = arg.read_bytes()
            else:
                arg = b""
        
        if isinstance(arg, str):
            arg = arg.encode()
        elif not isinstance(arg, bytes):
            arg = pickle.dumps(arg)
            
        m.update(arg)
    return m.hexdigest()
