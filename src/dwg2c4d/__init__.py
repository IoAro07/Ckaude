"""dwg2c4d: planimetria 2D (DWG/DXF) -> modello 3D OBJ per Cinema 4D."""

from .config import Config
from .pipeline import ConversionReport, convert

__all__ = ["Config", "ConversionReport", "convert"]
__version__ = "0.3.1"
