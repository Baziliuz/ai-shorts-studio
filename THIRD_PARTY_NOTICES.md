# Third-party notices — 0.1.0

Проект: GPL-3.0-or-later, полный текст в LICENSE. Приложение не содержит AI-моделей. Таблица описывает планируемый Windows-комплект, сборка и базовый запуск которого проверены Windows CI; ручная приёмка остаётся отдельной.

| Компонент | Версия | Лицензия | Источник и назначение |
|---|---|---|---|
| CPython x64 | 3.12.9 для CI | PSF License 2 | https://www.python.org/downloads/release/python-3129/ ; встроенный интерпретатор PyInstaller |
| PySide6, Addons, Essentials, shiboken6 | 6.8.3 | LGPL-3.0 / GPL, см. каждый модуль | https://pypi.org/project/PySide6/6.8.3/ ; https://code.qt.io/cgit/pyside/pyside-setup.git/ |
| Qt Core/Gui/Widgets/Multimedia | 6.8.3 | LGPL-3.0 / GPL; отдельные third-party notices | https://download.qt.io/archive/qt/6.8/6.8.3/ ; динамические DLL и Multimedia плагины из PySide6 wheel |
| Qt FFmpeg backend | из PySide6 6.8.3 wheel | LGPL для bundled FFmpeg, конкретный состав проверить в Windows wheel | https://doc.qt.io/qt-6.8/qtmultimedia-index.html ; DLL avcodec/avformat/avutil и др., не подменяются CLI |
| platformdirs | 4.3.6 | MIT | https://pypi.org/project/platformdirs/4.3.6/ ; пользовательские каталоги |
| FFmpeg / ffprobe Gyan Essentials | 7.1.1 | GPL-3.0, LICENSE внутри архива | https://github.com/GyanD/codexffmpeg/releases/tag/7.1.1 ; CLI, в том числе GPL libx264 |
| PyInstaller (инструмент) | 6.12.0 | GPL-2.0-or-later + bootloader exception | https://pypi.org/project/pyinstaller/6.12.0/ |
| pyinstaller-hooks-contrib (инструмент) | 2025.2 | Apache-2.0 / GPL hooks | https://pypi.org/project/pyinstaller-hooks-contrib/2025.2/ |
| Inno Setup (инструмент) | 6, установленный на Windows runner | Inno Setup License | https://jrsoftware.org/isinfo.php ; версия компилятора выводится в CI, точный patch зависит от образа runner |
| pytest (только тесты) | 8.3.4 | MIT | https://pypi.org/project/pytest/8.3.4/ |

Поддержка ОС по upstream: Python 3.12 и Qt 6.8 рассчитаны на Windows 10/11 x64. Источники: https://doc.qt.io/qt-6.8/supported-platforms.html и https://docs.python.org/3.12/using/windows.html . На Windows Server 2022 пройдены CI-тесты, сборка и базовый запуск EXE. На целевых Windows 10/11 совместимость ещё требуется проверить. Метаданные PyPI закреплённых версий проверены при реализации; Linux-проверка не подтверждает Windows.

## Проверка архива FFmpeg

URL: https://github.com/GyanD/codexffmpeg/releases/download/7.1.1/ffmpeg-7.1.1-essentials_build.zip

SHA-256: `04861d3339c5ebe38b56c19a15cf2c0cc97f5de4fa8910e4d47e5e6404e4a2d4`.

Архив загружен и хеш вычислен при подготовке проекта. `scripts/fetch_ffmpeg.py` сверяет его до извлечения. Это закрепление проверенного артефакта, не отдельная криптографическая подпись автора. В сборку входят ffmpeg.exe, ffprobe.exe и оригинальный LICENSE. Конфигурацию конкретного бинарника можно получить командой `ffmpeg -buildconf`.

Исходники FFmpeg: https://ffmpeg.org/releases/ffmpeg-7.1.1.tar.xz ; рецепты поставщика: https://github.com/GyanD/codexffmpeg ; исходники x264: https://code.videolan.org/videolan/x264 . Перед публичным распространением бинарного комплекта подготовить соответствующие исходники всех GPL-компонентов и сохранить лицензии/уведомления из Windows wheel. Одних ссылок на upstream недостаточно для заявления о завершённой лицензионной комплектации выпуска. Сейчас публичный бинарный выпуск не подготовлен.

Runtime-комплект: приложение и его исходный код; CPython runtime; PySide6/shiboken6; необходимые Qt DLL, platform plugin и Multimedia plugin; backend codec DLL из wheel; platformdirs; FFmpeg/ffprobe CLI; лицензии. Сборка onedir сохраняет динамические библиотеки отдельно. Тексты GPL-3.0/LGPL-3.0/LGPL-2.1 включены в `packaging/licenses`. Модели, личные записи, тестовые видео, SQLite-проекты и кэш не включаются.

Будущие AI-модели допускаются только после фиксации источника, версии, лицензии, размера и контрольной суммы отдельным реестром; в версии 0.1.0 такого реестра нет, потому что модели не поставляются.
