import asyncio
import json
import re
from datetime import datetime, timedelta

from pywebio import start_server
from pywebio.input import *
from pywebio.output import *
from pywebio.session import run_async, run_js

# Список сообщений и онлайн пользователей
chat_msgs = []
online_users = set()
muted_users = {}

# Параметры
MAX_MESSAGES_COUNT = 100
USER_DATA_FILE = "user_data.json"  # Файл для хранения данных пользователей

# Регулярное выражение для поиска URL
URL_PATTERN = re.compile(r'https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+')
MENTION_PATTERN = re.compile(r'@([a-zA-Z0-9_]+)')  # Упоминания пользователей



# Загрузка данных пользователей из JSON
def load_user_data():
    try:
        with open(USER_DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


# Сохранение данных пользователей в JSON
def save_user_data(data):
    with open(USER_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


# Функция для отправки системного сообщения
def send_system_message(message):
    """
    Добавляет системное сообщение в глобальный список и возвращает форматированный HTML
    """
    message_html = f"""
    <div style="background-color: #e0f7fa; padding: 10px; margin-bottom: 10px; border-radius: 10px; font-style: italic;">
        📢 {message}
    </div>
    """
    chat_msgs.append(("📢", message_html))

# Основная логика чата
async def main():
    global chat_msgs, muted_users

    # Загрузка данных пользователей
    user_accounts = load_user_data()

    put_markdown("## 🧊 Colander Chat | Private Demo test")

    # Регистрация/Вход
    while True:
        action = await actions("Выберите действие", buttons=["Вход", "Регистрация", "Выход"])
        if action == "Выход":
            put_text("Вы вышли из приложения!")
            return

        if action == "Регистрация":
            account = await input_group("Регистрация", [
                input("Ваше имя", name="nickname", required=True,
                      validate=lambda n: "Это имя уже занято!" if n in user_accounts else None),
                input("Пароль", name="password", type=PASSWORD, required=True),
            ])


            save_user_data(user_accounts)  # Сохранить данные после регистрации
            toast("Регистрация прошла успешно! Теперь войдите в свой аккаунт.")
        elif action == "Вход":
            account = await input_group("Вход", [
                input("Ваше имя", name="nickname", required=True),
                input("Пароль", name="password", type=PASSWORD, required=True)
            ])

            nickname = account["nickname"]
            password = account["password"]

            # Проверка на существование и корректность пароля
            if nickname not in user_accounts or user_accounts[nickname]["password"] != password:
                toast("Неверный ник или пароль!", color="error")
            else:
                is_admin = user_accounts[nickname].get("admin", False)
                break

    online_users.add(nickname)

    # Создаем контейнер для сообщений
    msg_box = output()
    put_scrollable(msg_box, height=300, keep_bottom=True)

    # Отправляем сообщение о входе пользователя (один раз)
    if is_admin:
        msg_box.append(put_html(send_system_message(f'Администратор `{nickname}` присоединился к чату!')))
    else:
        msg_box.append(put_html(send_system_message(f"`{nickname}` присоединился к чату!")))

    refresh_task = run_async(refresh_msg(nickname, msg_box))

    while True:
        # Проверяем, заблокирован ли пользователь (mute)
        if nickname in muted_users and muted_users[nickname] > datetime.now():
            toast("Вы временно заблокированы и не можете отправлять сообщения.", color="error")
            await asyncio.sleep(10000)
            continue

        data = await input_group("💭 Новое сообщение", [
            input(placeholder="Текст сообщения ...", name="msg"),
            actions(name="cmd", buttons=["Отправить", {'label': "Выйти из чата", 'type': 'cancel'}])
        ], validate=lambda m: ('msg', "Введите текст сообщения!") if m["cmd"] == "Отправить" and not m['msg'] else None)

        if data is None:
            break

        # Обработка команд администратора
        if data["msg"].startswith("/") and is_admin:
            command = data["msg"].split()
            if len(command) > 0:
                if command[0] == "/mute" and len(command) == 3:
                    username = command[1]
                    try:
                        mute_time = int(command[2])
                        mute_until = datetime.now() + timedelta(seconds=mute_time)
                        muted_users[username] = mute_until
                        msg_box.append(put_html(send_system_message(f'Пользователь `{username}` заблокирован на {mute_time} секунд.')))
                    except ValueError:
                        msg_box.append(put_html(send_system_message("Ошибка: Неверное время мьюта.")))
                elif command[0] == "/unmute" and len(command) == 2:
                    username = command[1]
                    if username in muted_users:
                        del muted_users[username]
                        msg_box.append(put_html(send_system_message(f'Пользователь `{username}` размьючен.')))
                    else:
                        msg_box.append(put_html(send_system_message(f"Ошибка: Пользователь `{username}` не был заблокирован.")))
                elif command[0] == "/help":
                    help_text = (
                        "/mute (username) (time) - заблокировать пользователя на время в секундах\n"
                        "/unmute (username) - размьютить пользователя\n"
                        "/help - показать список команд"

                    )
                    msg_box.append(put_html(send_system_message(help_text)))
                else:
                    msg_box.append(put_html(send_system_message("Ошибка: Неизвестная команда.")))
            continue

        # Проверка и обработка ссылки в сообщении
        message_text = data["msg"]

        # Заменяем упоминания на цветные
        def replace_mentions(match):
            username = match.group(1)
            if username in online_users:
                return f"<span style='color: blue'>@{username}</span>"
            return f"@{username}"

        message_text = re.sub(MENTION_PATTERN, replace_mentions, message_text)

        # Обработка ссылок
        urls = re.findall(URL_PATTERN, message_text)
        if urls:
            for url in urls:
                message_text = message_text.replace(url, f"{url}")

        # Обработка внешнего вида сообщения
        message_html = f"""
        <div style="background-color: #f0f0f0; padding: 10px; margin-bottom: 10px; border-radius: 10px; box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);">
            <strong>{nickname}</strong>: {message_text}
        </div>
        """
        msg_box.append(put_html(message_html))
        chat_msgs.append((nickname, message_html))

    refresh_task.close()
    online_users.remove(nickname)

    # Отправляем сообщение о выходе пользователя
    msg_box.append(put_html(send_system_message(f'`{nickname}` покинул чат!')))
    put_buttons(['Перезайти'], onclick=lambda btn: run_js('window.location.reload()'))
# Обновление сообщений


async def refresh_msg(nickname, msg_box):
    global chat_msgs
    last_idx = 0  # Индекс последнего обработанного сообщения
    displayed_messages = []  # Список уже отображённых сообщений

    while True:
        await asyncio.sleep(1)

        # Получаем новые сообщения
        new_msgs = chat_msgs[last_idx:]

        for sender, raw_message in new_msgs:
            if sender != nickname:  # Если сообщение не от пользователя
                # Обрабатываем упоминания
                def highlight_mentions(match):
                    mentioned_user = match.group(1)
                    if mentioned_user == nickname:
                        return f"<span style='color: blue'>@{mentioned_user}</span>"
                    return f"@{mentioned_user}"

                # Обрабатываем упоминания с помощью регулярного выражения
                personalized_message = re.sub(MENTION_PATTERN, highlight_mentions, raw_message)

                # Проверяем, чтобы сообщение не дублировалось
                if personalized_message not in displayed_messages:
                    msg_box.append(put_html(personalized_message))
                    displayed_messages.append(personalized_message)

        # Обновляем индекс последнего сообщения
        last_idx = len(chat_msgs)

        # Урезаем список сообщений, чтобы не перегружать память
        if len(chat_msgs) > MAX_MESSAGES_COUNT:
            chat_msgs = chat_msgs[-MAX_MESSAGES_COUNT:]



if __name__ == "__main__":
    start_server(main, debug=True, port=80, cdn=True, host='0.0.0.0',)


