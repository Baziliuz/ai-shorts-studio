from PySide6.QtCore import Qt,QRectF,QPointF
from PySide6.QtGui import QPixmap,QPainter,QPen,QColor
from PySide6.QtWidgets import QWidget,QDialog,QVBoxLayout,QComboBox,QLabel,QDialogButtonBox
from ai_shorts_studio.domain.model import Rect

class Canvas(QWidget):
    def __init__(self,path,regions):
        super().__init__()
        self.image=QPixmap(str(path)); self.regions=regions; self.active='crop'; self.anchor=None
        self.setMinimumSize(640,360)
        self.setMouseTracking(True)

    def image_rect(self):
        if self.image.isNull(): return QRectF()
        size=self.image.size(); size.scale(self.size(),Qt.AspectRatioMode.KeepAspectRatio)
        return QRectF((self.width()-size.width())/2,(self.height()-size.height())/2,size.width(),size.height())

    def paintEvent(self,event):
        painter=QPainter(self); target=self.image_rect()
        painter.drawPixmap(target,self.image,QRectF(self.image.rect()))
        for key,color in [('crop','#ffc857'),('camera','#7d9dff'),('game','#54d6a3')]:
            rect=self.regions[key]
            painter.setPen(QPen(QColor(color),3 if key==self.active else 1))
            painter.drawRect(QRectF(target.x()+rect.x*target.width(),target.y()+rect.y*target.height(),rect.w*target.width(),rect.h*target.height()))

    def normalized(self,position):
        rect=self.image_rect()
        return QPointF(max(0,min(1,(position.x()-rect.x())/rect.width())),max(0,min(1,(position.y()-rect.y())/rect.height())))

    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton and self.image_rect().contains(event.position()):
            self.anchor=self.normalized(event.position())

    def mouseMoveEvent(self,event):
        if self.anchor is None: return
        point=self.normalized(event.position()); x,y=min(point.x(),self.anchor.x()),min(point.y(),self.anchor.y())
        w,h=abs(point.x()-self.anchor.x()),abs(point.y()-self.anchor.y())
        if w>=.005 and h>=.005:
            self.regions[self.active]=Rect(x,y,w,h); self.update()

    def mouseReleaseEvent(self,event):
        self.mouseMoveEvent(event); self.anchor=None

class RegionDialog(QDialog):
    def __init__(self,path,regions,parent=None):
        super().__init__(parent); self.setWindowTitle('Области исходного кадра'); self.resize(980,720)
        layout=QVBoxLayout(self); selector=QComboBox()
        for title,key in [('Кадрирование · жёлтый','crop'),('Камера · синий','camera'),('Игра · зелёный','game')]: selector.addItem(title,key)
        layout.addWidget(selector); self.canvas=Canvas(path,regions); layout.addWidget(self.canvas,1)
        selector.currentIndexChanged.connect(lambda _:self.select(selector.currentData()))
        note=QLabel('Выберите область в списке и выделите её мышью. Камера не должна дублироваться в области игры. Закрытые части игры не восстанавливаются.')
        note.setWordWrap(True); layout.addWidget(note)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def select(self,key):
        self.canvas.active=key; self.canvas.update()
