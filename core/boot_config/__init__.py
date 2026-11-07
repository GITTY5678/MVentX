"""MVentX Boot Configuration Generation Subsystem.

Converts validated JSON environment definitions into native GRUB 2
configuration scripts (mventx_menu.cfg).
"""

from typing import List

from core.boot_config.windows_boot_generator import generate_windows_boot_commands
from core.boot_config.windows_boot_strategy import (
    BCDPathAnalysis,
    WindowsBootStrategy,
    WindowsStrategyEvaluator,
    WindowsVerificationLevel,
)

__all__: List[str] = [
    "generator",
    "generate_windows_boot_commands",
    "WindowsBootStrategy",
    "WindowsVerificationLevel",
    "BCDPathAnalysis",
    "WindowsStrategyEvaluator",
]

