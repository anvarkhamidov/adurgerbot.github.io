# Video Downloader Bot

Telegram-бот: присылаете ссылку на видео — бот скачивает его через актуальный
[yt-dlp](https://github.com/yt-dlp/yt-dlp) в лучшем доступном качестве (отдельные
видео- и аудиодорожки), FFmpeg объединяет их в MP4 со звуком, и файл приходит в чат.

- **Качество:** максимальное разрешение/fps; при равном качестве предпочитаются
  H.264 + AAC (воспроизводятся во всех клиентах Telegram). Аудио в MP4 всегда AAC.
- **Лимит Telegram:** если видео не влезает (50 МБ по умолчанию, 2000 МБ с локальным
  Bot API), бот сам переходит на качество ниже и ограничивает битрейт аудио.
  При 50 МБ 10-минутное видео помещается примерно в 480p — для 720p и выше
  включите локальный Bot API (ниже).
- **YouTube с IP сервера:** контейнер `pot-provider` выдаёт yt-dlp PO-токены, а
  бот использует клиент `mweb`. Без этого YouTube отвечает дата-центрам
  «Sign in to confirm you're not a bot» или отдаёт максимум 360p.
- **Устойчивость:** повторы HTTP-запросов и фрагментов с backoff, до
  `DOWNLOAD_ATTEMPTS` полных попыток, докачка `.part`-файлов; повторная отправка
  той же ссылки продолжает прерванное скачивание. Отправка в Telegram тоже с повторами.
- **Очистка:** после успешной отправки временная папка удаляется; остатки неудачных
  загрузок чистятся через `STALE_HOURS`.
- **yt-dlp обновляется** до последней версии при каждом старте контейнера.

## Развёртывание на VPS

Нужен Linux-сервер с доступом в интернет. Входящие порты не нужны (long polling),
firewall и SSH скрипт не трогает.

```bash
curl -fsSL https://raw.githubusercontent.com/anvarkhamidov/adurgerbot.github.io/claude/telegram-video-downloader-bot-00mq5d/deploy.sh | bash
```

Скрипт при необходимости (с подтверждением) ставит Docker, клонирует репозиторий
в `~/video-bot`, спрашивает токен бота, ID разрешённых пользователей и (по желанию)
`api_id`/`api_hash` для лимита 2 ГБ, записывает их в `.env` (права 600) и запускает `docker compose up -d --build`. Контейнеры
перезапускаются автоматически (`restart: unless-stopped`), в том числе после
перезагрузки сервера. Повторный запуск скрипта обновляет бота.

Вручную:

```bash
git clone -b claude/telegram-video-downloader-bot-00mq5d https://github.com/anvarkhamidov/adurgerbot.github.io.git ~/video-bot
cd ~/video-bot && cp .env.example .env && nano .env   # BOT_TOKEN, ALLOWED_USERS
sudo docker compose up -d --build
```

## Управление

```bash
cd ~/video-bot
sudo docker compose logs -f bot     # логи
sudo docker compose restart bot     # перезапуск (заодно обновит yt-dlp)
sudo docker compose down            # остановить
```

## Файлы до 2 ГБ (локальный Bot API)

Официальный Bot API принимает от ботов файлы до 50 МБ. Локальный сервер
`telegram-bot-api` поднимает лимит до 2000 МБ:

1. Получите `api_id` и `api_hash` на <https://my.telegram.org> → API development tools.
2. Отвяжите бота от облачного API (один раз):
   `curl https://api.telegram.org/bot<BOT_TOKEN>/logOut`
3. Раскомментируйте в `.env` строки `COMPOSE_PROFILES`, `BOT_API_URL`,
   `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` и выполните `sudo docker compose up -d`.

## Cookies (если YouTube всё равно требует вход, закрытый контент)

Если IP сервера у YouTube в чёрном списке даже с PO-токенами, или нужен контент
только для авторизованных (18+, подписки), нужны cookies. Экспортируйте
cookies в формате Netscape (например, расширением «Get cookies.txt LOCALLY»)
и положите в том бота:

```bash
sudo docker compose cp cookies.txt bot:/data/cookies.txt
sudo docker compose restart bot
```

## Настройки (`.env`)

| Переменная | По умолчанию | Описание |
|---|---|---|
| `BOT_TOKEN` | — | токен от @BotFather (обязательно) |
| `ALLOWED_USERS` | все | ID пользователей через запятую |
| `MAX_CONCURRENT` | 2 | одновременных загрузок |
| `DOWNLOAD_ATTEMPTS` | 5 | полных попыток скачивания |
| `STALE_HOURS` | 24 | через сколько часов удалять остатки неудачных загрузок |
| `BOT_API_URL` | — | адрес локального Bot API |
| `MAX_FILE_MB` | 50 / 2000 | лимит размера файла |
| `YTDLP_AUTO_UPDATE` | 1 | обновлять yt-dlp при старте |
