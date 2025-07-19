# OctoUpdater 🐙🔄

Автоматический сканер зависимостей и генератор Pull Request для репозиториев GitHub.  
*Держите зависимости в актуальном состоянии без ручной рутины.*

![Docker](https://img.shields.io/badge/Docker-ready-blue) ![FastAPI](https://img.shields.io/badge/FastAPI-async-green) ![License](https://img.shields.io/badge/license-MIT-lightgrey)

## Key Features
- Веб-панель `/dependencies` с тёмной темой.
- One-click регистрация репозитория: сервис сам создаёт GitHub Webhook.
- Поддержка `requirements.txt`, `package.json`, `pom.xml`/`build.gradle` и др.
- Автоматическое создание ветки и PR с обновлёнными зависимостями.
- Фоновый скан каждые 6 ч (легко настроить).
- Метрики Prometheus + дашбоард Grafana.
- Полностью контейнеризовано (Docker Compose).

## Architecture

```text
                +-------------+       push event       +--------------+
                |   GitHub    |  ───────────────────►  |   Webhook     |
                +-------------+                        +--------------+
                                                             │
                                                             ▼
                                                 +-----------------------+
                                                 |    OctoUpdater API    |
                                                 |  (FastAPI + Celery)   |
                                                 +-----------------------+
                                                             │
             scheduled scan (APScheduler)       ─────────────┘
                                                             ▼
                                                 +-----------------------+
                                                 |   Pull Request (PR)   |
                                                 +-----------------------+
                                                             │
                                                             ▼
                                                    +--------------+
                                                    |   GitHub     |
                                                    +--------------+
```




## Компоненты

1. **GitHub Webhook Monitor** - отслеживает изменения в master ветке
2. **Build Service** - собирает Java проект с Gradle
3. **Docker Builder** - создает и публикует Docker образы
4. **Deploy Service** -

## Технологический стек

- **Backend**: Python FastAPI / Node.js Express
- **Build**: Java + Gradle
- **Containerization**: Docker + Docker Compose
- **CI/CD**: GitHub Actions / Jenkins
- **Monitoring**: Prometheus + Grafana
- **Notifications**: Slack/Telegram интеграция

## Быстрый старт

1. Клонируйте репозиторий и поднимите Docker-стек:

   ```bash
   git clone https://github.com/Ivantech123/-i-cd-java.git
   cd github-ci-cd-monitor
   cp .env.example .env   # укажите GITHUB_WEBHOOK_SECRET и др.
   docker-compose up -d --build
   ```

2. Откройте браузер `http://localhost/dependencies`.

3. В правом верхнем углу нажмите ⚙️ **Settings** и:
   * Вставьте **GitHub PAT** (scope `repo`, `admin:repo_hook`) → «Save Token»;
   * Введите URL репозитория и ветку → «Add». Сервис сам создаст webhook `push` ➜ `/webhook`.

4. Готово! При каждом push сервис будет:
   * автоматически сканировать зависимости;
   * создавать PR, если есть обновления;
   * каждые 6 часов выполнять дополнительный фоновой скан (APScheduler).

## Получение GitHub PAT

Чтобы сервис мог создавать вебхуки и Pull Request, нужен персональный токен с правами на репозиторий.

1. Войдите в GitHub → аватар → **Settings**.
2. Слева внизу **Developer settings** → **Personal access tokens**.
3. Выберите:
   * **Tokens (classic)** → *Generate new token* **или**
   * **Fine-grained tokens** (рекомендуется) → *Generate new token*.
4. Задайте:
   * **Expiration**: 90 days или Custom.
   * **Repository access**: *Only select repositories* → выберите нужные, либо *All repositories*.
   * **Permissions**:
     - `repo` (все подразделы) — для чтения/записи кода и создания PR.
     - `admin:repo_hook` — для создания вебхуков.
5. Нажмите **Generate token** и СКОПИРУЙТЕ строку — GitHub показывает её один раз.
6. Перейдите в веб-панель OctoUpdater → **Settings** → вставьте токен и нажмите «Save Token».

> ⚠️ Никому не показывайте PAT и не коммитьте в репозиторий.

## Фоновое сканирование

`APScheduler` запускается при старте бекенда и раз в 6 часов перебирает все репозитории из `repos.json`. Если находятся устаревшие версии — инициируется тот же процесс создания Pull Request.

При необходимости изменить частоту, правьте интервал в `webhook_monitor.py`:

```python
scheduler.add_job(lambda: asyncio.run(scheduled_scan()), 'interval', hours=6)
```

## Безопасность

- Webhook подписи для верификации
- Секретные токены в environment variables
- Docker registry аутентификация
- Изолированные build environments
