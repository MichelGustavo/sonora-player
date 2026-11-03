from __future__ import annotations

import base64
import mimetypes
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from mutagen import File as MutagenFile
from mutagen.flac import Picture
from mutagen.id3 import APIC
from mutagen.mp4 import MP4Cover

from PySide6.QtCore import QEvent, QPointF, QRectF, QSettings, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QCursor,
    QIcon,
    QImage,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer


APP_NAME = "SONORA"
AUDIO_EXTENSIONS = {
    ".mp3", ".flac", ".wav", ".m4a", ".aac", ".ogg", ".opus",
    ".wma", ".aiff", ".aif", ".mp4",
}


@dataclass
class Track:
    path: str
    title: str
    artist: str
    album: str
    album_artist: str
    year: str
    track_number: str
    disc_number: str
    genre: str
    duration: float
    cover: Optional[bytes] = None

    @property
    def display_artist(self) -> str:
        return self.artist or "Artista desconhecido"

    @property
    def display_album(self) -> str:
        return self.album or "Álbum desconhecido"


# ---------- Leitura e gravação de metadados ----------

def _first_tag(tags, key: str, default: str = "") -> str:
    if not tags:
        return default
    value = tags.get(key)
    if value is None:
        return default
    if isinstance(value, (list, tuple)):
        value = value[0] if value else default
    text = str(value).strip()
    return text or default


def read_cover(path: str) -> Optional[bytes]:
    try:
        audio = MutagenFile(path)
        if audio is None:
            return None
        # FLAC e alguns contêineres Ogg expõem as imagens desta forma.
        pictures = getattr(audio, "pictures", None)
        if pictures:
            return pictures[0].data

        tags = getattr(audio, "tags", None)
        if not tags:
            return None

        # ID3 (MP3, WAV e AIFF)
        if hasattr(tags, "getall"):
            images = tags.getall("APIC")
            if images:
                return images[0].data

        # MP4 / M4A
        covr = tags.get("covr") if hasattr(tags, "get") else None
        if covr:
            return bytes(covr[0])

        # Vorbis comments / Opus: imagem FLAC serializada em base64.
        encoded = tags.get("metadata_block_picture") if hasattr(tags, "get") else None
        if encoded:
            raw = encoded[0] if isinstance(encoded, (list, tuple)) else encoded
            picture = Picture(base64.b64decode(raw))
            return picture.data

        # Tags antigos de Vorbis.
        coverart = tags.get("coverart") if hasattr(tags, "get") else None
        if coverart:
            raw = coverart[0] if isinstance(coverart, (list, tuple)) else coverart
            return base64.b64decode(raw)
    except Exception:
        return None
    return None


def read_track(path: str) -> Optional[Track]:
    try:
        audio = MutagenFile(path, easy=True)
        if audio is None:
            return None
        tags = audio.tags or {}
        title = _first_tag(tags, "title", Path(path).stem)
        artist = _first_tag(tags, "artist", "Artista desconhecido")
        album = _first_tag(tags, "album", "Álbum desconhecido")
        album_artist = _first_tag(tags, "albumartist", artist)
        year = _first_tag(tags, "date", _first_tag(tags, "year", ""))
        track_number = _first_tag(tags, "tracknumber", "")
        disc_number = _first_tag(tags, "discnumber", "")
        genre = _first_tag(tags, "genre", "")
        duration = float(getattr(getattr(audio, "info", None), "length", 0) or 0)
        return Track(
            path=path,
            title=title,
            artist=artist,
            album=album,
            album_artist=album_artist,
            year=year,
            track_number=track_number,
            disc_number=disc_number,
            genre=genre,
            duration=duration,
            cover=read_cover(path),
        )
    except Exception:
        return None


def save_tags(track_path: str, values: dict[str, str], cover_path: Optional[str] = None) -> None:
    audio = MutagenFile(track_path, easy=True)
    if audio is None:
        raise RuntimeError("Este formato de arquivo não permite edição de metadados pelo SONORA.")
    if audio.tags is None:
        audio.add_tags()
        # Reabre em modo easy para que os nomes amigáveis (title, artist etc.)
        # continuem disponíveis também em arquivos que ainda não tinham tags.
        audio = MutagenFile(track_path, easy=True)
        if audio is None or audio.tags is None:
            raise RuntimeError("Não foi possível inicializar os metadados deste arquivo.")
    tags = audio.tags
    fields = {
        "title": "title",
        "artist": "artist",
        "album": "album",
        "albumartist": "albumartist",
        "date": "date",
        "tracknumber": "tracknumber",
        "discnumber": "discnumber",
        "genre": "genre",
    }
    for form_name, tag_name in fields.items():
        value = values.get(form_name, "").strip()
        try:
            if value:
                tags[tag_name] = [value]
            elif tag_name in tags:
                del tags[tag_name]
        except Exception as exc:
            raise RuntimeError(f"Não foi possível gravar o campo '{tag_name}': {exc}") from exc
    audio.save()

    if cover_path:
        try:
            _embed_cover(track_path, cover_path)
        except Exception as exc:
            # Os campos de texto já foram gravados; deixa isso explícito para o usuário.
            raise RuntimeError(
                f"Os metadados de texto foram gravados, mas a capa não pôde ser incorporada: {exc}"
            ) from exc


def _embed_cover(track_path: str, cover_path: str) -> None:
    image_data = Path(cover_path).read_bytes()
    mime = mimetypes.guess_type(cover_path)[0] or "image/jpeg"
    audio = MutagenFile(track_path)
    if audio is None:
        raise RuntimeError("Não foi possível abrir o arquivo para inserir a capa.")

    # MP3, WAV e AIFF usam quadros APIC (ID3).
    if hasattr(getattr(audio, "tags", None), "getall") or Path(track_path).suffix.lower() in {".mp3", ".wav", ".aiff", ".aif"}:
        if audio.tags is None:
            audio.add_tags()
        audio.tags.delall("APIC")
        audio.tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=image_data))
        audio.save()
        return

    # FLAC.
    if hasattr(audio, "clear_pictures") and hasattr(audio, "add_picture"):
        picture = Picture()
        picture.data = image_data
        picture.type = 3
        picture.mime = mime
        picture.desc = "Cover"
        image = QImage.fromData(image_data)
        picture.width = image.width() if not image.isNull() else 0
        picture.height = image.height() if not image.isNull() else 0
        picture.depth = 24
        audio.clear_pictures()
        audio.add_picture(picture)
        audio.save()
        return

    # M4A / MP4.
    if Path(track_path).suffix.lower() in {".m4a", ".mp4"}:
        if audio.tags is None:
            audio.add_tags()
        if mime not in ("image/png", "image/jpeg"):
            raise RuntimeError("Arquivos M4A/MP4 só aceitam capas em JPG ou PNG.")
        fmt = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
        audio.tags["covr"] = [MP4Cover(image_data, imageformat=fmt)]
        audio.save()
        return

    # OGG Vorbis / Opus.
    if Path(track_path).suffix.lower() in {".ogg", ".opus"} and getattr(audio, "tags", None) is not None:
        picture = Picture()
        picture.data = image_data
        picture.type = 3
        picture.mime = mime
        picture.desc = "Cover"
        image = QImage.fromData(image_data)
        picture.width = image.width() if not image.isNull() else 0
        picture.height = image.height() if not image.isNull() else 0
        picture.depth = 24
        encoded = base64.b64encode(picture.write()).decode("ascii")
        audio.tags["metadata_block_picture"] = [encoded]
        audio.save()
        return

    raise RuntimeError("A inserção de capa não é compatível com este formato de áudio.")


# ---------- Identidade visual ----------

PALETTE = {
    "bg": "#0E0E12",
    "sidebar": "#09090C",
    "surface": "#16161B",
    "surface2": "#1D1D24",
    "elevated": "#25252E",
    "hover": "#30303B",
    "selected": "#2B2B37",
    "border": "#26262F",
    "borderStrong": "#3A3A47",
    "track": "#3A3A47",
    "text": "#F3F3F6",
    "text2": "#A3A4B2",
    "text3": "#6E6F7E",
    "accent": "#FF7A45",
    "accentHover": "#FF9262",
    "accentPress": "#E8672F",
    "onAccent": "#1A0D07",
    "danger": "#FF6B7A",
}

STYLESHEET_TEMPLATE = """
* { font-family: 'Inter', 'Segoe UI', 'Helvetica Neue', Arial, sans-serif; font-size: 13px; }
QMainWindow, QWidget#root { background: @bg; }
QWidget { color: @text; }
QLabel { background: transparent; }

/* Tipografia */
QLabel#pageTitle { font-size: 26px; font-weight: 800; }
QLabel#pageSubtitle { color: @text2; font-size: 12px; }
QLabel#eyebrow { color: @text3; font-size: 10px; font-weight: 700; }
QLabel#eyebrowAccent { color: @accent; font-size: 10px; font-weight: 800; }
QLabel#brand { font-size: 18px; font-weight: 800; }
QLabel#tagline { color: @text3; font-size: 10px; }
QLabel#emptyTitle { font-size: 22px; font-weight: 800; }
QLabel#emptyText { color: @text2; font-size: 13px; }
QLabel#countLabel { color: @text2; font-size: 12px; }
QLabel#subtle { color: @text3; font-size: 11px; }
QLabel#cardTitle { font-size: 13px; font-weight: 700; }
QLabel#cardSub { color: @text2; font-size: 12px; }
QLabel#cardMeta { color: @text3; font-size: 11px; }
QLabel#nowTitle { font-size: 13px; font-weight: 700; }
QLabel#nowArtist { color: @text2; font-size: 12px; }
QLabel#timeLabel { color: @text2; font-size: 11px; }
QLabel#volumeLabel { color: @text3; font-size: 11px; }
QLabel#detailTitle { font-size: 30px; font-weight: 800; }
QLabel#detailArtist { font-size: 14px; font-weight: 600; }
QLabel#detailMeta { color: @text2; font-size: 12px; }
QLabel#dialogTitle { font-size: 18px; font-weight: 800; }
QLabel#fieldLabel { color: @text2; font-size: 11px; font-weight: 600; }
QLabel#folderName { font-size: 12px; font-weight: 700; }
QLabel#toast {
    background: @elevated; color: @text; border: 1px solid @borderStrong; border-radius: 12px;
}
QLabel#toast[kind="error"] { border-color: @danger; color: @text; }

/* Barra lateral */
QWidget#sidebar { background: @sidebar; border-right: 1px solid @border; }
QFrame#folderCard { background: @surface; border: 1px solid @border; border-radius: 12px; }
QFrame#separator { background: @border; border: 0; max-height: 1px; min-height: 1px; }

/* Botões */
QPushButton {
    background: @elevated; color: @text; border: 1px solid @border; border-radius: 20px;
    padding: 0 18px; min-height: 38px; font-weight: 600;
}
QPushButton:hover { background: @hover; border-color: @borderStrong; }
QPushButton:pressed { background: @surface2; }
QPushButton:disabled { background: @surface; color: @text3; border-color: @border; }
QPushButton#primaryButton { background: @accent; color: @onAccent; border: 1px solid @accent; font-weight: 700; }
QPushButton#primaryButton:hover { background: @accentHover; border-color: @accentHover; }
QPushButton#primaryButton:pressed { background: @accentPress; border-color: @accentPress; }
QPushButton#primaryButton:disabled { background: @surface2; color: @text3; border-color: @border; }
QPushButton#navButton {
    background: transparent; border: 0; border-radius: 10px; color: @text2;
    text-align: left; padding: 0 14px; min-height: 42px; font-size: 14px; font-weight: 600;
}
QPushButton#navButton:hover { background: @surface; color: @text; }
QPushButton#navButton[active="true"] { background: @elevated; color: @text; }
QPushButton#ghostButton {
    background: transparent; border: 0; color: @text2; padding: 0 12px 0 6px; min-height: 32px;
}
QPushButton#ghostButton:hover { background: @surface2; color: @text; }
QPushButton#rowEditButton {
    background: @surface2; color: @text; border: 0; border-radius: 15px;
    padding: 0; min-width: 30px; max-width: 30px; min-height: 30px; max-height: 30px;
}
QPushButton#rowEditButton:hover { background: @accent; color: @onAccent; }

/* Campos */
QLineEdit {
    background: @surface2; border: 1px solid @border; border-radius: 20px; padding: 0 14px;
    min-height: 38px; color: @text; selection-background-color: @accent; selection-color: @onAccent;
}
QLineEdit:hover { border-color: @borderStrong; }
QLineEdit:focus { border: 1px solid @accent; }
QDialog QLineEdit { border-radius: 10px; min-height: 34px; padding: 0 12px; }

/* Tabelas */
QTableWidget {
    background: transparent; border: 0; outline: 0; color: @text;
    selection-background-color: @selected; selection-color: @text;
}
QTableWidget::item { padding: 0 12px; border: 0; }
QTableWidget::item:selected { background: @selected; color: @text; }
QHeaderView { background: transparent; border: 0; }
QHeaderView::section {
    background: @bg; color: @text3; border: 0; border-bottom: 1px solid @border;
    padding: 0 12px; min-height: 34px; font-size: 11px; font-weight: 700;
}
QTableCornerButton::section { background: transparent; border: 0; }

/* Barras de rolagem */
QScrollArea { border: 0; background: transparent; }
QWidget#albumViewport, QWidget#albumGridHost { background: transparent; }
QScrollBar:vertical { width: 12px; background: transparent; margin: 2px 2px 2px 0; }
QScrollBar::handle:vertical { min-height: 32px; background: @track; border-radius: 4px; margin: 0 2px; }
QScrollBar::handle:vertical:hover { background: @text3; }
QScrollBar:horizontal { height: 12px; background: transparent; margin: 0 2px 2px 2px; }
QScrollBar::handle:horizontal { min-width: 32px; background: @track; border-radius: 4px; margin: 2px 0; }
QScrollBar::handle:horizontal:hover { background: @text3; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; background: transparent; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

/* Sliders */
QSlider { min-height: 16px; }
QSlider::groove:horizontal { height: 4px; background: @track; border-radius: 2px; }
QSlider::sub-page:horizontal { background: @accent; border-radius: 2px; }
QSlider::add-page:horizontal { background: @track; border-radius: 2px; }
QSlider::handle:horizontal { background: @text; width: 12px; margin: -4px 0; border-radius: 6px; }
QSlider::handle:horizontal:hover { background: #FFFFFF; }
QSlider::sub-page:horizontal:disabled { background: @track; }
QSlider::handle:horizontal:disabled { background: @text3; }

/* Superfícies */
QFrame#playerBar { background: @surface; border-top: 1px solid @border; }
QFrame#albumCard { background: @surface; border: 1px solid transparent; border-radius: 14px; }
QFrame#albumCard:hover { background: @elevated; border-color: @border; }

/* Diálogos, menus e dicas */
QDialog, QMessageBox { background: @surface; }
QMessageBox QLabel { color: @text; }
QMessageBox QTextEdit {
    background: @surface2; color: @text2; border: 1px solid @border; border-radius: 8px;
}
QDialog QPushButton, QMessageBox QPushButton { min-width: 96px; }
QMenu { background: @elevated; border: 1px solid @borderStrong; padding: 6px; }
QMenu::item { padding: 8px 28px 8px 10px; border-radius: 6px; }
QMenu::item:selected { background: @hover; }
QMenu::separator { height: 1px; background: @border; margin: 4px 6px; }
QToolTip {
    background: @elevated; color: @text; border: 1px solid @borderStrong; padding: 5px 8px;
}
"""


def build_stylesheet() -> str:
    sheet = STYLESHEET_TEMPLATE
    # Chaves mais longas primeiro, para que "@text2" não seja trocado por "@text".
    for key in sorted(PALETTE, key=len, reverse=True):
        sheet = sheet.replace("@" + key, PALETTE[key])
    return sheet


def build_palette() -> QPalette:
    """Paleta nativa do Qt, para que diálogos padrão e campos sigam o tema escuro."""
    palette = QPalette()
    roles = {
        QPalette.Window: "bg",
        QPalette.WindowText: "text",
        QPalette.Base: "surface2",
        QPalette.AlternateBase: "surface",
        QPalette.Text: "text",
        QPalette.Button: "elevated",
        QPalette.ButtonText: "text",
        QPalette.ToolTipBase: "elevated",
        QPalette.ToolTipText: "text",
        QPalette.PlaceholderText: "text3",
        QPalette.Highlight: "accent",
        QPalette.HighlightedText: "onAccent",
        QPalette.Link: "accent",
    }
    for role, key in roles.items():
        palette.setColor(role, QColor(PALETTE[key]))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role, QColor(PALETTE["text3"]))
    return palette


def qcolor(key: str, alpha: Optional[int] = None) -> QColor:
    color = QColor(PALETTE[key])
    if alpha is not None:
        color.setAlpha(alpha)
    return color


def format_time(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"{total // 60:02d}:{total % 60:02d}"


# ---------- Ícones desenhados em código (alinhamento consistente, sem fontes) ----------

RENDER_SCALE = 2  # super-amostragem: nitidez em telas comuns e em HiDPI


def _canvas(width: int, height: Optional[int] = None):
    height = height or width
    pixmap = QPixmap(int(width * RENDER_SCALE), int(height * RENDER_SCALE))
    pixmap.fill(Qt.transparent)
    pixmap.setDevicePixelRatio(RENDER_SCALE)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    return pixmap, painter


def _poly(*points) -> QPolygonF:
    return QPolygonF([QPointF(x, y) for x, y in points])


def _stroke(color: QColor, width: float = 2.0) -> QPen:
    pen = QPen(color)
    pen.setWidthF(width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


def draw_icon(painter: QPainter, name: str, rect: QRectF, color: QColor) -> None:
    """Desenha o ícone `name` dentro de `rect`, numa grade de 24x24."""
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.translate(rect.topLeft())
    painter.scale(rect.width() / 24.0, rect.height() / 24.0)
    fill = QBrush(color)
    stroke = _stroke(color)

    if name == "play":
        painter.setPen(_stroke(color, 1.8))
        painter.setBrush(fill)
        painter.drawPolygon(_poly((7.6, 5.6), (18.2, 12), (7.6, 18.4)))
    elif name == "pause":
        painter.setPen(Qt.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(QRectF(6.6, 5, 3.8, 14), 1.3, 1.3)
        painter.drawRoundedRect(QRectF(13.6, 5, 3.8, 14), 1.3, 1.3)
    elif name == "prev":
        painter.setPen(Qt.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(QRectF(5.2, 5.2, 2.6, 13.6), 1.1, 1.1)
        painter.setPen(_stroke(color, 1.6))
        painter.drawPolygon(_poly((18.6, 6.2), (10, 12), (18.6, 17.8)))
    elif name == "next":
        painter.setPen(Qt.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(QRectF(16.2, 5.2, 2.6, 13.6), 1.1, 1.1)
        painter.setPen(_stroke(color, 1.6))
        painter.drawPolygon(_poly((5.4, 6.2), (14, 12), (5.4, 17.8)))
    elif name in ("repeat", "repeat-one"):
        painter.setPen(stroke)
        painter.setBrush(Qt.NoBrush)
        upper = QPainterPath()
        upper.moveTo(5, 12)
        upper.lineTo(5, 10)
        upper.quadTo(5, 7, 8, 7)
        upper.lineTo(17, 7)
        painter.drawPath(upper)
        lower = QPainterPath()
        lower.moveTo(19, 12)
        lower.lineTo(19, 14)
        lower.quadTo(19, 17, 16, 17)
        lower.lineTo(7, 17)
        painter.drawPath(lower)
        painter.setBrush(fill)
        painter.setPen(_stroke(color, 1.2))
        painter.drawPolygon(_poly((16, 4.2), (20.2, 7), (16, 9.8)))
        painter.drawPolygon(_poly((8, 14.2), (3.8, 17), (8, 19.8)))
        if name == "repeat-one":
            painter.setBrush(Qt.NoBrush)
            painter.setPen(_stroke(color, 1.6))
            painter.drawPolyline(_poly((10.6, 10.8), (12.2, 9.8), (12.2, 14.2)))
    elif name.startswith("volume"):
        painter.setPen(_stroke(color, 1.4))
        painter.setBrush(fill)
        painter.drawPolygon(_poly((4, 9.6), (7.8, 9.6), (12.6, 5.6), (12.6, 18.4), (7.8, 14.4), (4, 14.4)))
        painter.setBrush(Qt.NoBrush)
        painter.setPen(stroke)
        if name == "volume-mute":
            painter.drawLine(QPointF(16, 9.4), QPointF(21, 14.6))
            painter.drawLine(QPointF(21, 9.4), QPointF(16, 14.6))
        else:
            painter.drawArc(QRectF(9, 8, 8, 8), -50 * 16, 100 * 16)
            if name == "volume-high":
                painter.drawArc(QRectF(6, 5, 14, 14), -50 * 16, 100 * 16)
    elif name == "note":
        painter.setPen(Qt.NoPen)
        painter.setBrush(fill)
        painter.drawEllipse(QPointF(9, 17.4), 3.6, 3.1)
        painter.drawRoundedRect(QRectF(11.5, 4.6, 2, 13.2), 1, 1)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(stroke)
        flag = QPainterPath()
        flag.moveTo(13.4, 5)
        flag.cubicTo(14, 9, 19, 8.4, 19, 13)
        painter.drawPath(flag)
    elif name == "albums":
        painter.setPen(Qt.NoPen)
        painter.setBrush(fill)
        for x, y in ((4, 4), (13.2, 4), (4, 13.2), (13.2, 13.2)):
            painter.drawRoundedRect(QRectF(x, y, 6.8, 6.8), 2, 2)
    elif name == "list":
        painter.setPen(stroke)
        for y in (7, 12, 17):
            painter.drawLine(QPointF(4.5, y), QPointF(19.5, y))
    elif name == "folder":
        painter.setPen(stroke)
        painter.setBrush(Qt.NoBrush)
        path = QPainterPath()
        path.moveTo(3.5, 17)
        path.lineTo(3.5, 7)
        path.quadTo(3.5, 5.5, 5, 5.5)
        path.lineTo(9, 5.5)
        path.lineTo(11, 7.5)
        path.lineTo(19, 7.5)
        path.quadTo(20.5, 7.5, 20.5, 9)
        path.lineTo(20.5, 17)
        path.quadTo(20.5, 18.5, 19, 18.5)
        path.lineTo(5, 18.5)
        path.quadTo(3.5, 18.5, 3.5, 17)
        path.closeSubpath()
        painter.drawPath(path)
    elif name == "search":
        painter.setPen(stroke)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(QPointF(10.5, 10.5), 6, 6)
        painter.drawLine(QPointF(15, 15), QPointF(20, 20))
    elif name == "edit":
        painter.setPen(_stroke(color, 1.8))
        painter.setBrush(Qt.NoBrush)
        painter.drawPolygon(_poly((4.5, 19.5), (5.4, 14.8), (15.8, 4.4), (19.6, 8.2), (9.2, 18.6)))
        painter.drawLine(QPointF(13.6, 6.6), QPointF(17.4, 10.4))
    elif name == "back":
        painter.setPen(stroke)
        painter.setBrush(Qt.NoBrush)
        painter.drawPolyline(_poly((15, 5.5), (8.5, 12), (15, 18.5)))
    painter.restore()


def make_icon(name: str, color, px: int = 20) -> QIcon:
    if not isinstance(color, QColor):
        color = QColor(color)
    pixmap, painter = _canvas(px)
    draw_icon(painter, name, QRectF(0, 0, px, px), color)
    painter.end()
    return QIcon(pixmap)


def brand_pixmap(size: int) -> QPixmap:
    """Marca própria do SONORA: quadrado arredondado com barras de equalizador."""
    pixmap, painter = _canvas(size)
    painter.setPen(Qt.NoPen)
    gradient = QLinearGradient(0, 0, size, size)
    gradient.setColorAt(0, qcolor("accentHover"))
    gradient.setColorAt(1, qcolor("accentPress"))
    painter.setBrush(QBrush(gradient))
    painter.drawRoundedRect(QRectF(0, 0, size, size), size * 0.28, size * 0.28)
    painter.setBrush(qcolor("onAccent"))
    bar_w = size * 0.1
    gap = size * 0.07
    heights = (0.30, 0.52, 0.40, 0.24)
    total = bar_w * len(heights) + gap * (len(heights) - 1)
    x = (size - total) / 2
    for h in heights:
        bar_h = size * h
        painter.drawRoundedRect(QRectF(x, (size - bar_h) / 2, bar_w, bar_h), bar_w / 2, bar_w / 2)
        x += bar_w + gap
    painter.end()
    return pixmap


def placeholder_pixmap(size: int, radius: float = 10) -> QPixmap:
    """Capa padrão quando o arquivo não tem imagem incorporada."""
    pixmap, painter = _canvas(size)
    gradient = QLinearGradient(0, 0, size, size)
    gradient.setColorAt(0, qcolor("hover"))
    gradient.setColorAt(1, qcolor("surface2"))
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(gradient))
    painter.drawRoundedRect(QRectF(0, 0, size, size), radius, radius)
    icon_size = size * 0.42
    draw_icon(
        painter, "note",
        QRectF((size - icon_size) / 2, (size - icon_size) / 2, icon_size, icon_size),
        qcolor("accent", 210),
    )
    painter.end()
    return pixmap


def badge_pixmap(icon_name: str, size: int = 88) -> QPixmap:
    pixmap, painter = _canvas(size)
    painter.setPen(QPen(qcolor("border"), 1))
    painter.setBrush(qcolor("surface2"))
    painter.drawEllipse(QRectF(0.5, 0.5, size - 1, size - 1))
    icon_size = size * 0.42
    draw_icon(
        painter, icon_name,
        QRectF((size - icon_size) / 2, (size - icon_size) / 2, icon_size, icon_size),
        qcolor("accent"),
    )
    painter.end()
    return pixmap


def cover_pixmap(data: Optional[bytes], size: int, radius: float = 10) -> Optional[QPixmap]:
    """Recorta a capa em quadrado, com cantos arredondados. Devolve None se inválida."""
    if not data:
        return None
    source = QPixmap()
    if not source.loadFromData(data):
        return None
    pixels = size * RENDER_SCALE
    scaled = source.scaled(pixels, pixels, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    x = max(0, (scaled.width() - pixels) // 2)
    y = max(0, (scaled.height() - pixels) // 2)
    square = scaled.copy(x, y, pixels, pixels)
    pixmap, painter = _canvas(size)
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(0, 0, size, size), radius, radius)
    painter.setClipPath(clip)
    painter.drawPixmap(QRectF(0, 0, size, size), square, QRectF(0, 0, pixels, pixels))
    painter.end()
    return pixmap


def cover_or_placeholder(data: Optional[bytes], size: int, radius: float = 10) -> QPixmap:
    return cover_pixmap(data, size, radius) or placeholder_pixmap(size, radius)


# ---------- Componentes reutilizáveis ----------

class ElidedLabel(QLabel):
    """Rótulo de uma linha que corta o texto com reticências em vez de forçar largura."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full_text = ""
        self._tooltip_override = ""
        self.setTextFormat(Qt.PlainText)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 - assinatura do Qt
        self._full_text = text or ""
        self._apply_elision()

    def fullText(self) -> str:  # noqa: N802
        return self._full_text

    def set_tooltip_override(self, text: str) -> None:
        """Dica fixa (ex.: caminho completo); sem ela, a dica mostra o texto cortado."""
        self._tooltip_override = text
        self._apply_elision()

    def _apply_elision(self) -> None:
        width = self.contentsRect().width()
        if width > 0:
            shown = self.fontMetrics().elidedText(self._full_text, Qt.ElideRight, width)
        else:
            shown = self._full_text
        if shown != QLabel.text(self):
            QLabel.setText(self, shown)
        self.setToolTip(self._tooltip_override or (self._full_text if shown != self._full_text else ""))

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._apply_elision()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self._apply_elision()


class IconButton(QAbstractButton):
    """Botão com ícone desenhado em código: todos têm a mesma caixa, centro e estados."""

    def __init__(self, icon_name: str, tooltip: str = "", box: int = 36, icon_px: int = 20,
                 primary: bool = False, parent=None):
        super().__init__(parent)
        self._icon_name = icon_name
        self._icon_px = icon_px
        self._primary = primary
        self._active = False
        self.setToolTip(tooltip)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)  # evita que a barra de espaço acione o botão em foco
        self.setAttribute(Qt.WA_Hover, True)
        self.setFixedSize(box, box)

    def set_icon_name(self, name: str) -> None:
        if name != self._icon_name:
            self._icon_name = name
            self.update()

    def set_active(self, active: bool) -> None:
        if active != self._active:
            self._active = active
            self.update()

    def enterEvent(self, event):  # noqa: N802
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        enabled = self.isEnabled()
        hover = enabled and self.underMouse()
        down = enabled and self.isDown()
        rect = QRectF(self.rect())
        if self._primary:
            if not enabled:
                background = qcolor("elevated")
            elif down:
                background = qcolor("accentPress")
            elif hover:
                background = qcolor("accentHover")
            else:
                background = qcolor("accent")
            painter.setPen(Qt.NoPen)
            painter.setBrush(background)
            painter.drawEllipse(rect.adjusted(1, 1, -1, -1))
            color = qcolor("onAccent") if enabled else qcolor("text3")
        else:
            if hover:
                painter.setPen(Qt.NoPen)
                painter.setBrush(qcolor("elevated"))
                painter.drawEllipse(rect.adjusted(1, 1, -1, -1))
            if not enabled:
                color = qcolor("text3", 140)
            elif self._active:
                color = qcolor("accentHover") if hover else qcolor("accent")
            else:
                color = qcolor("text") if hover else qcolor("text2")
        size = float(self._icon_px)
        draw_icon(
            painter, self._icon_name,
            QRectF((rect.width() - size) / 2, (rect.height() - size) / 2, size, size),
            color,
        )


class NavButton(QPushButton):
    """Item de navegação da barra lateral, com ícone que acompanha hover e seleção."""

    def __init__(self, icon_name: str, text: str, tooltip: str = "", parent=None):
        super().__init__("  " + text, parent)
        self._icon_name = icon_name
        self._active = False
        self._hover = False
        self.setObjectName("navButton")
        self.setProperty("active", False)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setIconSize(QSize(20, 20))
        self.setToolTip(tooltip)
        self._refresh_icon()

    def set_active(self, active: bool) -> None:
        self._active = active
        self.setProperty("active", active)
        self.style().unpolish(self)
        self.style().polish(self)
        self._refresh_icon()

    def enterEvent(self, event):  # noqa: N802
        self._hover = True
        self._refresh_icon()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self._hover = False
        self._refresh_icon()
        super().leaveEvent(event)

    def _refresh_icon(self) -> None:
        if self._active:
            color = qcolor("accent")
        elif self._hover:
            color = qcolor("text")
        else:
            color = qcolor("text2")
        self.setIcon(make_icon(self._icon_name, color, 20))


class ClickableSlider(QSlider):
    """Slider em que clicar na trilha salta direto para a posição clicada."""

    HANDLE = 12

    def _value_at(self, event) -> int:
        span = max(1, self.width() - self.HANDLE)
        x = int(event.position().x() - self.HANDLE / 2)
        x = max(0, min(span, x))
        return QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), x, span)

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton and self.isEnabled():
            self.setSliderDown(True)
            self.setValue(self._value_at(event))
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802
        if self.isSliderDown():
            self.setValue(self._value_at(event))
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton and self.isSliderDown():
            self.setValue(self._value_at(event))
            self.setSliderDown(False)
            event.accept()
        else:
            super().mouseReleaseEvent(event)


class EmptyState(QWidget):
    """Estado vazio: ícone, título, descrição e, opcionalmente, uma ação principal."""

    def __init__(self, icon_name: str, title: str, text: str, button_text: Optional[str] = None,
                 button_icon: Optional[str] = None, on_click: Optional[Callable] = None, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(10)
        badge = QLabel()
        badge.setPixmap(badge_pixmap(icon_name, 88))
        badge.setAlignment(Qt.AlignCenter)
        layout.addWidget(badge, 0, Qt.AlignHCenter)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("emptyTitle")
        self.title_label.setTextFormat(Qt.PlainText)
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setContentsMargins(0, 8, 0, 0)
        layout.addWidget(self.title_label, 0, Qt.AlignHCenter)
        self.text_label = QLabel(text)
        self.text_label.setObjectName("emptyText")
        self.text_label.setTextFormat(Qt.PlainText)
        self.text_label.setAlignment(Qt.AlignCenter)
        self.text_label.setWordWrap(True)
        self.text_label.setFixedWidth(400)
        layout.addWidget(self.text_label, 0, Qt.AlignHCenter)
        if button_text and on_click:
            layout.addSpacing(10)
            button = QPushButton(button_text)
            button.setObjectName("primaryButton")
            button.setFixedHeight(42)
            button.setCursor(Qt.PointingHandCursor)
            if button_icon:
                button.setIcon(make_icon(button_icon, PALETTE["onAccent"], 18))
            button.clicked.connect(on_click)
            layout.addWidget(button, 0, Qt.AlignHCenter)

    def set_content(self, title: str, text: str) -> None:
        self.title_label.setText(title)
        self.text_label.setText(text)


class ToastLabel(QLabel):
    """Aviso flutuante e discreto; clique para dispensar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("toast")
        self.setTextFormat(Qt.PlainText)
        self.setWordWrap(True)
        self.setContentsMargins(16, 10, 16, 10)
        self.setCursor(Qt.PointingHandCursor)
        self.hide()

    def mousePressEvent(self, event):  # noqa: N802
        self.hide()


class AlbumCard(QFrame):
    COVER_SOURCE = 256
    PADDING = 12

    def __init__(self, album: dict, on_click: Callable, parent=None):
        super().__init__(parent)
        self.album = album
        self.on_click = on_click
        self.setObjectName("albumCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(f"{album['name']} — {album['artist']}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(self.PADDING, self.PADDING, self.PADDING, self.PADDING + 2)
        layout.setSpacing(3)

        self.cover = QLabel()
        self.cover.setScaledContents(True)
        self.cover.setPixmap(cover_or_placeholder(album.get("cover"), self.COVER_SOURCE, 18))
        layout.addWidget(self.cover, 0, Qt.AlignHCenter)
        layout.addSpacing(8)

        name = ElidedLabel(album["name"])
        name.setObjectName("cardTitle")
        layout.addWidget(name)
        artist = ElidedLabel(album["artist"])
        artist.setObjectName("cardSub")
        layout.addWidget(artist)
        count = len(album["tracks"])
        meta = f"{count} faixa" if count == 1 else f"{count} faixas"
        if album.get("year"):
            meta = f"{album['year']}  ·  {meta}"
        info = ElidedLabel(meta)
        info.setObjectName("cardMeta")
        layout.addWidget(info)
        self.set_card_width(188)

    def set_card_width(self, width: int) -> None:
        self.setFixedWidth(width)
        side = max(80, width - 2 * self.PADDING)
        self.cover.setFixedSize(side, side)

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.on_click(self.album)
        super().mousePressEvent(event)


class MetadataDialog(QDialog):
    def __init__(self, track: Track, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Editar metadados")
        self.setMinimumWidth(580)
        self.setModal(True)
        self.cover_path: Optional[str] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(26, 24, 26, 22)
        root.setSpacing(18)

        head = QHBoxLayout()
        head.setSpacing(18)
        self.cover_preview = QLabel()
        self.cover_preview.setFixedSize(96, 96)
        self.cover_preview.setPixmap(cover_or_placeholder(track.cover, 96, 12))
        head.addWidget(self.cover_preview, 0, Qt.AlignTop)

        head_text = QVBoxLayout()
        head_text.setSpacing(3)
        eyebrow = QLabel("EDITAR METADADOS")
        eyebrow.setObjectName("eyebrowAccent")
        title = ElidedLabel(track.title)
        title.setObjectName("dialogTitle")
        file_label = ElidedLabel(Path(track.path).name)
        file_label.setObjectName("subtle")
        file_label.set_tooltip_override(track.path)
        self.cover_label = ElidedLabel(
            "Capa incorporada ao arquivo" if track.cover else "Sem capa incorporada"
        )
        self.cover_label.setObjectName("subtle")
        choose_cover = QPushButton("Escolher nova capa…")
        choose_cover.setFixedHeight(34)
        choose_cover.setCursor(Qt.PointingHandCursor)
        choose_cover.setToolTip("Escolha uma imagem JPG, PNG ou WebP para incorporar ao arquivo")
        choose_cover.clicked.connect(self.choose_cover)
        head_text.addWidget(eyebrow)
        head_text.addWidget(title)
        head_text.addWidget(file_label)
        head_text.addWidget(self.cover_label)
        head_text.addSpacing(6)
        head_text.addWidget(choose_cover, 0, Qt.AlignLeft)
        head.addLayout(head_text, 1)
        root.addLayout(head)

        separator = QFrame()
        separator.setObjectName("separator")
        root.addWidget(separator)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)
        self.fields: dict[str, QLineEdit] = {}

        def add_field(key: str, label: str, value: str, row: int, col: int, span: int) -> None:
            box = QVBoxLayout()
            box.setSpacing(6)
            caption = QLabel(label)
            caption.setObjectName("fieldLabel")
            edit = QLineEdit(value)
            edit.setPlaceholderText(label)
            self.fields[key] = edit
            box.addWidget(caption)
            box.addWidget(edit)
            grid.addLayout(box, row, col, 1, span)

        add_field("title", "Título", track.title, 0, 0, 4)
        add_field("artist", "Artista", track.artist, 1, 0, 2)
        add_field("albumartist", "Artista do álbum", track.album_artist, 1, 2, 2)
        add_field("album", "Álbum", track.album if track.album != "Álbum desconhecido" else "", 2, 0, 4)
        add_field("date", "Ano / data", track.year, 3, 0, 1)
        add_field("tracknumber", "Faixa nº", track.track_number, 3, 1, 1)
        add_field("discnumber", "Disco nº", track.disc_number, 3, 2, 1)
        add_field("genre", "Gênero", track.genre, 3, 3, 1)
        for column in range(4):
            grid.setColumnStretch(column, 1)
        root.addLayout(grid)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        save_button.setText("Salvar alterações")
        save_button.setObjectName("primaryButton")
        save_button.setDefault(True)
        cancel_button.setText("Cancelar")
        for button in (save_button, cancel_button):
            button.setFixedHeight(40)
            button.setCursor(Qt.PointingHandCursor)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def choose_cover(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Escolher imagem da capa", "", "Imagens (*.jpg *.jpeg *.png *.webp)"
        )
        if not path:
            return
        try:
            data = Path(path).read_bytes()
        except OSError as exc:
            QMessageBox.warning(self, "Não foi possível abrir a imagem", f"{Path(path).name}: {exc}")
            return
        preview = cover_pixmap(data, 96, 12)
        if preview is None:
            QMessageBox.warning(
                self, "Imagem inválida",
                f"“{Path(path).name}” não pôde ser lida como imagem. Escolha um arquivo JPG, PNG ou WebP válido.",
            )
            return
        self.cover_path = path
        self.cover_preview.setPixmap(preview)
        self.cover_label.setText(f"Nova capa: {Path(path).name}")

    def values(self) -> dict[str, str]:
        return {key: widget.text().strip() for key, widget in self.fields.items()}


ALBUM_MIN_WIDTH = 176
ALBUM_MAX_WIDTH = 232
GRID_GAP = 16
SIDEBAR_WIDTH = 228
DEFAULT_VOLUME = 72


class SonoraWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SONORA — Music Library")
        self.setWindowIcon(QIcon(brand_pixmap(64)))
        self.setMinimumSize(900, 620)
        self.resize(1360, 860)
        self.settings = QSettings("SonoraStudio", "SonoraPlayer")
        self.library_path = ""
        self.all_tracks: list[Track] = []
        self.albums: list[dict] = []
        self.current_album: Optional[dict] = None
        self.current_track: Optional[Track] = None
        self.play_queue: list[Track] = []
        self.queue_index = -1
        self.repeat_mode = 0  # 0 = desligado, 1 = repetir fila, 2 = repetir faixa
        self._updating_seek = False
        self._last_volume = DEFAULT_VOLUME
        self.album_cards: list[AlbumCard] = []
        self._album_layout_key: Optional[tuple[int, int]] = None
        self._album_stretch_row: Optional[int] = None

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.audio_output.setVolume(DEFAULT_VOLUME / 100)
        self.player.positionChanged.connect(self.update_position)
        self.player.durationChanged.connect(self.update_duration)
        self.player.playbackStateChanged.connect(self.update_play_button)
        self.player.mediaStatusChanged.connect(self.handle_media_status)
        self.player.errorOccurred.connect(self.handle_player_error)

        self._build_ui()
        self._add_shortcuts()
        self._set_volume_label(DEFAULT_VOLUME)
        self._update_transport_state()
        saved_folder = self.settings.value("library_folder", "")
        if saved_folder:
            if Path(saved_folder).is_dir():
                self.load_library(saved_folder)
            else:
                missing = f"A última pasta usada não está mais disponível: {saved_folder}"
                QTimer.singleShot(0, lambda: self.notify(missing, error=True, timeout=9000))

    # ----- Interface -----
    def _build_ui(self):
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        outer.addLayout(body, 1)
        body.addWidget(self._build_sidebar())

        content = QWidget()
        main = QVBoxLayout(content)
        main.setContentsMargins(32, 26, 32, 0)
        main.setSpacing(18)
        body.addWidget(content, 1)
        main.addLayout(self._build_header())

        self.pages = QStackedWidget()
        main.addWidget(self.pages, 1)
        self.empty_page = self._make_empty_page()
        self.tracks_page, self.track_table = self._make_tracks_page()
        self.albums_page = self._make_albums_page()
        self.album_detail_page = self._make_album_detail_page()
        for page in (self.empty_page, self.tracks_page, self.albums_page, self.album_detail_page):
            self.pages.addWidget(page)

        self._build_player_bar(outer)
        self.toast = ToastLabel(root)
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self.toast.hide)
        self.pages.setCurrentWidget(self.empty_page)
        self._update_nav("tracks")
        self.page_subtitle.setText("Escolha uma pasta para começar")

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(SIDEBAR_WIDTH)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(18, 24, 18, 18)
        side.setSpacing(6)

        brand_row = QHBoxLayout()
        brand_row.setSpacing(12)
        mark = QLabel()
        mark.setPixmap(brand_pixmap(38))
        mark.setFixedSize(38, 38)
        brand_row.addWidget(mark)
        brand_text = QVBoxLayout()
        brand_text.setSpacing(0)
        brand = QLabel("SONORA")
        brand.setObjectName("brand")
        tagline = QLabel("Your music, your space")
        tagline.setObjectName("tagline")
        brand_text.addWidget(brand)
        brand_text.addWidget(tagline)
        brand_row.addLayout(brand_text, 1)
        side.addLayout(brand_row)
        side.addSpacing(26)

        section = QLabel("BIBLIOTECA")
        section.setObjectName("eyebrow")
        section.setContentsMargins(14, 0, 0, 4)
        side.addWidget(section)
        self.nav_tracks = NavButton("list", "Faixas", "Todas as faixas da biblioteca")
        self.nav_albums = NavButton("albums", "Álbuns", "Coleção agrupada por álbum")
        self.nav_tracks.clicked.connect(lambda: self.show_page("tracks"))
        self.nav_albums.clicked.connect(lambda: self.show_page("albums"))
        side.addWidget(self.nav_tracks)
        side.addWidget(self.nav_albums)

        side.addSpacing(20)
        collection = QLabel("SUA COLEÇÃO")
        collection.setObjectName("eyebrow")
        collection.setContentsMargins(14, 0, 0, 4)
        side.addWidget(collection)
        self.folder_button = QPushButton("Abrir pasta")
        self.folder_button.setIcon(make_icon("folder", PALETTE["text"], 18))
        self.folder_button.setFixedHeight(42)
        self.folder_button.setCursor(Qt.PointingHandCursor)
        self.folder_button.setFocusPolicy(Qt.NoFocus)
        self.folder_button.setToolTip("Escolher a pasta de músicas (Ctrl+O)")
        self.folder_button.clicked.connect(self.choose_folder)
        side.addWidget(self.folder_button)
        side.addStretch(1)

        card = QFrame()
        card.setObjectName("folderCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(2)
        self.folder_name = ElidedLabel("Nenhuma pasta selecionada")
        self.folder_name.setObjectName("folderName")
        self.folder_info = ElidedLabel("Use “Abrir pasta” para importar")
        self.folder_info.setObjectName("subtle")
        card_layout.addWidget(self.folder_name)
        card_layout.addWidget(self.folder_info)
        side.addWidget(card)
        version = QLabel("SONORA  ·  DESKTOP 1.0")
        version.setObjectName("subtle")
        version.setContentsMargins(4, 6, 0, 0)
        side.addWidget(version)
        return sidebar

    def _build_header(self) -> QHBoxLayout:
        top = QHBoxLayout()
        top.setSpacing(12)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        self.page_title = ElidedLabel("Faixas")
        self.page_title.setObjectName("pageTitle")
        self.page_subtitle = ElidedLabel("")
        self.page_subtitle.setObjectName("pageSubtitle")
        title_box.addWidget(self.page_title)
        title_box.addWidget(self.page_subtitle)
        top.addLayout(title_box, 2)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar faixas, artistas, álbuns…")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(make_icon("search", PALETTE["text3"], 18), QLineEdit.LeadingPosition)
        self.search.setFixedHeight(40)
        self.search.setMinimumWidth(190)
        self.search.setMaximumWidth(340)
        self.search.setToolTip("Buscar na biblioteca (Ctrl+F)")
        self.search.textChanged.connect(self.filter_content)
        top.addWidget(self.search, 1, Qt.AlignVCenter)

        return top

    def _make_empty_page(self) -> QWidget:
        return EmptyState(
            "note", "Sua música começa aqui",
            "Selecione uma pasta para importar sua coleção. O SONORA organiza as faixas "
            "automaticamente pelos metadados.",
            "Selecionar pasta de músicas", "folder", self.choose_folder,
        )

    def _new_track_table(self) -> QTableWidget:
        table = QTableWidget(0, 5)
        table.setHorizontalHeaderLabels(["TÍTULO", "ARTISTA", "ÁLBUM", "DURAÇÃO", ""])
        table.verticalHeader().setVisible(False)
        table.setShowGrid(False)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setFocusPolicy(Qt.NoFocus)
        table.setContextMenuPolicy(Qt.CustomContextMenu)
        table.setTextElideMode(Qt.ElideRight)
        table.setWordWrap(False)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        table.setMouseTracking(True)
        table.viewport().setMouseTracking(True)
        table.viewport().installEventFilter(self)
        header = table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setSectionsClickable(False)
        header.setHighlightSections(False)
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(80)
        for column in (0, 1, 2):
            header.setSectionResizeMode(column, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.Fixed)
        header.setSectionResizeMode(4, QHeaderView.Fixed)
        table.setColumnWidth(3, 96)
        table.setColumnWidth(4, 40)
        table.horizontalHeaderItem(3).setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        table.verticalHeader().setDefaultSectionSize(48)
        table.tracks = []
        table.edit_buttons = []
        table.cellEntered.connect(lambda row, _column, t=table: self._show_row_edit_button(t, row))
        table.cellDoubleClicked.connect(lambda row, _col, t=table: self.play_table_row(t, row))
        table.customContextMenuRequested.connect(lambda pos, t=table: self.show_track_context_menu(t, pos))
        return table

    def _show_row_edit_button(self, table: QTableWidget, row: int):
        for index, button in enumerate(table.edit_buttons):
            button.setVisible(index == row)

    def _hide_row_edit_buttons(self, table: QTableWidget):
        for button in table.edit_buttons:
            button.hide()

    def _sync_hovered_edit_button(self, table: QTableWidget):
        point = table.viewport().mapFromGlobal(QCursor.pos())
        row = table.rowAt(point.y()) if table.viewport().rect().contains(point) else -1
        if row >= 0:
            self._show_row_edit_button(table, row)
        else:
            self._hide_row_edit_buttons(table)

    def _edit_table_row(self, table: QTableWidget, row: int):
        tracks = getattr(table, "tracks", [])
        if 0 <= row < len(tracks):
            table.selectRow(row)
            self.edit_track(tracks[row])

    def _make_tracks_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.track_count_label = QLabel("0 faixas")
        self.track_count_label.setObjectName("countLabel")
        layout.addWidget(self.track_count_label)
        table = self._new_track_table()
        layout.addWidget(table, 1)
        self.tracks_empty = EmptyState("search", "Nenhum resultado", "")
        self.tracks_empty.hide()
        layout.addWidget(self.tracks_empty, 1)
        return page, table

    def _make_albums_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.album_count_label = QLabel("0 álbuns")
        self.album_count_label.setObjectName("countLabel")
        layout.addWidget(self.album_count_label)
        scroll = QScrollArea()
        scroll.viewport().setObjectName("albumViewport")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # Barra sempre visível: a largura útil fica estável e o reflow não oscila.
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        host = QWidget()
        host.setObjectName("albumGridHost")
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 4, 0, 16)
        grid.setHorizontalSpacing(GRID_GAP)
        grid.setVerticalSpacing(GRID_GAP)
        scroll.setWidget(host)
        layout.addWidget(scroll, 1)
        self.albums_empty = EmptyState("search", "Nenhum álbum encontrado", "")
        self.albums_empty.hide()
        layout.addWidget(self.albums_empty, 1)
        self.albums_scroll = scroll
        self.album_grid = grid
        self.album_grid_host = host
        scroll.viewport().installEventFilter(self)
        return page

    def _make_album_detail_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        back = QPushButton("Álbuns")
        back.setObjectName("ghostButton")
        back.setIcon(make_icon("back", PALETTE["text2"], 18))
        back.setCursor(Qt.PointingHandCursor)
        back.setFocusPolicy(Qt.NoFocus)
        back.setToolTip("Voltar para a lista de álbuns")
        back.clicked.connect(lambda: self.show_page("albums"))
        layout.addWidget(back, 0, Qt.AlignLeft)

        hero = QHBoxLayout()
        hero.setSpacing(24)
        self.detail_cover = QLabel()
        self.detail_cover.setFixedSize(176, 176)
        self.detail_cover.setPixmap(placeholder_pixmap(176, 14))
        hero.addWidget(self.detail_cover, 0, Qt.AlignTop)
        info = QVBoxLayout()
        info.setSpacing(4)
        info.setAlignment(Qt.AlignVCenter)
        self.detail_type = QLabel("ÁLBUM")
        self.detail_type.setObjectName("eyebrowAccent")
        self.detail_name = QLabel("")
        self.detail_name.setObjectName("detailTitle")
        self.detail_name.setTextFormat(Qt.PlainText)
        self.detail_name.setWordWrap(True)
        self.detail_artist = ElidedLabel("")
        self.detail_artist.setObjectName("detailArtist")
        self.detail_meta = ElidedLabel("")
        self.detail_meta.setObjectName("detailMeta")
        play_album = QPushButton("Reproduzir álbum")
        play_album.setObjectName("primaryButton")
        play_album.setIcon(make_icon("play", PALETTE["onAccent"], 18))
        play_album.setFixedHeight(42)
        play_album.setCursor(Qt.PointingHandCursor)
        play_album.setFocusPolicy(Qt.NoFocus)
        play_album.setToolTip("Tocar todas as faixas do álbum, em ordem")
        play_album.clicked.connect(self.play_current_album)
        info.addStretch(1)
        info.addWidget(self.detail_type)
        info.addWidget(self.detail_name)
        info.addWidget(self.detail_artist)
        info.addWidget(self.detail_meta)
        info.addSpacing(12)
        info.addWidget(play_album, 0, Qt.AlignLeft)
        info.addStretch(1)
        hero.addLayout(info, 1)
        layout.addLayout(hero)
        layout.addSpacing(6)
        self.detail_table = self._new_track_table()
        layout.addWidget(self.detail_table, 1)
        return page

    def _build_player_bar(self, outer: QVBoxLayout):
        bar = QFrame()
        bar.setObjectName("playerBar")
        bar.setFixedHeight(92)
        self.player_bar = bar
        row = QHBoxLayout(bar)
        row.setContentsMargins(20, 10, 20, 10)
        row.setSpacing(20)

        # Esquerda: capa + faixa atual
        left = QWidget()
        left_row = QHBoxLayout(left)
        left_row.setContentsMargins(0, 0, 0, 0)
        left_row.setSpacing(12)
        self.player_cover = QLabel()
        self.player_cover.setFixedSize(56, 56)
        self.player_cover.setPixmap(placeholder_pixmap(56, 8))
        left_row.addWidget(self.player_cover)
        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        self.now_title = ElidedLabel("Nada tocando")
        self.now_title.setObjectName("nowTitle")
        self.now_artist = ElidedLabel("Escolha uma faixa da biblioteca")
        self.now_artist.setObjectName("nowArtist")
        text_col.addStretch(1)
        text_col.addWidget(self.now_title)
        text_col.addWidget(self.now_artist)
        text_col.addStretch(1)
        left_row.addLayout(text_col, 1)
        left.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        row.addWidget(left, 1)

        # Centro: controles e progresso
        center = QWidget()
        center.setMaximumWidth(680)
        controls = QVBoxLayout(center)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(4)
        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.previous_button = IconButton("prev", "Faixa anterior (volta ao início se já passou de 3 s)", 36, 20)
        self.play_button = IconButton("play", "Reproduzir (Espaço)", 44, 22, primary=True)
        self.next_button = IconButton("next", "Próxima faixa", 36, 20)
        self.repeat_button = IconButton("repeat", "Repetição: desligada", 36, 20)
        self.previous_button.clicked.connect(self.previous_track)
        self.play_button.clicked.connect(self.toggle_playback)
        self.next_button.clicked.connect(self.next_track)
        self.repeat_button.clicked.connect(self.cycle_repeat)
        buttons.addStretch(1)
        buttons.addSpacing(46)  # equilibra o botão de repetição e centraliza o Play
        for button in (self.previous_button, self.play_button, self.next_button):
            buttons.addWidget(button, 0, Qt.AlignVCenter)
        buttons.addSpacing(10)
        buttons.addWidget(self.repeat_button, 0, Qt.AlignVCenter)
        buttons.addStretch(1)
        controls.addLayout(buttons)

        seek_row = QHBoxLayout()
        seek_row.setSpacing(10)
        self.position_label = QLabel("00:00")
        self.position_label.setObjectName("timeLabel")
        self.position_label.setFixedWidth(44)
        self.position_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.seek_slider = ClickableSlider(Qt.Horizontal)
        self.seek_slider.setRange(0, 0)
        self.seek_slider.setCursor(Qt.PointingHandCursor)
        self.seek_slider.setToolTip("Posição da faixa")
        self.seek_slider.sliderPressed.connect(self._seek_pressed)
        self.seek_slider.sliderReleased.connect(self._seek_released)
        self.duration_label = QLabel("00:00")
        self.duration_label.setObjectName("timeLabel")
        self.duration_label.setFixedWidth(44)
        self.duration_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        seek_row.addWidget(self.position_label)
        seek_row.addWidget(self.seek_slider, 1)
        seek_row.addWidget(self.duration_label)
        controls.addLayout(seek_row)
        row.addWidget(center, 2)

        # Direita: volume
        right = QWidget()
        right_row = QHBoxLayout(right)
        right_row.setContentsMargins(0, 0, 0, 0)
        right_row.setSpacing(8)
        self.volume_button = IconButton("volume-high", "Silenciar", 32, 20)
        self.volume_button.clicked.connect(self.toggle_mute)
        self.volume_slider = ClickableSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(DEFAULT_VOLUME)
        self.volume_slider.setFixedWidth(104)
        self.volume_slider.setCursor(Qt.PointingHandCursor)
        self.volume_slider.valueChanged.connect(self.change_volume)
        self.volume_label = QLabel("72%")
        self.volume_label.setObjectName("volumeLabel")
        self.volume_label.setFixedWidth(34)
        self.volume_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        right_row.addStretch(1)
        right_row.addWidget(self.volume_button, 0, Qt.AlignVCenter)
        right_row.addWidget(self.volume_slider, 0, Qt.AlignVCenter)
        right_row.addWidget(self.volume_label, 0, Qt.AlignVCenter)
        right.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        row.addWidget(right, 1)
        outer.addWidget(bar)

    def _add_shortcuts(self):
        for sequence, handler in (
            ("Ctrl+O", self.choose_folder),
            ("Ctrl+E", self.edit_selected_track),
            ("Space", self.toggle_playback),
            ("Ctrl+F", self.focus_search),
        ):
            action = QAction(self)
            action.setShortcut(QKeySequence(sequence))
            action.triggered.connect(handler)
            self.addAction(action)

    def focus_search(self):
        self.search.setFocus()
        self.search.selectAll()

    # ----- Avisos e diálogos -----
    def notify(self, text: str, error: bool = False, timeout: int = 6000):
        """Aviso temporário sobre a barra do player; erros ganham destaque."""
        self.toast.setProperty("kind", "error" if error else "info")
        self.toast.style().unpolish(self.toast)
        self.toast.style().polish(self.toast)
        self.toast.setText(text)
        self.toast.show()
        self._place_toast()
        self.toast.raise_()
        self._toast_timer.start(timeout)

    def _place_toast(self):
        if not self.toast.isVisible():
            return
        root = self.centralWidget()
        width = max(240, min(560, root.width() - 64))
        self.toast.setFixedWidth(width)
        self.toast.setFixedHeight(self.toast.heightForWidth(width))
        x = (root.width() - width) // 2
        y = root.height() - self.player_bar.height() - self.toast.height() - 16
        self.toast.move(max(0, x), max(0, y))

    def show_message(self, kind: str, title: str, text: str, details: Optional[str] = None):
        icons = {
            "info": QMessageBox.Information,
            "warning": QMessageBox.Warning,
            "error": QMessageBox.Critical,
        }
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(icons.get(kind, QMessageBox.Information))
        box.setTextFormat(Qt.PlainText)
        box.setText(text)
        if details:
            box.setDetailedText(details)
        box.setStandardButtons(QMessageBox.Ok)
        box.exec()

    # ----- Biblioteca -----
    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Selecionar pasta de músicas", self.library_path or str(Path.home()))
        if folder:
            self.load_library(folder)

    def load_library(self, folder: str):
        self.library_path = folder
        self.settings.setValue("library_folder", folder)
        self.folder_name.setText(Path(folder).name or folder)
        self.folder_info.setText(folder)
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        walk_errors: list[str] = []
        unreadable: list[str] = []
        found: list[Track] = []
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for root, _dirs, files in os.walk(folder, onerror=lambda exc: walk_errors.append(str(exc))):
                for filename in files:
                    if Path(filename).suffix.lower() not in AUDIO_EXTENSIONS:
                        continue
                    path = os.path.join(root, filename)
                    track = read_track(path)
                    if track:
                        found.append(track)
                    else:
                        unreadable.append(path)
            found.sort(key=self.track_sort_key)
            self.all_tracks = found
            self._group_albums()
            self._populate_tracks(self.track_table, found)
            self._populate_albums()
            self._set_tracks_empty(False, "")
            self.show_page("tracks")
        finally:
            QApplication.restoreOverrideCursor()
        self._update_transport_state()
        # Adiado para depois do evento atual: no arranque a janela ainda nem foi exibida.
        QTimer.singleShot(0, lambda: self._report_load_problems(folder, found, unreadable, walk_errors))

    def _report_load_problems(self, folder: str, found: list[Track], unreadable: list[str], walk_errors: list[str]):
        problems = [f"Não foi possível ler: {path}" for path in unreadable]
        problems += [f"Pasta inacessível: {error}" for error in walk_errors]
        if not problems:
            if not found:
                self.show_message(
                    "info", "Nenhuma música encontrada",
                    "Não encontrei arquivos de áudio compatíveis nesta pasta ou em suas subpastas.",
                )
            return
        shown = problems[:60]
        if len(problems) > len(shown):
            shown.append(f"… e mais {len(problems) - len(shown)} item(ns).")
        if found:
            summary = (
                f"{len(found)} faixa(s) foram importadas, mas {len(problems)} item(ns) "
                "não puderam ser lidos (arquivo corrompido, sem permissão ou formato sem metadados)."
            )
        else:
            summary = (
                f"Nenhuma faixa pôde ser importada de “{folder}”. "
                f"{len(problems)} item(ns) apresentaram erro."
            )
        self.notify(f"{len(problems)} item(ns) não puderam ser lidos.", error=True)
        self.show_message("warning", "Importação concluída com avisos", summary, "\n".join(shown))

    @staticmethod
    def track_sort_key(track: Track):
        def number(value: str):
            try:
                return int(value.split("/")[0])
            except (ValueError, AttributeError):
                return 0
        return (
            track.album_artist.casefold(), track.album.casefold(),
            number(track.disc_number), number(track.track_number), track.title.casefold()
        )

    def _group_albums(self):
        grouped: dict[tuple[str, str], list[Track]] = {}
        for track in self.all_tracks:
            key = (track.album_artist.casefold(), track.display_album.casefold())
            grouped.setdefault(key, []).append(track)
        self.albums = []
        for (_artist_key, _album_key), tracks in grouped.items():
            tracks.sort(key=self.track_sort_key)
            cover = next((t.cover for t in tracks if t.cover), None)
            year = next((t.year[:4] for t in tracks if t.year), "")
            self.albums.append({
                "name": tracks[0].display_album,
                "artist": tracks[0].album_artist or tracks[0].display_artist,
                "tracks": tracks,
                "cover": cover,
                "year": year,
            })
        self.albums.sort(key=lambda a: (a["artist"].casefold(), a["name"].casefold()))

    def _populate_tracks(self, table: QTableWidget, tracks: list[Track]):
        table.setUpdatesEnabled(False)
        try:
            table.clearContents()
            table.setRowCount(len(tracks))
            table.tracks = tracks
            table.edit_buttons = []
            for row, track in enumerate(tracks):
                title_item = QTableWidgetItem(track.title)
                title_item.setData(Qt.UserRole, track.path)
                title_item.setToolTip(track.path)
                artist_item = QTableWidgetItem(track.display_artist)
                artist_item.setToolTip(track.display_artist)
                album_item = QTableWidgetItem(track.display_album)
                album_item.setToolTip(track.display_album)
                duration_item = QTableWidgetItem(format_time(track.duration))
                duration_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                duration_item.setForeground(QBrush(qcolor("text2")))
                table.setItem(row, 0, title_item)
                table.setItem(row, 1, artist_item)
                table.setItem(row, 2, album_item)
                table.setItem(row, 3, duration_item)
                edit_button = QPushButton()
                edit_button.setObjectName("rowEditButton")
                edit_button.setIcon(make_icon("edit", PALETTE["text"], 16))
                edit_button.setCursor(Qt.PointingHandCursor)
                edit_button.setFocusPolicy(Qt.NoFocus)
                edit_button.setToolTip(f"Editar metadados de {track.title}")
                edit_button.clicked.connect(lambda _checked=False, t=table, r=row: self._edit_table_row(t, r))
                table.setCellWidget(row, 4, edit_button)
                edit_button.hide()
                table.edit_buttons.append(edit_button)
            self._mark_playing(table)
        finally:
            table.setUpdatesEnabled(True)

    def _mark_playing(self, table: Optional[QTableWidget] = None):
        """Destaca em cor de acento a faixa que está tocando."""
        path = self.current_track.path if self.current_track else None
        tables = (table,) if table is not None else (self.track_table, self.detail_table)
        for current in tables:
            for row, track in enumerate(getattr(current, "tracks", [])):
                item = current.item(row, 0)
                if item is None:
                    continue
                playing = track.path == path
                item.setForeground(QBrush(qcolor("accent" if playing else "text")))
                font = item.font()
                font.setBold(playing)
                item.setFont(font)

    def _populate_albums(self):
        for card in self.album_cards:
            self.album_grid.removeWidget(card)
            card.hide()
            card.deleteLater()
        self.album_cards = []
        query = self.search.text().strip().casefold()
        visible = [a for a in self.albums if not query or query in a["name"].casefold() or query in a["artist"].casefold()]
        for album in visible:
            card = AlbumCard(album, self.open_album, self.album_grid_host)
            card.hide()  # só aparece quando _layout_albums o posiciona na grade
            self.album_cards.append(card)
        total = len(self.albums)
        if query:
            self.album_count_label.setText(f"{len(visible)} de {total} álbuns")
        else:
            self.album_count_label.setText(f"{total} álbum" if total == 1 else f"{total} álbuns")
        self.albums_scroll.setVisible(bool(visible))
        self.albums_empty.setVisible(not visible and bool(self.all_tracks))
        if not visible and self.all_tracks:
            self.albums_empty.set_content(
                "Nenhum álbum encontrado",
                f"Nada encontrado para “{self.search.text().strip()}”. Tente outro nome de álbum ou artista.",
            )
        self._layout_albums(force=True)

    def _layout_albums(self, force: bool = False):
        """Distribui os cartões conforme a largura disponível, esticando-os para preencher a linha."""
        available = self.albums_scroll.viewport().width()
        if available <= 0:
            return
        columns = max(1, (available + GRID_GAP) // (ALBUM_MIN_WIDTH + GRID_GAP))
        card_width = (available - (columns - 1) * GRID_GAP) // columns
        card_width = max(120, min(ALBUM_MAX_WIDTH, card_width))
        key = (columns, card_width)
        if not force and key == self._album_layout_key:
            return
        self._album_layout_key = key
        while self.album_grid.count():
            self.album_grid.takeAt(0)
        for index, card in enumerate(self.album_cards):
            card.set_card_width(card_width)
            self.album_grid.addWidget(card, index // columns, index % columns, Qt.AlignTop | Qt.AlignLeft)
            card.show()
        if self._album_stretch_row is not None:
            self.album_grid.setRowStretch(self._album_stretch_row, 0)
        self._album_stretch_row = (len(self.album_cards) + columns - 1) // columns
        self.album_grid.setRowStretch(self._album_stretch_row, 1)

    def eventFilter(self, obj, event):  # noqa: N802
        scroll = getattr(self, "albums_scroll", None)
        if scroll is not None and obj is scroll.viewport() and event.type() == QEvent.Type.Resize:
            QTimer.singleShot(0, self._layout_albums)
        if event.type() in (QEvent.Type.Show, QEvent.Type.Leave):
            for table in (getattr(self, "track_table", None), getattr(self, "detail_table", None)):
                if table is not None and obj is table.viewport():
                    if event.type() == QEvent.Type.Show:
                        QTimer.singleShot(0, lambda t=table: self._sync_hovered_edit_button(t))
                    else:
                        self._hide_row_edit_buttons(table)
                    break
        return super().eventFilter(obj, event)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "toast"):
            self._place_toast()

    def _set_tracks_empty(self, show: bool, query: str):
        self.tracks_empty.setVisible(show)
        self.track_table.setVisible(not show)
        if show:
            self.tracks_empty.set_content(
                "Nenhum resultado",
                f"Nada encontrado para “{query}”. Tente buscar por título, artista, álbum ou gênero.",
            )

    def filter_content(self, text: str):
        query = text.strip().casefold()
        filtered = [t for t in self.all_tracks if not query or any(
            query in value.casefold() for value in (t.title, t.artist, t.album, t.album_artist, t.genre)
        )]
        self._populate_tracks(self.track_table, filtered)
        total = len(self.all_tracks)
        if query:
            self.track_count_label.setText(f"{len(filtered)} de {total} faixas")
        else:
            self.track_count_label.setText(f"{total} faixa" if total == 1 else f"{total} faixas")
        self._set_tracks_empty(not filtered and bool(self.all_tracks), text.strip())
        self._populate_albums()
        if self.current_album and self.pages.currentWidget() == self.album_detail_page:
            album_tracks = self.current_album["tracks"]
            filtered_album = [t for t in album_tracks if not query or any(
                query in value.casefold() for value in (t.title, t.artist, t.album, t.genre)
            )]
            self._populate_tracks(self.detail_table, filtered_album)

    def show_page(self, page: str):
        if page == "tracks":
            self.pages.setCurrentWidget(self.tracks_page if self.all_tracks else self.empty_page)
            self.page_title.setText("Faixas")
            self.page_subtitle.setText(
                f"{len(self.all_tracks)} músicas na biblioteca" if self.all_tracks else "Escolha uma pasta para começar"
            )
            total = len(self.all_tracks)
            if not self.search.text().strip():
                self.track_count_label.setText(f"{total} faixa" if total == 1 else f"{total} faixas")
        elif page == "albums":
            self.pages.setCurrentWidget(self.albums_page if self.all_tracks else self.empty_page)
            self.page_title.setText("Álbuns")
            self.page_subtitle.setText(
                "Sua coleção organizada por álbum e artista" if self.all_tracks else "Escolha uma pasta para começar"
            )
            self._populate_albums()
            QTimer.singleShot(0, self._layout_albums)
        self._update_nav(page)

    def _update_nav(self, active: str):
        self.nav_tracks.set_active(active == "tracks")
        self.nav_albums.set_active(active == "albums")

    def open_album(self, album: dict):
        self.current_album = album
        self.page_title.setText("Álbum")
        self.page_subtitle.setText(f"{album['name']} — {album['artist']}")
        self.detail_cover.setPixmap(cover_or_placeholder(album.get("cover"), 176, 14))
        self.detail_name.setText(album["name"])
        self.detail_artist.setText(album["artist"])
        duration = sum(t.duration for t in album["tracks"])
        count = len(album["tracks"])
        pieces = [f"{count} faixa" if count == 1 else f"{count} faixas", format_time(duration)]
        if album.get("year"):
            pieces.insert(0, album["year"])
        self.detail_meta.setText("  ·  ".join(pieces))
        query = self.search.text().strip().casefold()
        tracks = [t for t in album["tracks"] if not query or any(
            query in value.casefold() for value in (t.title, t.artist, t.album, t.genre)
        )]
        self._populate_tracks(self.detail_table, tracks)
        self.pages.setCurrentWidget(self.album_detail_page)
        self._update_nav("albums")

    # ----- Seleção, contexto e edição -----
    def play_table_row(self, table: QTableWidget, row: int):
        tracks = getattr(table, "tracks", [])
        if 0 <= row < len(tracks):
            self.play_track(tracks[row], tracks)

    def selected_track(self) -> Optional[Track]:
        current = self.pages.currentWidget()
        if current == self.album_detail_page:
            table = self.detail_table
        elif current == self.tracks_page:
            table = self.track_table
        else:
            return None
        row = table.currentRow()
        tracks = getattr(table, "tracks", [])
        return tracks[row] if 0 <= row < len(tracks) else None

    def show_track_context_menu(self, table: QTableWidget, pos):
        row = table.rowAt(pos.y())
        tracks = getattr(table, "tracks", [])
        if not 0 <= row < len(tracks):
            return
        table.selectRow(row)
        track = tracks[row]
        menu = QMenu(self)
        play = menu.addAction(make_icon("play", PALETTE["text"], 16), "Reproduzir")
        edit = menu.addAction(make_icon("edit", PALETTE["text"], 16), "Editar metadados")
        chosen = menu.exec(table.viewport().mapToGlobal(pos))
        if chosen == play:
            self.play_track(track, tracks)
        elif chosen == edit:
            self.edit_track(track)

    def edit_selected_track(self):
        track = self.selected_track()
        if track:
            self.edit_track(track)

    def edit_track(self, track: Track):
        dialog = MetadataDialog(track, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            save_tags(track.path, dialog.values(), dialog.cover_path)
        except Exception as exc:
            self.show_message("error", "Não foi possível salvar", str(exc))
            return
        was_detail = self.pages.currentWidget() == self.album_detail_page
        playing_path = self.current_track.path if self.current_track else None
        self.load_library(self.library_path)
        if playing_path:
            refreshed = next((t for t in self.all_tracks if t.path == playing_path), None)
            if refreshed:
                self.current_track = refreshed
                self._show_now_playing(refreshed)
                self._mark_playing()
        if was_detail:
            album = next((a for a in self.albums if any(t.path == track.path for t in a["tracks"])), None)
            if album:
                self.open_album(album)
        self.notify("Metadados gravados no arquivo de música.")

    # ----- Reprodução -----
    def play_track(self, track: Track, queue: Optional[list[Track]] = None):
        self.play_queue = list(queue) if queue else list(self.all_tracks)
        try:
            self.queue_index = next(i for i, item in enumerate(self.play_queue) if item.path == track.path)
        except StopIteration:
            self.play_queue = list(self.all_tracks)
            self.queue_index = next((i for i, item in enumerate(self.play_queue) if item.path == track.path), -1)
        self.current_track = track
        self.player.setSource(QUrl.fromLocalFile(track.path))
        self.player.play()
        self._show_now_playing(track)
        self._mark_playing()
        self._update_transport_state()

    def _show_now_playing(self, track: Track):
        self.now_title.setText(track.title)
        self.now_artist.setText(track.display_artist)
        self.player_cover.setPixmap(cover_or_placeholder(track.cover, 56, 8))

    def _update_transport_state(self):
        has_track = self.current_track is not None
        self.previous_button.setEnabled(has_track)
        self.next_button.setEnabled(has_track)
        self.seek_slider.setEnabled(has_track)
        self.play_button.setEnabled(has_track or bool(self.all_tracks))

    def toggle_playback(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        elif self.current_track:
            self.player.play()
        elif self.all_tracks:
            self.play_track(self.all_tracks[0], self.all_tracks)

    def update_play_button(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_button.set_icon_name("pause" if playing else "play")
        self.play_button.setToolTip("Pausar (Espaço)" if playing else "Reproduzir (Espaço)")

    def next_track(self):
        if not self.play_queue or self.queue_index < 0:
            return
        next_index = self.queue_index + 1
        if next_index >= len(self.play_queue):
            if self.repeat_mode == 1:
                next_index = 0
            else:
                return
        self.play_track(self.play_queue[next_index], self.play_queue)

    def previous_track(self):
        if self.player.position() > 3000:
            self.player.setPosition(0)
            return
        if not self.play_queue or self.queue_index < 0:
            return
        previous_index = self.queue_index - 1
        if previous_index < 0:
            if self.repeat_mode == 1:
                previous_index = len(self.play_queue) - 1
            else:
                self.player.setPosition(0)
                return
        self.play_track(self.play_queue[previous_index], self.play_queue)

    def play_current_album(self):
        if self.current_album and self.current_album["tracks"]:
            self.play_track(self.current_album["tracks"][0], self.current_album["tracks"])

    def cycle_repeat(self):
        self.repeat_mode = (self.repeat_mode + 1) % 3
        names = ["desligada", "repetir fila", "repetir faixa atual"]
        self.repeat_button.setToolTip(f"Repetição: {names[self.repeat_mode]} (clique para alternar)")
        self.repeat_button.set_active(self.repeat_mode != 0)
        self.repeat_button.set_icon_name("repeat-one" if self.repeat_mode == 2 else "repeat")

    def handle_media_status(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self.repeat_mode == 2 and self.current_track:
                self.player.setPosition(0)
                self.player.play()
            elif self.queue_index + 1 < len(self.play_queue):
                self.next_track()
            elif self.repeat_mode == 1 and self.play_queue:
                self.play_track(self.play_queue[0], self.play_queue)
            else:
                self.update_play_button(QMediaPlayer.PlaybackState.StoppedState)

    def handle_player_error(self, _error, error_string):
        if not error_string:
            return
        name = f"“{self.current_track.title}”: " if self.current_track else ""
        self.notify(f"Falha na reprodução de {name}{error_string}", error=True, timeout=9000)

    def _seek_pressed(self):
        self._updating_seek = True

    def _seek_released(self):
        self.player.setPosition(self.seek_slider.value())
        self._updating_seek = False

    def update_position(self, position: int):
        if not self._updating_seek:
            self.seek_slider.setValue(position)
        self.position_label.setText(format_time(position / 1000))

    def update_duration(self, duration: int):
        self.seek_slider.setRange(0, max(0, duration))
        self.duration_label.setText(format_time(duration / 1000))

    def change_volume(self, value: int):
        self.audio_output.setVolume(value / 100)
        if value > 0:
            self._last_volume = value
        self._set_volume_label(value)

    def toggle_mute(self):
        if self.volume_slider.value() > 0:
            self.volume_slider.setValue(0)
        else:
            self.volume_slider.setValue(self._last_volume or DEFAULT_VOLUME)

    def _set_volume_label(self, value: int):
        icon = "volume-mute" if value == 0 else "volume-low" if value <= 50 else "volume-high"
        self.volume_button.set_icon_name(icon)
        self.volume_button.setToolTip("Ativar som" if value == 0 else "Silenciar")
        self.volume_slider.setToolTip(f"Volume: {value}%")
        self.volume_label.setText(f"{value}%")


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("SonoraStudio")
    app.setStyle("Fusion")
    app.setPalette(build_palette())
    app.setStyleSheet(build_stylesheet())
    window = SonoraWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
