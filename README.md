# telegrambott — автоответчик для Telegram

Бот отвечает на личные сообщения **от вашего имени**, пока вы заняты. Ответы берутся из ваших шаблонов в `replies.toml`, без ИИ и платных API.

Бот подключается через официальную функцию **Telegram Business → Чат-боты**, поэтому сообщения уходят прямо из вашего аккаунта. Без риска блокировки, как у «юзерботов». Нужна подписка **Telegram Premium**.

## Как это работает

1. Вам кто-то пишет, и бот получает сообщение через бизнес-подключение.
2. Бот ждёт `REPLY_DELAY_SECONDS`: собеседник может дописать ещё, а вы можете ответить сами.
3. Бот подбирает шаблон по такому порядку:
   - сообщение целиком из списка `skip` («ок», «спасибо»): бот не отвечает;
   - нашлось ключевое слово из правила: ответ этого правила;
   - сейчас ночные часы: ночной ответ;
   - иначе: ответ по умолчанию.
4. Ответ уходит от вашего имени, а вам в чат с ботом приходит его копия (`NOTIFY_OWNER`).

Что ещё умеет бот:

- **Не спамит.** Один и тот же шаблон бот повторяет в чате не чаще, чем раз в `REPLY_COOLDOWN_MINUTES` минут. Если собеседник пишет «привет», потом «ты тут?», ответ придёт один.
- **Не перебивает вас.** Если вы сами написали в чате, бот молчит там `OWNER_TAKEOVER_MINUTES` минут.
- **Не отвечает на стикеры.**
- **Не хранит переписку.** В базе только настройки и время последних автоответов.

## Шаблоны

Пример с комментариями лежит в `replies.example.toml`:

```toml
timezone = "Europe/Moscow"
default = ["Привет, {name}! Сейчас занят, отвечу чуть позже 🙂"]
skip = ["ок", "спасибо", "пока"]

[night]
start = 23
end = 8
replies = ["Я уже сплю 😴 Отвечу утром"]

[[rules]]
keywords = ["срочно", "важно"]
replies = ["Увидел, что срочно — постараюсь ответить как можно скорее"]
```

- `{name}` заменяется на имя собеседника.
- Если вариантов ответа несколько, бот выбирает случайный.
- Правила проверяются сверху вниз, срабатывает первое подходящее.
- Регистр и буква «ё» при сравнении не важны.
- Файл перечитывается перед каждым ответом, поэтому перезапуск не нужен. Если в файле ошибка, бот продолжит работать с прошлой версией и напишет об ошибке в лог.

## Локальный запуск

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env                  # впишите токен бота
cp replies.example.toml replies.toml  # настройте свои ответы
.venv/bin/python -m bot.main
```

1. **Токен бота.** Создайте бота у [@BotFather](https://t.me/BotFather) (`/newbot`). Затем в настройках бота включите **Business Mode**: `/mybots` → ваш бот → Bot Settings → Business Mode → Turn on.
2. **Подключение к аккаунту.** В Telegram откройте Настройки → Telegram Business → Чат-боты, укажите username бота и разрешите «Отвечать на сообщения». Там же можно выбрать, в каких чатах бот работает.

После подключения бот напишет вам в личку, что всё готово.

## Команды (в личном чате с ботом)

| Команда | Что делает |
|---|---|
| `/on`, `/off` | включить или выключить автоответы |
| `/status` | текущее состояние |
| `/ignore <chat_id>` | не отвечать в этом чате (id есть в уведомлениях) |
| `/unignore <chat_id>` | снова отвечать в этом чате |

Остальные параметры задаются в `.env`, см. `.env.example`.

## Запуск на сервере

Нужен любой VPS с Ubuntu или Debian: хватит 1 ядра и 512 МБ памяти. Публичный адрес и домен не нужны, бот сам опрашивает Telegram.

### Одной командой (Docker)

Подключитесь к серверу по SSH и выполните:

```bash
curl -fsSL https://raw.githubusercontent.com/tupamango007/telegrambott/main/deploy/install.sh | bash
```

Скрипт сам сделает всё остальное:
1. установит Docker и скачает проект в `~/telegrambott`;
2. спросит токен бота;
3. запустит контейнер, который перезапускается сам после сбоев и перезагрузки сервера.

Повторный запуск той же команды обновляет бота до свежей версии.

Полезные команды (из папки `~/telegrambott`):

| Команда | Что делает |
|---|---|
| `nano config/replies.toml` | правка шаблонов, применяется без перезапуска |
| `sudo docker compose logs -f` | логи |
| `sudo docker compose restart` | перезапуск, например после правки `.env` |
| `sudo docker compose down` | остановка |

### Без Docker (systemd)

```bash
sudo useradd -r -m -d /opt/telegrambott telegrambott
sudo -u telegrambott git clone https://github.com/tupamango007/telegrambott.git /opt/telegrambott
cd /opt/telegrambott
sudo -u telegrambott python3 -m venv .venv
sudo -u telegrambott .venv/bin/pip install -r requirements.txt
sudo -u telegrambott cp .env.example .env              # впишите токен
sudo -u telegrambott cp replies.example.toml replies.toml
sudo cp deploy/telegrambott.service /etc/systemd/system/
sudo systemctl enable --now telegrambott
journalctl -u telegrambott -f                          # логи
```

## Тесты

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```
