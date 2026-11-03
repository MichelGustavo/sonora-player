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

from PySide6.QtCore import Qt, QUrl, QSize, QSettings, Signal
from PySide6.QtGui import QAction, QKeySequence, QPixmap, QImage
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
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
    QSlider,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
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
        _embed_cover(track_path, cover_path)


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


# ---------- Componentes visuais ----------

STYLESHEET = """
* { font-family: 'Inter', 'Segoe UI', sans-serif; }
QMainWindow, QWidget#root { background: #121212; color: #F2F2F2; }
QWidget#sidebar { background: #000000; border-right: 1px solid #282828; }
QLabel { color: #F1F1F5; }
QLabel.muted { color: #92939F; }
QLabel.eyebrow { color: #8B8D9A; font-size: 10px; font-weight: 700; letter-spacing: 2px; }
QPushButton, QToolButton {
    background: transparent; color: #B3B3B3; border: 0; border-radius: 8px;
    padding: 9px 12px; text-align: center; font-size: 13px;
}
QPushButton:hover, QToolButton:hover { background: #282828; color: #FFFFFF; }
QPushButton[active="true"] { background: #242424; color: #FFFFFF; }
QPushButton#navButton, QPushButton#folderButton { text-align: left; }
QPushButton#primaryButton { background: #1ED760; color: #000000; font-weight: 700; padding: 11px 18px; border-radius: 22px; }
QPushButton#primaryButton:hover { background: #3BE477; }
QPushButton#roundPlay { background: #1ED760; color: #000000; border-radius: 22px; font-size: 18px; font-weight: 800; padding: 0; }
QPushButton#roundPlay:hover { background: #3BE477; }
QPushButton#transportButton { color: #B3B3B3; padding: 0; font-size: 18px; font-weight: 600; }
QPushButton#transportButton:hover { color: #FFFFFF; background: transparent; }
QPushButton#repeatButton { color: #B3B3B3; padding: 0; font-size: 17px; }
QPushButton#repeatButton:hover { color: #FFFFFF; background: transparent; }
QLineEdit {
    background: #242424; border: 1px solid transparent; border-radius: 22px;
    color: #F5F5F7; padding: 10px 16px; selection-background-color: #1ED760;
}
QLineEdit:focus { border: 1px solid #B3B3B3; }
QTableWidget {
    background: transparent; alternate-background-color: #181818; border: 0;
    gridline-color: transparent; color: #E4E4E9; selection-background-color: #2A2A2A;
    selection-color: #FFFFFF; outline: 0;
}
QHeaderView::section {
    background: transparent; color: #A7A7A7; border: 0; border-bottom: 1px solid #303030;
    padding: 10px 8px; font-size: 10px; font-weight: 700; letter-spacing: 1px;
}
QTableWidget::item { padding: 8px; border: 0; }
QTableWidget::item:selected { background: #292929; }
QDialogButtonBox QPushButton { min-width: 90px; text-align: center; }
QSlider::groove:horizontal { height: 4px; background: #5E5E5E; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #1ED760; border-radius: 2px; }
QSlider::add-page:horizontal { background: #5E5E5E; border-radius: 2px; }
QSlider::handle:horizontal { background: #F2F2F5; width: 11px; margin: -4px 0; border-radius: 5px; }
QFrame#playerBar { background: #181818; border-top: 1px solid #282828; }
QFrame#albumCard { background: #181818; border: 1px solid transparent; border-radius: 9px; }
QFrame#albumCard:hover { background: #282828; border: 1px solid #383838; }
QFrame#coverFrame { background: #242424; border-radius: 8px; }
QScrollArea { border: 0; background: transparent; }
QWidget#albumViewport, QWidget#albumGridHost { background: transparent; }
QScrollBar:vertical { width: 10px; background: transparent; margin: 2px 2px 2px 0; }
QScrollBar::handle:vertical { min-height: 28px; background: #535353; border-radius: 5px; }
QScrollBar::handle:vertical:hover { background: #777777; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; background: transparent; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QDialog { background: #181818; }
QDialog QLabel { color: #E9E9EF; }
QDialog QLineEdit { background: #242424; }
QMenu { background: #282828; color: #F2F2F5; border: 1px solid #393939; padding: 5px; }
QMenu::item { padding: 8px 28px 8px 12px; border-radius: 6px; }
QMenu::item:selected { background: #3A3A3A; }
"""


def format_time(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"{total // 60:02d}:{total % 60:02d}"


def pixmap_from_cover(data: Optional[bytes], size: int) -> QPixmap:
    if data:
        pixmap = QPixmap()
        if pixmap.loadFromData(data):
            scaled = pixmap.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            x = max(0, (scaled.width() - size) // 2)
            y = max(0, (scaled.height() - size) // 2)
            return scaled.copy(x, y, size, size)
    return QPixmap()


class AlbumCard(QFrame):
    def __init__(self, album: dict, on_click: Callable, parent=None):
        super().__init__(parent)
        self.album = album
        self.on_click = on_click
        self.setObjectName("albumCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedWidth(196)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(13, 13, 13, 14)
        layout.setSpacing(9)

        cover = QLabel()
        cover.setObjectName("coverFrame")
        cover.setFixedSize(168, 168)
        cover.setAlignment(Qt.AlignCenter)
        pixmap = pixmap_from_cover(album.get("cover"), 168)
        if not pixmap.isNull():
            cover.setPixmap(pixmap)
        else:
            cover.setText("♫")
            cover.setStyleSheet("font-size: 54px; color: #1ED760; background: #242424; border-radius: 8px;")
        layout.addWidget(cover, alignment=Qt.AlignCenter)

        name = QLabel(album["name"])
        name.setStyleSheet("font-size: 13px; font-weight: 700;")
        name.setWordWrap(True)
        name.setMaximumHeight(38)
        layout.addWidget(name)
        artist = QLabel(album["artist"])
        artist.setStyleSheet("font-size: 11px; color: #92939F;")
        artist.setWordWrap(True)
        layout.addWidget(artist)
        count = QLabel(f"{len(album['tracks'])} faixa(s)")
        count.setStyleSheet("font-size: 10px; color: #777986;")
        layout.addWidget(count)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.on_click(self.album)
        super().mousePressEvent(event)


class MetadataDialog(QDialog):
    def __init__(self, track: Track, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Editar metadados")
        self.setMinimumWidth(470)
        self.cover_path: Optional[str] = None
        root = QVBoxLayout(self)
        heading = QLabel("METADADOS DA FAIXA")
        heading.setStyleSheet("color: #1ED760; font-size: 10px; font-weight: 800; letter-spacing: 2px;")
        root.addWidget(heading)

        form = QFormLayout()
        form.setVerticalSpacing(12)
        self.fields: dict[str, QLineEdit] = {}
        field_data = [
            ("title", "Título", track.title),
            ("artist", "Artista", track.artist),
            ("album", "Álbum", track.album if track.album != "Álbum desconhecido" else ""),
            ("albumartist", "Artista do álbum", track.album_artist),
            ("date", "Ano / data", track.year),
            ("tracknumber", "Número da faixa", track.track_number),
            ("discnumber", "Número do disco", track.disc_number),
            ("genre", "Gênero", track.genre),
        ]
        for key, label, value in field_data:
            edit = QLineEdit(value)
            edit.setPlaceholderText(label)
            self.fields[key] = edit
            form.addRow(label, edit)
        root.addLayout(form)

        cover_row = QHBoxLayout()
        self.cover_label = QLabel("Capa atual incorporada ao arquivo" if track.cover else "Sem capa incorporada")
        self.cover_label.setStyleSheet("color: #999BA8; font-size: 11px;")
        cover_row.addWidget(self.cover_label, 1)
        choose_cover = QPushButton("Escolher capa…")
        choose_cover.clicked.connect(self.choose_cover)
        cover_row.addWidget(choose_cover)
        root.addLayout(cover_row)

        self.file_label = QLabel(Path(track.path).name)
        self.file_label.setStyleSheet("color: #777986; font-size: 10px;")
        self.file_label.setWordWrap(True)
        root.addWidget(self.file_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Salvar alterações")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def choose_cover(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Escolher imagem da capa", "", "Imagens (*.jpg *.jpeg *.png *.webp)"
        )
        if path:
            self.cover_path = path
            self.cover_label.setText(Path(path).name)

    def values(self) -> dict[str, str]:
        return {key: widget.text().strip() for key, widget in self.fields.items()}


class SonoraWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SONORA — Music Library")
        self.setMinimumSize(1050, 700)
        self.resize(1440, 900)
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
        self._album_columns = 0
        self._album_stretch_row: Optional[int] = None

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.audio_output.setVolume(0.72)
        self.player.positionChanged.connect(self.update_position)
        self.player.durationChanged.connect(self.update_duration)
        self.player.playbackStateChanged.connect(self.update_play_button)
        self.player.mediaStatusChanged.connect(self.handle_media_status)
        self.player.errorOccurred.connect(self.handle_player_error)

        self._build_ui()
        self._add_shortcuts()
        self._set_volume_label(72)
        saved_folder = self.settings.value("library_folder", "")
        if saved_folder and Path(saved_folder).is_dir():
            self.load_library(saved_folder)

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

        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(228)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(18, 25, 18, 18)
        side.setSpacing(8)

        brand_row = QHBoxLayout()
        brand_mark = QLabel("◖")
        brand_mark.setStyleSheet("color: #1ED760; font-size: 30px; font-weight: 900;")
        brand_row.addWidget(brand_mark)
        brand = QLabel("SONORA")
        brand.setStyleSheet("font-size: 19px; font-weight: 900; letter-spacing: 3px;")
        brand_row.addWidget(brand)
        brand_row.addStretch()
        side.addLayout(brand_row)

        tagline = QLabel("YOUR MUSIC, YOUR SPACE")
        tagline.setObjectName("eyebrow")
        tagline.setStyleSheet("color: #686A77; font-size: 8px; letter-spacing: 1.7px; margin-left: 3px; margin-bottom: 25px;")
        side.addWidget(tagline)

        section = QLabel("BIBLIOTECA")
        section.setObjectName("eyebrow")
        section.setStyleSheet("margin: 0 0 5px 8px;")
        side.addWidget(section)

        self.nav_tracks = self._nav_button("♫   Faixas", True)
        self.nav_albums = self._nav_button("▦   Álbuns", False)
        self.nav_tracks.clicked.connect(lambda: self.show_page("tracks"))
        self.nav_albums.clicked.connect(lambda: self.show_page("albums"))
        side.addWidget(self.nav_tracks)
        side.addWidget(self.nav_albums)

        side.addSpacing(22)
        collection_label = QLabel("SUA COLEÇÃO")
        collection_label.setObjectName("eyebrow")
        side.addWidget(collection_label)
        self.folder_button = QPushButton("＋   Adicionar pasta")
        self.folder_button.setObjectName("folderButton")
        self.folder_button.setMinimumHeight(42)
        self.folder_button.setCursor(Qt.PointingHandCursor)
        self.folder_button.clicked.connect(self.choose_folder)
        side.addWidget(self.folder_button)
        side.addStretch(1)

        self.folder_info = QLabel("Nenhuma pasta selecionada")
        self.folder_info.setWordWrap(True)
        self.folder_info.setStyleSheet("color: #686A77; font-size: 10px; padding: 8px 4px;")
        side.addWidget(self.folder_info)
        version = QLabel("SONORA  ·  DESKTOP 1.0")
        version.setStyleSheet("color: #555762; font-size: 9px; padding: 4px;")
        side.addWidget(version)
        body.addWidget(sidebar)

        content = QWidget()
        main = QVBoxLayout(content)
        main.setContentsMargins(30, 24, 30, 0)
        main.setSpacing(18)
        body.addWidget(content, 1)

        top = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(3)
        self.page_title = QLabel("Sua biblioteca")
        self.page_title.setStyleSheet("font-size: 25px; font-weight: 800;")
        self.page_subtitle = QLabel("Todas as suas músicas em um só lugar")
        self.page_subtitle.setStyleSheet("color: #898B98; font-size: 11px;")
        title_box.addWidget(self.page_title)
        title_box.addWidget(self.page_subtitle)
        top.addLayout(title_box, 1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("⌕   Buscar faixa, artista ou álbum")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(280)
        self.search.textChanged.connect(self.filter_content)
        top.addWidget(self.search)
        self.edit_button = QPushButton("Editar metadados")
        self.edit_button.setMinimumHeight(40)
        self.edit_button.setEnabled(False)
        self.edit_button.clicked.connect(self.edit_selected_track)
        top.addWidget(self.edit_button)
        main.addLayout(top)

        self.pages = QStackedWidget()
        main.addWidget(self.pages, 1)
        self.empty_page = self._make_empty_page()
        self.tracks_page, self.track_table = self._make_tracks_page()
        self.albums_page, self.album_grid, self.album_grid_host = self._make_albums_page()
        self.album_detail_page = self._make_album_detail_page()
        self.pages.addWidget(self.empty_page)
        self.pages.addWidget(self.tracks_page)
        self.pages.addWidget(self.albums_page)
        self.pages.addWidget(self.album_detail_page)

        self._build_player_bar(outer)
        self.pages.setCurrentWidget(self.empty_page)
        self._update_nav("tracks")

    def _nav_button(self, text: str, active: bool) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("navButton")
        button.setProperty("active", active)
        button.setMinimumHeight(42)
        button.setCursor(Qt.PointingHandCursor)
        return button

    def _make_empty_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignCenter)
        icon = QLabel("♫")
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet("font-size: 64px; color: #1ED760;")
        layout.addWidget(icon)
        heading = QLabel("Sua música começa aqui")
        heading.setAlignment(Qt.AlignCenter)
        heading.setStyleSheet("font-size: 23px; font-weight: 800; margin-top: 8px;")
        layout.addWidget(heading)
        desc = QLabel("Selecione uma pasta para importar sua coleção.\nO SONORA organiza as faixas automaticamente pelos metadados.")
        desc.setAlignment(Qt.AlignCenter)
        desc.setStyleSheet("color: #92939F; font-size: 12px; line-height: 1.5; margin: 8px 0 18px;")
        layout.addWidget(desc)
        choose = QPushButton("Selecionar pasta de músicas")
        choose.setObjectName("primaryButton")
        choose.setCursor(Qt.PointingHandCursor)
        choose.clicked.connect(self.choose_folder)
        layout.addWidget(choose, alignment=Qt.AlignCenter)
        return page

    def _new_track_table(self) -> QTableWidget:
        table = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(["TÍTULO", "ARTISTA", "ÁLBUM", "DURAÇÃO"])
        table.verticalHeader().setVisible(False)
        table.setShowGrid(False)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setFocusPolicy(Qt.NoFocus)
        table.setContextMenuPolicy(Qt.CustomContextMenu)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setMaximumSectionSize(260)
        header.setSectionResizeMode(1, QHeaderView.Interactive)
        header.setSectionResizeMode(2, QHeaderView.Interactive)
        header.setSectionResizeMode(3, QHeaderView.Fixed)
        table.setColumnWidth(1, 180)
        table.setColumnWidth(2, 190)
        table.setColumnWidth(3, 86)
        table.verticalHeader().setDefaultSectionSize(50)
        table.cellDoubleClicked.connect(lambda row, _col, t=table: self.play_table_row(t, row))
        table.itemSelectionChanged.connect(self.on_table_selection_changed)
        table.customContextMenuRequested.connect(lambda pos, t=table: self.show_track_context_menu(t, pos))
        return table

    def _make_tracks_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self.track_count_label = QLabel("0 faixas")
        self.track_count_label.setStyleSheet("color: #898B98; font-size: 11px; padding: 0 2px 5px;")
        layout.addWidget(self.track_count_label)
        table = self._new_track_table()
        layout.addWidget(table, 1)
        return page, table

    def _make_albums_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self.album_count_label = QLabel("0 álbuns")
        self.album_count_label.setStyleSheet("color: #898B98; font-size: 11px; padding: 0 2px 5px;")
        layout.addWidget(self.album_count_label)
        scroll = QScrollArea()
        scroll.viewport().setObjectName("albumViewport")
        scroll.setWidgetResizable(True)
        host = QWidget()
        host.setObjectName("albumGridHost")
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 2, 8, 15)
        grid.setHorizontalSpacing(15)
        grid.setVerticalSpacing(17)
        scroll.setWidget(host)
        layout.addWidget(scroll, 1)
        return page, grid, host

    def _make_album_detail_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        hero = QHBoxLayout()
        self.detail_cover = QLabel("♫")
        self.detail_cover.setFixedSize(174, 174)
        self.detail_cover.setAlignment(Qt.AlignCenter)
        self.detail_cover.setStyleSheet("font-size: 54px; color: #1ED760; background: #242424; border-radius: 12px;")
        hero.addWidget(self.detail_cover)
        info = QVBoxLayout()
        info.setAlignment(Qt.AlignVCenter)
        self.detail_type = QLabel("ÁLBUM")
        self.detail_type.setObjectName("eyebrow")
        self.detail_name = QLabel("")
        self.detail_name.setWordWrap(True)
        self.detail_name.setStyleSheet("font-size: 27px; font-weight: 850;")
        self.detail_artist = QLabel("")
        self.detail_artist.setStyleSheet("font-size: 13px; color: #B7B8C2;")
        self.detail_meta = QLabel("")
        self.detail_meta.setStyleSheet("font-size: 11px; color: #898B98;")
        play_album = QPushButton("▶   Reproduzir álbum")
        play_album.setObjectName("primaryButton")
        play_album.clicked.connect(self.play_current_album)
        info.addWidget(self.detail_type)
        info.addWidget(self.detail_name)
        info.addWidget(self.detail_artist)
        info.addWidget(self.detail_meta)
        info.addSpacing(10)
        info.addWidget(play_album, alignment=Qt.AlignLeft)
        info.addStretch()
        hero.addLayout(info, 1)
        layout.addLayout(hero)
        self.detail_table = self._new_track_table()
        layout.addWidget(self.detail_table, 1)
        return page

    def _build_player_bar(self, outer: QVBoxLayout):
        bar = QFrame()
        bar.setObjectName("playerBar")
        bar.setMinimumHeight(104)
        row = QHBoxLayout(bar)
        row.setContentsMargins(22, 12, 22, 12)
        row.setSpacing(14)

        self.player_cover = QLabel("♫")
        self.player_cover.setFixedSize(60, 60)
        self.player_cover.setAlignment(Qt.AlignCenter)
        self.player_cover.setStyleSheet("font-size: 24px; color: #1ED760; background: #282828; border-radius: 8px;")
        row.addWidget(self.player_cover)

        song_info = QVBoxLayout()
        song_info.setSpacing(4)
        self.now_title = QLabel("Nada tocando")
        self.now_title.setStyleSheet("font-size: 12px; font-weight: 700;")
        self.now_title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.now_artist = QLabel("Escolha uma faixa da sua biblioteca")
        self.now_artist.setStyleSheet("font-size: 10px; color: #8F919E;")
        self.now_artist.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        song_info.addWidget(self.now_title)
        song_info.addWidget(self.now_artist)
        row.addLayout(song_info, 2)

        controls = QVBoxLayout()
        controls.setSpacing(5)
        buttons = QHBoxLayout()
        buttons.setSpacing(12)
        self.repeat_button = QPushButton("↻")
        self.repeat_button.setObjectName("repeatButton")
        self.repeat_button.setFixedSize(32, 30)
        self.repeat_button.setToolTip("Repetição: desligada")
        self.repeat_button.setCursor(Qt.PointingHandCursor)
        self.repeat_button.clicked.connect(self.cycle_repeat)
        self.previous_button = QPushButton("⏮")
        self.previous_button.setObjectName("transportButton")
        self.previous_button.setFixedSize(34, 34)
        self.previous_button.setToolTip("Faixa anterior")
        self.previous_button.setCursor(Qt.PointingHandCursor)
        self.previous_button.clicked.connect(self.previous_track)
        self.play_button = QPushButton("▶")
        self.play_button.setObjectName("roundPlay")
        self.play_button.setFixedSize(44, 44)
        self.play_button.setCursor(Qt.PointingHandCursor)
        self.play_button.clicked.connect(self.toggle_playback)
        self.next_button = QPushButton("⏭")
        self.next_button.setObjectName("transportButton")
        self.next_button.setFixedSize(34, 34)
        self.next_button.setToolTip("Próxima faixa")
        self.next_button.setCursor(Qt.PointingHandCursor)
        self.next_button.clicked.connect(self.next_track)
        buttons.addStretch(1)
        for button in (self.repeat_button, self.previous_button, self.play_button, self.next_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        controls.addLayout(buttons)

        seek_row = QHBoxLayout()
        self.position_label = QLabel("00:00")
        self.position_label.setStyleSheet("color: #828491; font-size: 9px;")
        self.seek_slider = QSlider(Qt.Horizontal)
        self.seek_slider.setRange(0, 0)
        self.seek_slider.sliderPressed.connect(self._seek_pressed)
        self.seek_slider.sliderReleased.connect(self._seek_released)
        self.duration_label = QLabel("00:00")
        self.duration_label.setStyleSheet("color: #828491; font-size: 9px;")
        seek_row.addWidget(self.position_label)
        seek_row.addWidget(self.seek_slider, 1)
        seek_row.addWidget(self.duration_label)
        controls.addLayout(seek_row)
        row.addLayout(controls, 4)

        volume_box = QHBoxLayout()
        self.volume_icon = QLabel("◖))")
        self.volume_icon.setStyleSheet("color: #9A9CA8; font-size: 11px;")
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(72)
        self.volume_slider.setFixedWidth(104)
        self.volume_slider.valueChanged.connect(self.change_volume)
        volume_box.addWidget(self.volume_icon)
        volume_box.addWidget(self.volume_slider)
        row.addLayout(volume_box)
        outer.addWidget(bar)

    def _add_shortcuts(self):
        open_action = QAction(self)
        open_action.setShortcut(QKeySequence("Ctrl+O"))
        open_action.triggered.connect(self.choose_folder)
        self.addAction(open_action)
        edit_action = QAction(self)
        edit_action.setShortcut(QKeySequence("Ctrl+E"))
        edit_action.triggered.connect(self.edit_selected_track)
        self.addAction(edit_action)
        play_action = QAction(self)
        play_action.setShortcut(QKeySequence("Space"))
        play_action.triggered.connect(self.toggle_playback)
        self.addAction(play_action)

    # ----- Biblioteca -----
    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Selecionar pasta de músicas", self.library_path or str(Path.home()))
        if folder:
            self.load_library(folder)

    def load_library(self, folder: str):
        self.library_path = folder
        self.settings.setValue("library_folder", folder)
        self.folder_info.setText(folder)
        self.folder_info.setToolTip(folder)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            found: list[Track] = []
            for root, _dirs, files in os.walk(folder):
                for filename in files:
                    path = os.path.join(root, filename)
                    if Path(filename).suffix.lower() in AUDIO_EXTENSIONS:
                        track = read_track(path)
                        if track:
                            found.append(track)
            found.sort(key=self.track_sort_key)
            self.all_tracks = found
            self._group_albums()
            self._populate_tracks(self.track_table, found)
            self._populate_albums()
            self.track_count_label.setText(f"{len(found)} faixa(s) encontradas")
            self.album_count_label.setText(f"{len(self.albums)} álbuns")
            self.pages.setCurrentWidget(self.tracks_page if found else self.empty_page)
            self.page_title.setText("Sua biblioteca")
            self.page_subtitle.setText(f"{len(found)} faixas · {len(self.albums)} álbuns")
            self.search.clear()
            if not found:
                QMessageBox.information(self, "Nenhuma música encontrada", "Não encontrei arquivos de áudio compatíveis nesta pasta.")
        finally:
            QApplication.restoreOverrideCursor()

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
        table.setRowCount(0)
        table.tracks = tracks
        for row, track in enumerate(tracks):
            table.insertRow(row)
            title_item = QTableWidgetItem(track.title)
            title_item.setData(Qt.UserRole, track.path)
            title_item.setToolTip(track.path)
            artist_item = QTableWidgetItem(track.display_artist)
            album_item = QTableWidgetItem(track.display_album)
            duration_item = QTableWidgetItem(format_time(track.duration))
            duration_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            table.setItem(row, 0, title_item)
            table.setItem(row, 1, artist_item)
            table.setItem(row, 2, album_item)
            table.setItem(row, 3, duration_item)

    def _populate_albums(self):
        while self.album_grid.count():
            item = self.album_grid.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        query = self.search.text().strip().casefold()
        visible = [a for a in self.albums if not query or query in a["name"].casefold() or query in a["artist"].casefold()]
        available_width = max(0, self.width() - 228 - 60 - 8)
        columns = max(1, (available_width + 15) // (196 + 15))
        self._album_columns = columns
        for index, album in enumerate(visible):
            card = AlbumCard(album, self.open_album)
            self.album_grid.addWidget(card, index // columns, index % columns, Qt.AlignTop | Qt.AlignLeft)
        self.album_count_label.setText(f"{len(visible)} de {len(self.albums)} álbuns")
        if self._album_stretch_row is not None:
            self.album_grid.setRowStretch(self._album_stretch_row, 0)
        self._album_stretch_row = (len(visible) + columns - 1) // columns
        self.album_grid.setRowStretch(self._album_stretch_row, 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "album_grid"):
            available_width = max(0, event.size().width() - 228 - 60 - 8)
            columns = max(1, (available_width + 15) // (196 + 15))
            if columns != self._album_columns:
                self._populate_albums()

    def filter_content(self, text: str):
        query = text.strip().casefold()
        filtered = [t for t in self.all_tracks if not query or any(
            query in value.casefold() for value in (t.title, t.artist, t.album, t.album_artist, t.genre)
        )]
        self._populate_tracks(self.track_table, filtered)
        if hasattr(self, "album_grid"):
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
            self.page_subtitle.setText(f"{len(self.all_tracks)} músicas na biblioteca")
        elif page == "albums":
            self.pages.setCurrentWidget(self.albums_page if self.all_tracks else self.empty_page)
            self.page_title.setText("Álbuns")
            self.page_subtitle.setText("Sua coleção organizada por álbum e artista")
            self._populate_albums()
        self._update_nav(page)

    def _update_nav(self, active: str):
        self.nav_tracks.setProperty("active", active == "tracks")
        self.nav_albums.setProperty("active", active == "albums")
        self.nav_tracks.style().unpolish(self.nav_tracks)
        self.nav_tracks.style().polish(self.nav_tracks)
        self.nav_albums.style().unpolish(self.nav_albums)
        self.nav_albums.style().polish(self.nav_albums)

    def open_album(self, album: dict):
        self.current_album = album
        self.page_title.setText("Álbum")
        self.page_subtitle.setText("Visão do álbum")
        pixmap = pixmap_from_cover(album.get("cover"), 174)
        if not pixmap.isNull():
            self.detail_cover.setPixmap(pixmap)
            self.detail_cover.setText("")
        else:
            self.detail_cover.setPixmap(QPixmap())
            self.detail_cover.setText("♫")
        self.detail_name.setText(album["name"])
        self.detail_artist.setText(album["artist"])
        duration = sum(t.duration for t in album["tracks"])
        year = album.get("year")
        pieces = [f"{len(album['tracks'])} faixa(s)"]
        if year:
            pieces.insert(0, year)
        pieces.append(format_time(duration))
        self.detail_meta.setText("  ·  ".join(pieces))
        self._populate_tracks(self.detail_table, album["tracks"])
        self.pages.setCurrentWidget(self.album_detail_page)
        self.nav_tracks.setProperty("active", False)
        self.nav_albums.setProperty("active", True)
        self._update_nav("albums")

    # ----- Seleção, contexto e edição -----
    def play_table_row(self, table: QTableWidget, row: int):
        tracks = getattr(table, "tracks", [])
        if 0 <= row < len(tracks):
            self.play_track(tracks[row], tracks)

    def selected_track(self) -> Optional[Track]:
        current = self.pages.currentWidget()
        table = self.detail_table if current == self.album_detail_page else self.track_table
        row = table.currentRow()
        tracks = getattr(table, "tracks", [])
        return tracks[row] if 0 <= row < len(tracks) else None

    def on_table_selection_changed(self):
        self.edit_button.setEnabled(self.selected_track() is not None)

    def show_track_context_menu(self, table: QTableWidget, pos):
        row = table.rowAt(pos.y())
        if row >= 0:
            table.selectRow(row)
        track = getattr(table, "tracks", [])[row] if 0 <= row < len(getattr(table, "tracks", [])) else None
        if not track:
            return
        menu = QMenu(self)
        play = menu.addAction("▶  Reproduzir")
        edit = menu.addAction("✎  Editar metadados")
        chosen = menu.exec(table.viewport().mapToGlobal(pos))
        if chosen == play:
            self.play_track(track, getattr(table, "tracks", []))
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
            playing_path = self.current_track.path if self.current_track else None
            self.load_library(self.library_path)
            if playing_path:
                refreshed = next((t for t in self.all_tracks if t.path == playing_path), None)
                if refreshed:
                    self.current_track = refreshed
                    self._show_now_playing(refreshed)
            QMessageBox.information(self, "Metadados salvos", "As alterações foram gravadas no arquivo de música.")
        except Exception as exc:
            QMessageBox.critical(self, "Erro ao salvar", str(exc))

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
        self.play_button.setText("Ⅱ")

    def _show_now_playing(self, track: Track):
        self.now_title.setText(track.title)
        self.now_title.setToolTip(track.title)
        self.now_artist.setText(track.display_artist)
        self.now_artist.setToolTip(track.display_artist)
        pixmap = pixmap_from_cover(track.cover, 60)
        if not pixmap.isNull():
            self.player_cover.setPixmap(pixmap)
            self.player_cover.setText("")
        else:
            self.player_cover.setPixmap(QPixmap())
            self.player_cover.setText("♫")

    def toggle_playback(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        elif self.current_track:
            self.player.play()
        elif self.all_tracks:
            self.play_track(self.all_tracks[0], self.all_tracks)

    def update_play_button(self, state):
        self.play_button.setText("Ⅱ" if state == QMediaPlayer.PlaybackState.PlayingState else "▶")

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
        names = ["desligada", "todas as faixas", "faixa atual"]
        self.repeat_button.setToolTip(f"Repetição: {names[self.repeat_mode]}")
        self.repeat_button.setStyleSheet("color: #1ED760; font-weight: 800;" if self.repeat_mode else "color: #B3B3B3;")
        self.repeat_button.setText("↻¹" if self.repeat_mode == 2 else "↻")

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
                self.play_button.setText("▶")

    def handle_player_error(self, _error, error_string):
        if error_string:
            self.statusBar().showMessage(f"Falha na reprodução: {error_string}", 7000)

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
        self._set_volume_label(value)

    def _set_volume_label(self, value: int):
        self.volume_icon.setText("◖))" if value > 55 else "◖)" if value > 0 else "◖")
        self.volume_slider.setToolTip(f"Volume: {value}%")


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("SonoraStudio")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    window = SonoraWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
