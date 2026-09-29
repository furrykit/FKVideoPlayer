from PyQt5.QtCore import QSize
from PyQt5.QtGui import QFontMetrics
from PyQt5.QtWidgets import QTabBar


class AutoAdjustTabBar(QTabBar):
    """QTabBar that dynamically calculates tabSizeHint based on the actual
    text font metrics (including bold state for selected tab and extra horizontal margin)
    so tab titles are never cut off in any language, font, or DPI scaling."""

    def __init__(self, parent=None, extra_padding=48, min_tab_width=110):
        super().__init__(parent)
        self.extra_padding = extra_padding
        self.min_tab_width = min_tab_width

    def tabSizeHint(self, index: int) -> QSize:
        hint = super().tabSizeHint(index)
        text = self.tabText(index)
        if not text:
            return hint

        # Use bold font metrics to ensure tab has enough room even when selected and bold
        f = self.font()
        f.setBold(True)
        if f.pointSize() < 9:
            f.setPointSize(10)
        fm = QFontMetrics(f)

        text_w = fm.horizontalAdvance(text) if hasattr(fm, 'horizontalAdvance') else fm.width(text)
        icon = self.tabIcon(index)
        icon_w = (self.iconSize().width() + 8) if not icon.isNull() else 0
        close_btn_w = 26 if self.tabsClosable() else 0

        target_w = max(self.min_tab_width, text_w + icon_w + close_btn_w + self.extra_padding)
        target_h = max(hint.height(), fm.height() + 16)
        return QSize(int(target_w), int(target_h))
