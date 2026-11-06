"""MVentX Desktop Manager UI Components."""

from manager.ui.add_environment import AddEnvironmentScreen
from manager.ui.dashboard import DashboardScreen
from manager.ui.environment_library import EnvironmentLibraryScreen
from manager.ui.main_window import MainWindow
from manager.ui.settings import SettingsScreen
from manager.ui.storage_manager import StorageManagerScreen
from manager.ui.styles import DARK_GRAPHITE_THEME

__all__ = [
    "AddEnvironmentScreen",
    "DARK_GRAPHITE_THEME",
    "DashboardScreen",
    "EnvironmentLibraryScreen",
    "MainWindow",
    "SettingsScreen",
    "StorageManagerScreen",
]

