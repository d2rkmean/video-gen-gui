<div align="center">

# video-gen-gui

**Графический и веб-интерфейс (Flet 1.1) для настройки, запуска и мониторинга конвейера [`video-gen`](https://github.com/d2rkmean/video-gen)**

[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![Flet 1.1](https://img.shields.io/badge/UI-Flet%201.1-02569B?style=flat-square&logo=flutter&logoColor=white)](https://flet.dev/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)
[![Ruff](https://img.shields.io/badge/Linter-Ruff-D7FF64?style=flat-square&logo=ruff&logoColor=black)](https://docs.astral.sh/ruff/)
[![Mypy](https://img.shields.io/badge/Typecheck-Mypy-2A6DB2?style=flat-square)](https://mypy-lang.org/)

*Управляйте генерацией Shorts/Reels локально или на удалённом GPU-сервере по OpenSSH без ручного редактирования JSON и терминала.*

</div>

---

## Возможности

- **Два режима работы (`LOCAL` и `SSH`)**:
  - переключение на Дашборде с индикатором активного хоста в шапке окна;
  - диагностика окружения (`video-gen`, `uv`, `.venv`, `FFmpeg`, `NVENC GPU`) локально и на удалённом сервере;
  - запуск `uv sync` / `uv sync --upgrade` и CLI `video-gen --json-progress` с живым прогрессом, логами и корректной остановкой процесса (включая дочерние процессы на удалённом хосте).
- **Полноценный редактор `config.json`** (100% совместимость со strict-схемой `video-gen` + импорт legacy `.toml`):
  - **Источники**: управление `top_videos` (файлы, папки, YouTube) и `bottom_videos` (видео или PyGame-пресеты), локальный `FilePicker` и удалённый SSH-проводник;
  - **Конвейер**: режим нарезки (`highlights` / `speech_split`), пресеты LLM (OpenAI, Groq, Google Gemini, OpenRouter, Ollama), настройки Whisper, SponsorBlock, воркеров и кэша;
  - **Рендер**: разрешение, кодеки NVENC/CPU, цветокоррекция, антидетект, расписание баннеров (`center`, `overlay`, `end`, `interval_minutes`, `interval_percent`), фоновая музыка;
  - **Субтитры**: живое пиксельное превью караоке-стиля через Pillow с автоматической синхронизацией `.ttf`/`.otf` шрифтов с удалённого сервера;
  - **База данных**: настройка PostgreSQL и проверка доступности TCP-порта (локально или с удалённого сервера по SSH);
  - **Клипы**: галерея готовых `.mp4` и `.json` метаданных, копирование сгенерированных описаний и потоковое скачивание клипов по SSH.

---

## Требования

- **Python:** 3.12+
- **Менеджер пакетов:** [uv](https://docs.astral.sh/uv/)
- **Для режима `LOCAL`:** локальная копия [`video-gen`](https://github.com/d2rkmean/video-gen)
- **Для режима `SSH`:** клиент `ssh` (OpenSSH) в `PATH` и настроенная авторизация по SSH-ключу на сервере

---

## Установка и запуск

```bash
git clone https://github.com/d2rkmean/video-gen-gui.git
cd video-gen-gui
uv sync
```

**Desktop-приложение:**

```bash
uv run video-gen-gui
# либо через Flet CLI:
uv run flet run
```

**Web-версия (в браузере):**

```bash
uv run flet run --web
```

Настройки интерфейса сохраняются в `~/.config/video-gen-gui/settings.json`, локальный кэш удалённых конфигов, шрифтов и скачанных клипов — в `~/.cache/video-gen-gui/remote/`.

---

## Подключение к удалённому GPU-серверу (SSH)

1. Настройте вход по SSH-ключу без парольной фразы (`BatchMode=yes` — интерактивный ввод пароля отключён).
2. На вкладке **Дашборд** выберите режим **Удалённый сервер (SSH)**.
3. Укажите хост (`IP` или `user@host`), порт, пользователя, путь к приватному ключу (если нестандартный) и путь к репозиторию `video-gen` на сервере (например, `~/projects/video-gen`).
4. Нажмите **Проверить SSH** — приложение проверит соединение, определит GPU/ОС и просканирует окружение.

---

## Структура проекта

```text
video-gen-gui/
├── src/video_gen_gui/
│   ├── app.py               # Главный каркас приложения Flet и навигация
│   ├── models.py            # Модели конфигурации (зеркало strict-схемы video-gen) и GuiSettings
│   ├── state.py             # Состояние приложения и сохранение настроек
│   ├── runner.py            # Локальный/SSH запуск CLI, preflight и парсинг --json-progress
│   ├── remote.py            # OpenSSH-операции (чтение/запись, sync шрифтов, скачивание клипов)
│   ├── fonts.py             # Поиск шрифтов и генерация PNG-превью субтитров через Pillow
│   └── views/               # 7 экранов интерфейса (dashboard, sources, pipeline, render, subtitles, database, gallery)
├── tests/                   # Unit-тесты UI/моделей, аудит схемы и real sshd E2E-тесты
└── pyproject.toml           # Зависимости и настройки ruff / mypy / pytest
```

---

## Тестирование и качество кода

В набор входят unit-тесты моделей и UI, аудит строгой совместимости со схемой `video-gen`, а также настоящие end-to-end SSH-тесты против локального экземпляра `sshd`.

```bash
uv run ruff check .
uv run mypy src tests
uv run pytest -v
```

---

## Лицензия

Проект распространяется под лицензией [MIT](LICENSE).
