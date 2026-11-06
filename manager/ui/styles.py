"""MVentX Desktop Manager Stylesheet and Palette Definitions.

Implements a professional dark graphite desktop theme with subtle blue accents,
designed to look like a native system management utility.
"""

# Palette Constants
COLOR_BG = "#131417"
COLOR_SIDEBAR = "#1a1c22"
COLOR_SURFACE = "#21242c"
COLOR_SURFACE_HOVER = "#2a2e38"
COLOR_SURFACE_ACTIVE = "#333742"
COLOR_BORDER = "#2e323b"
COLOR_BORDER_FOCUS = "#3b82f6"

COLOR_ACCENT = "#3b82f6"
COLOR_ACCENT_HOVER = "#2563eb"
COLOR_ACCENT_ACTIVE = "#1d4ed8"
COLOR_ACCENT_LIGHT = "rgba(59, 130, 246, 0.15)"

COLOR_TEXT_PRIMARY = "#f3f4f6"
COLOR_TEXT_SECONDARY = "#9ca3af"
COLOR_TEXT_MUTED = "#6b7280"

COLOR_SUCCESS = "#10b981"
COLOR_SUCCESS_BG = "rgba(16, 185, 129, 0.15)"
COLOR_WARNING = "#f59e0b"
COLOR_WARNING_BG = "rgba(245, 158, 11, 0.15)"
COLOR_DANGER = "#ef4444"
COLOR_DANGER_BG = "rgba(239, 68, 68, 0.15)"

DARK_GRAPHITE_THEME = f"""
/* Global Reset and Typography */
QWidget {{
    background-color: {COLOR_BG};
    color: {COLOR_TEXT_PRIMARY};
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
    selection-background-color: {COLOR_ACCENT};
    selection-color: #ffffff;
}}

/* Main Window & Sidebar */
QMainWindow {{
    background-color: {COLOR_BG};
}}

#sidebar {{
    background-color: {COLOR_SIDEBAR};
    border-right: 1px solid {COLOR_BORDER};
    min-width: 210px;
    max-width: 210px;
}}

#sidebarHeader {{
    padding: 18px 16px;
    border-bottom: 1px solid {COLOR_BORDER};
}}

#sidebarTitle {{
    font-size: 16px;
    font-weight: 700;
    color: {COLOR_TEXT_PRIMARY};
    letter-spacing: 0.5px;
}}

#sidebarSubtitle {{
    font-size: 11px;
    color: {COLOR_TEXT_MUTED};
    margin-top: 2px;
}}

/* Sidebar Nav Buttons */
QPushButton.navButton {{
    background-color: transparent;
    color: {COLOR_TEXT_SECONDARY};
    text-align: left;
    padding: 10px 16px;
    border: none;
    border-left: 3px solid transparent;
    border-radius: 0px;
    font-size: 13px;
    font-weight: 500;
}}

QPushButton.navButton:hover {{
    background-color: {COLOR_SURFACE};
    color: {COLOR_TEXT_PRIMARY};
}}

QPushButton.navButton:checked, QPushButton.navButton.active {{
    background-color: {COLOR_SURFACE_HOVER};
    color: #ffffff;
    border-left: 3px solid {COLOR_ACCENT};
    font-weight: 600;
}}

/* Top Header Bar */
#headerBar {{
    background-color: {COLOR_SIDEBAR};
    border-bottom: 1px solid {COLOR_BORDER};
    padding: 12px 24px;
}}

#deviceSelectorLabel {{
    color: {COLOR_TEXT_SECONDARY};
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}}

/* Content Area */
#contentArea {{
    background-color: {COLOR_BG};
    padding: 24px;
}}

/* Card Container */
QFrame.card {{
    background-color: {COLOR_SURFACE};
    border: 1px solid {COLOR_BORDER};
    border-radius: 8px;
    padding: 16px;
}}

QFrame.card:hover {{
    border: 1px solid {COLOR_BORDER_FOCUS};
}}

/* Metric Cards */
QLabel.metricValue {{
    font-size: 26px;
    font-weight: 700;
    color: {COLOR_TEXT_PRIMARY};
}}

QLabel.metricLabel {{
    font-size: 12px;
    font-weight: 500;
    color: {COLOR_TEXT_SECONDARY};
    text-transform: uppercase;
    letter-spacing: 0.5px;
}}

/* Standard Buttons */
QPushButton {{
    background-color: {COLOR_SURFACE};
    color: {COLOR_TEXT_PRIMARY};
    border: 1px solid {COLOR_BORDER};
    border-radius: 6px;
    padding: 8px 16px;
    font-size: 13px;
    font-weight: 500;
}}

QPushButton:hover {{
    background-color: {COLOR_SURFACE_HOVER};
    border-color: {COLOR_BORDER_FOCUS};
}}

QPushButton:pressed {{
    background-color: {COLOR_SURFACE_ACTIVE};
}}

QPushButton:disabled {{
    background-color: {COLOR_SURFACE};
    color: {COLOR_TEXT_MUTED};
    border-color: {COLOR_BORDER};
}}

/* Primary Action Buttons */
QPushButton.primaryButton {{
    background-color: {COLOR_ACCENT};
    color: #ffffff;
    border: 1px solid {COLOR_ACCENT};
    font-weight: 600;
}}

QPushButton.primaryButton:hover {{
    background-color: {COLOR_ACCENT_HOVER};
    border-color: {COLOR_ACCENT_HOVER};
}}

QPushButton.primaryButton:pressed {{
    background-color: {COLOR_ACCENT_ACTIVE};
}}

/* Danger Action Buttons */
QPushButton.dangerButton {{
    background-color: {COLOR_DANGER_BG};
    color: {COLOR_DANGER};
    border: 1px solid {COLOR_DANGER};
}}

QPushButton.dangerButton:hover {{
    background-color: {COLOR_DANGER};
    color: #ffffff;
}}

/* Form Controls & Inputs */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox {{
    background-color: {COLOR_SURFACE};
    color: {COLOR_TEXT_PRIMARY};
    border: 1px solid {COLOR_BORDER};
    border-radius: 6px;
    padding: 8px 12px;
    font-size: 13px;
}}

QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus {{
    border: 1px solid {COLOR_BORDER_FOCUS};
    background-color: {COLOR_SURFACE_HOVER};
}}

QComboBox {{
    background-color: {COLOR_SURFACE};
    color: {COLOR_TEXT_PRIMARY};
    border: 1px solid {COLOR_BORDER};
    border-radius: 6px;
    padding: 7px 12px;
    min-height: 20px;
}}

QComboBox:hover {{
    border-color: {COLOR_BORDER_FOCUS};
}}

QComboBox::drop-down {{
    border: none;
    width: 24px;
}}

QComboBox QAbstractItemView {{
    background-color: {COLOR_SURFACE};
    color: {COLOR_TEXT_PRIMARY};
    border: 1px solid {COLOR_BORDER};
    selection-background-color: {COLOR_ACCENT};
    selection-color: #ffffff;
    padding: 4px;
}}

/* Tables */
QTableWidget, QTableView {{
    background-color: {COLOR_SURFACE};
    border: 1px solid {COLOR_BORDER};
    border-radius: 8px;
    gridline-color: {COLOR_BORDER};
    color: {COLOR_TEXT_PRIMARY};
    selection-background-color: {COLOR_ACCENT_LIGHT};
    selection-color: {COLOR_TEXT_PRIMARY};
}}

QHeaderView::section {{
    background-color: {COLOR_SIDEBAR};
    color: {COLOR_TEXT_SECONDARY};
    font-weight: 600;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    padding: 10px 12px;
    border: none;
    border-bottom: 1px solid {COLOR_BORDER};
    border-right: 1px solid {COLOR_BORDER};
}}

QTableWidget::item {{
    padding: 8px 12px;
    border-bottom: 1px solid {COLOR_BORDER};
}}

QTableWidget::item:selected {{
    background-color: {COLOR_ACCENT_LIGHT};
}}

/* Progress Bar */
QProgressBar {{
    background-color: {COLOR_SURFACE};
    border: 1px solid {COLOR_BORDER};
    border-radius: 6px;
    text-align: center;
    color: {COLOR_TEXT_PRIMARY};
    font-size: 11px;
    font-weight: 600;
    height: 18px;
}}

QProgressBar::chunk {{
    background-color: {COLOR_ACCENT};
    border-radius: 5px;
}}

/* Scrollbars */
QScrollBar:vertical {{
    background: {COLOR_SIDEBAR};
    width: 8px;
    margin: 0px;
}}

QScrollBar::handle:vertical {{
    background: {COLOR_BORDER};
    min-height: 24px;
    border-radius: 4px;
}}

QScrollBar::handle:vertical:hover {{
    background: {COLOR_TEXT_MUTED};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}

/* Status Badge Pills */
QLabel.statusBadgeEnabled {{
    background-color: {COLOR_SUCCESS_BG};
    color: {COLOR_SUCCESS};
    border: 1px solid {COLOR_SUCCESS};
    border-radius: 12px;
    padding: 2px 10px;
    font-size: 11px;
    font-weight: 600;
}}

QLabel.statusBadgeDisabled {{
    background-color: {COLOR_SURFACE_ACTIVE};
    color: {COLOR_TEXT_MUTED};
    border: 1px solid {COLOR_BORDER};
    border-radius: 12px;
    padding: 2px 10px;
    font-size: 11px;
    font-weight: 600;
}}

QLabel.statusBadgeWarning {{
    background-color: {COLOR_WARNING_BG};
    color: {COLOR_WARNING};
    border: 1px solid {COLOR_WARNING};
    border-radius: 12px;
    padding: 2px 10px;
    font-size: 11px;
    font-weight: 600;
}}
"""

