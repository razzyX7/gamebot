
import asyncio
import logging
import sqlite3
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.methods import set_my_commands
from aiogram.types import BotCommand
from aiogram.client.default import DefaultBotProperties
import random
from datetime import datetime

# --- Конфигурация ---
BOT_TOKEN = "8274176969:AAGjR6eHgmTZOROhV1KeswtUdBIOegUIHMM"  # Замените на токен вашего бота
ADMIN_ID = 8469018212  # Замените на ID администратора
GAME_FIELD_SIZE = 5
BOMB_COUNT = 5
START_BALANCE = 1500
CURRENCY = "Moon Game | BOT"
WIN_MULTIPLIERS = [1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 3.5, 3.75, 4.0, 4.5, 4.75]
BLACKJACK_WIN_MULTIPLIER = 1.5
GRANNY_GAME_REWARD = 560
GRANNY_GAME_PENALTY = 450  # Штраф за неправильный провод
DATABASE_NAME = "bot_hub_games.db"

# --- Настройка логирования ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Инициализация базы данных ---
def init_db():
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()

    # Создаем таблицу пользователей
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        balance REAL DEFAULT 1500,
        games_played INTEGER DEFAULT 0,
        best_score INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Создаем таблицу для статистики игры "Разминируй бабку"
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS granny_games (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        played INTEGER DEFAULT 0,
        won INTEGER DEFAULT 0,
        FOREIGN KEY (user_id) REFERENCES users (user_id)
    )
    """)

    # Создаем таблицу для истории транзакций
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        amount REAL,
        type TEXT,
        description TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (user_id)
    )
    """)

    conn.commit()
    conn.close()

init_db()

# --- Функции для работы с базой данных ---
def get_user(user_id: int):
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()

    if user:
        cursor.execute('SELECT played, won FROM granny_games WHERE user_id = ?', (user_id,))
        granny_stats = cursor.fetchone()
        if not granny_stats:
            cursor.execute('INSERT INTO granny_games (user_id, played, won) VALUES (?, 0, 0)', (user_id,))
            conn.commit()
            granny_stats = (0, 0)

        user_data = {
            "user_id": user[0],
            "username": user[1],
            "balance": user[2],
            "games_played": user[3],
            "best_score": user[4],
            "created_at": user[5],
            "granny_games": {
                "played": granny_stats[0],
                "won": granny_stats[1]
            }
        }
    else:
        user_data = None

    conn.close()
    return user_data

def create_user(user_id: int, username: str):
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()

    cursor.execute('INSERT INTO users (user_id, username, balance) VALUES (?, ?, ?)',
                  (user_id, username, START_BALANCE))
    cursor.execute('INSERT INTO granny_games (user_id, played, won) VALUES (?, 0, 0)', (user_id,))
    cursor.execute('INSERT INTO transactions (user_id, amount, type, description) VALUES (?, ?, ?, ?)',
                  (user_id, START_BALANCE, "deposit", "Initial balance"))

    conn.commit()
    conn.close()

def update_user_balance(user_id: int, amount: float, transaction_type: str, description: str = ""):
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()

    cursor.execute('UPDATE users SET balance = balance + ? WHERE user_id = ?', (amount, user_id))
    cursor.execute('INSERT INTO transactions (user_id, amount, type, description) VALUES (?, ?, ?, ?)',
                  (user_id, amount, transaction_type, description))

    conn.commit()
    conn.close()

def update_user_stats(user_id: int, games_played: int = 0, best_score: int = 0):
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()

    if games_played:
        cursor.execute('UPDATE users SET games_played = games_played + ? WHERE user_id = ?',
                      (games_played, user_id))
    if best_score:
        cursor.execute('UPDATE users SET best_score = ? WHERE user_id = ? AND (best_score < ? OR best_score = 0)',
                      (best_score, user_id, best_score))

    conn.commit()
    conn.close()

def update_granny_stats(user_id: int, won: bool = False):
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()

    if won:
        cursor.execute('UPDATE granny_games SET played = played + 1, won = won + 1 WHERE user_id = ?', (user_id,))
    else:
        cursor.execute('UPDATE granny_games SET played = played + 1 WHERE user_id = ?', (user_id,))

    conn.commit()
    conn.close()

def get_top_players(limit: int = 10):
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()

    cursor.execute("""
    SELECT user_id, username, balance
    FROM users
    ORDER BY balance DESC
    LIMIT ?
    """, (limit,))

    top_players = cursor.fetchall()
    conn.close()
    return top_players

# --- FSM States ---
class UserState(StatesGroup):
    menu = State()
    playing = State()
    admin_panel = State()
    admin_give = State()
    admin_take = State()
    admin_profile = State()
    betting = State()
    blackjack = State()
    blackjack_betting = State()
    granny_game = State()

# --- Глобальные переменные для хранения состояния игр ---
game_states = {}

# --- Функции для Сапера ---
def create_game_field(size: int, bomb_count: int) -> list[list[str]]:
    field = [[' ' for _ in range(size)] for _ in range(size)]
    bombs_placed = 0
    while bombs_placed < bomb_count:
        x = random.randint(0, size - 1)
        y = random.randint(0, size - 1)
        if field[x][y] != 'B':
            field[x][y] = 'B'
            bombs_placed += 1
    return field

def display_field(field: list[list[str]], revealed: set[(int, int)], current_multiplier: float) -> InlineKeyboardMarkup:
    size = len(field)
    keyboard = []
    info_row = [
        InlineKeyboardButton(text=f"Множитель: {current_multiplier:.2f}x", callback_data="noop"),
        InlineKeyboardButton(text="💰 Забрать выигрыш 💰", callback_data="cashout")
    ]
    keyboard.append(info_row)

    for x in range(size):
        row = []
        for y in range(size):
            if (x, y) in revealed:
                cell = " " if field[x][y] != 'B' else "💣"
            else:
                cell = '■'
            row.append(InlineKeyboardButton(text=cell, callback_data=f"click_{x}_{y}"))
        keyboard.append(row)
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def check_win(field: list[list[str]], revealed: set[(int, int)]) -> bool:
    size = len(field)
    bomb_count = sum(row.count('B') for row in field)
    return len(revealed) == size * size - bomb_count

# --- Функции для Блекджека ---
def deal_card():
    return random.randint(1, 11)

def display_blackjack_hand(cards: list[int], hide_one: bool = False) -> str:
    card_names = {
        1: "Туз",
        11: "Валет",
        12: "Дама",
        13: "Король"
    }
    if hide_one:
        displayed_cards = ["?"] + [card_names.get(card, str(card)) if card <= 10 else card_names.get(card, str(card)) for card in cards[1:]]
        return ", ".join(displayed_cards)
    else:
        displayed_cards = [card_names.get(card, str(card)) if card <= 10 else card_names.get(card, str(card)) for card in cards]
        return ", ".join(displayed_cards)

# --- Функции для Разминируй бабку ---
async def start_granny_game(message: types.Message, state: FSMContext):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔴 Красный провод", callback_data="granny_red")],
        [InlineKeyboardButton(text="🔵 Синий провод", callback_data="granny_blue")],
        [InlineKeyboardButton(text="🟢 Зеленый провод", callback_data="granny_green")],
        [InlineKeyboardButton(text="🟡 Желтый провод", callback_data="granny_yellow")]
    ])

    await message.reply(
        "👵 Бабка случайно села на мину! Нужно перерезать правильный провод, чтобы разминировать её.\n"
        "Выбери провод для перерезания:",
        reply_markup=keyboard
    )
    await state.set_state(UserState.granny_game)

async def handle_granny_game(query: types.CallbackQuery, state: FSMContext):
    user_id = query.from_user.id
    wire_color = query.data.split('_')[1]
    correct_wire = random.choice(["red", "blue", "green", "yellow"])

    if wire_color == correct_wire:
        reward = GRANNY_GAME_REWARD
        update_user_balance(user_id, reward, "win", "Granny game reward")
        update_granny_stats(user_id, won=True)

        user = get_user(user_id)
        await query.answer("✅ Успех! Бабка спасена!")
        await query.message.edit_text(
            f"🎉 Ты перерезал правильный провод ({correct_wire}) и спас бабку!\n"
            f"💸 Ты получаешь {reward} {CURRENCY}!\n"
            f"💰 Твой баланс: {user['balance']} {CURRENCY}",
            reply_markup=None
        )
    else:
        # Снимаем штраф за неправильный выбор
        update_user_balance(user_id, -GRANNY_GAME_PENALTY, "penalty", "Incorrect wire in Granny game")
        update_granny_stats(user_id)

        user = get_user(user_id)
        await query.answer("💥 Бах! Бабка взорвалась!")
        await query.message.edit_text(
            f"💣 Ты перерезал не тот провод! Бабка взорвалась!\n"
            f"Правильный провод был: {correct_wire}\n"
            f"💸 С тебя списано {GRANNY_GAME_PENALTY} {CURRENCY} за неудачную попытку.\n"
            f"💰 Твой баланс: {user['balance']} {CURRENCY}\n\n"
            f"Попробуй ещё раз, может повезёт!",
            reply_markup=None
        )

    await state.set_state(UserState.menu)

# --- Клавиатуры ---
def get_menu_keyboard(is_admin: bool = False) -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton(text="🕹️ Играть в Сапер"), KeyboardButton(text="🃏 21 Очко")],
        [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="🏆 Топ игроков")],
        [KeyboardButton(text="👵 Разминируй бабку")]
    ]
    if is_admin:
        keyboard.append([KeyboardButton(text="⚙️ Админ-панель")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_admin_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton(text="➕ Выдать Коины"), KeyboardButton(text="➖ Забрать Коины")],
        [KeyboardButton(text="👁️ Посмотреть профиль")],
        [KeyboardButton(text="🔙 Назад в меню")],
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

# --- Обработчики команд ---
async def start_command(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name or "Player"

    if not get_user(user_id):
        create_user(user_id, username)
        await message.reply(f"🎉 Приветствуем нового игрока! На ваш счет зачислено {START_BALANCE} {CURRENCY}!")

    await state.set_state(UserState.menu)
    await message.reply("👋 Добро пожаловать в Moon Game | BOT Games!",
                       reply_markup=get_menu_keyboard(user_id == ADMIN_ID))

async def menu_handler(message: types.Message, state: FSMContext):
    if message.text == "🕹️ Играть в Сапер":
        await message.reply("💰 Какую ставку вы хотите сделать? (Введите число)")
        await state.set_state(UserState.betting)
    elif message.text == "🃏 21 Очко":
        await start_blackjack(message, state)
    elif message.text == "👤 Профиль":
        await show_profile(message, state)
    elif message.text == "🏆 Топ игроков":
        await show_top_players(message, state)
    elif message.text == "👵 Разминируй бабку":
        await start_granny_game(message, state)
    elif message.text == "⚙️ Админ-панель" and message.from_user.id == ADMIN_ID:
        await admin_panel(message, state)
    else:
        await message.reply("🤔 Пожалуйста, выберите опцию из меню.")

# --- Обработчики для Сапера ---
async def betting_handler(message: types.Message, state: FSMContext):
    try:
        bet = float(message.text)
        user_id = message.from_user.id
        user = get_user(user_id)

        if not user:
            await message.reply("⚠️ Пожалуйста, начните с команды /start")
            return

        if bet <= 0:
            await message.reply("⛔ Ставка должна быть положительной.")
            return

        if bet > user['balance']:
            await message.reply(f"💸 Недостаточно средств. Ваш баланс: {user['balance']} {CURRENCY}")
            await state.set_state(UserState.menu)
            return

        await start_new_game(message, state, bet)

    except ValueError:
        await message.reply("⌨️ Пожалуйста, введите число.")

async def start_new_game(message: types.Message, state: FSMContext, bet: float):
    user_id = message.from_user.id
    field = create_game_field(GAME_FIELD_SIZE, BOMB_COUNT)
    game_states[user_id] = {
        "field": field,
        "revealed": set(),
        "game_over": False,
        "bet": bet,
        "multiplier_index": 0
    }

    update_user_balance(user_id, -bet, "bet", "Minesweeper game bet")

    await state.set_state(UserState.playing)
    multiplier = WIN_MULTIPLIERS[game_states[user_id]["multiplier_index"]]
    await message.reply(
        f"🚀 Начинаем игру! Ваша ставка: {bet} {CURRENCY}. Осторожно, бомбы! 💣",
        reply_markup=display_field(field, game_states[user_id]["revealed"], multiplier)
    )

async def handle_callback_query(query: types.CallbackQuery, state: FSMContext):
    user_id = query.from_user.id
    user = get_user(user_id)

    if not user:
        await query.answer("⚠️ Пожалуйста, начните с команды /start")
        return

    if query.data.startswith("granny_"):
        await handle_granny_game(query, state)  # Обработка Granny game callback
        return

    if user_id not in game_states or game_states[user_id]["game_over"]:
        await query.answer("Игра окончена. Начните новую игру.")
        return

    field = game_states[user_id]["field"]
    revealed = game_states[user_id]["revealed"]
    bet = game_states[user_id]["bet"]
    multiplier_index = game_states[user_id]["multiplier_index"]
    current_multiplier = WIN_MULTIPLIERS[multiplier_index]

    if query.data == "cashout":
        win_amount = bet * current_multiplier
        update_user_balance(user_id, win_amount, "win", "Minesweeper game win")
        update_user_stats(user_id, games_played=1)
        game_states[user_id]["game_over"] = True

        history_message = (
            "📜 <b>История Moon Game | BOT Games:</b>\n\n"
            "Moon Game | BOT Games был создан группой энтузиастов, увлеченных идеей объединения развлечений и инновационных технологий. "
            "Наша цель - предоставить игрокам захватывающий опыт и возможность испытать удачу в безопасной и честной среде. "
            "Мы постоянно развиваемся и добавляем новые игры, чтобы каждый мог найти что-то по своему вкусу. Спасибо, что вы с нами!"
        )

        await query.message.edit_text("✅ Игра окончена!", reply_markup=None)
        await query.answer("Вы забрали выигрыш!")
        await query.message.answer(
            f"💰 Вы забрали {win_amount:.2f} {CURRENCY}! \n💸 Ваш баланс: {user['balance']:.2f} {CURRENCY}.\n\n" + history_message,
            parse_mode="HTML",
            reply_markup=get_menu_keyboard(user_id == ADMIN_ID)
        )

        await state.set_state(UserState.menu)
        return

    x, y = map(int, query.data[6:].split('_'))

    if (x, y) in revealed:
        await query.answer("Эта клетка уже открыта.")
        return

    revealed.add((x, y))

    if field[x][y] == 'B':
        await query.answer("💥 Бум! Игра окончена.")
        game_states[user_id]["game_over"] = True
        update_user_stats(user_id, games_played=1)

        history_message = (
            "📜 <b>История Moon Game | BOT Games:</b>\n\n"
            "Moon Game | BOT Games был создан группой энтузиастов, увлеченных идеей объединения развлечений и инновационных технологий. "
            "Наша цель - предоставить игрокам захватывающий опыт и возможность испытать удачу в безопасной и честной среде. "
            "Мы постоянно развиваемся и добавляем новые игры, чтобы каждый мог найти что-то по своему вкусу. Спасибо, что вы с нами!"
        )

        await query.message.edit_text("💣 Вы наткнулись на бомбу! Игра окончена.", reply_markup=None)
        await state.set_state(UserState.menu)

        user = get_user(user_id)
        if user['balance'] < 0:
            update_user_balance(user_id, -user['balance'], "adjustment", "Balance reset to zero")
            await query.message.answer(
                f"😭 Игра окончена. \n💸 Ваш баланс: 0 {CURRENCY}. \n⚠️ Баланс обнулен.\n\n"+history_message,
                reply_markup=get_menu_keyboard(user_id == ADMIN_ID),
                parse_mode="HTML"
            )
        else:
            await query.message.answer(
                f"😭 Игра окончена. \n💸 Ваш баланс: {user['balance']} {CURRENCY}.\n\n"+history_message,
                reply_markup=get_menu_keyboard(user_id == ADMIN_ID),
                parse_mode="HTML"
            )
    else:
        if multiplier_index < len(WIN_MULTIPLIERS) - 1:
            multiplier_index += 1
            game_states[user_id]["multiplier_index"] = multiplier_index
            await query.answer(f"✅ Клик! \n📈 Множитель увеличен до {WIN_MULTIPLIERS[multiplier_index]:.2f}x")
        else:
            await query.answer("🔥 Клик! \n🏆 Достигнут максимальный множитель!")

        current_multiplier = WIN_MULTIPLIERS[multiplier_index]
        field_markup = display_field(field, revealed, current_multiplier)
        await query.message.edit_reply_markup(reply_markup=field_markup)

# --- Обработчики для Блекджека ---
async def start_blackjack(message: types.Message, state: FSMContext):
    await message.reply("💰 Какую ставку вы хотите сделать для игры в 21 очко? (Введите число)")
    await state.set_state(UserState.blackjack_betting)

async def blackjack_betting_handler(message: types.Message, state: FSMContext):
    try:
        bet = float(message.text)
        user_id = message.from_user.id
        user = get_user(user_id)

        if not user:
            await message.reply("⚠️ Пожалуйста, начните с команды /start")
            return

        if bet <= 0:
            await message.reply("⛔ Ставка должна быть положительной.")
            return

        if bet > user['balance']:
            await message.reply(f"💸 Недостаточно средств. Ваш баланс: {user['balance']} {CURRENCY}")
            await state.set_state(UserState.menu)
            return

        update_user_balance(user_id, -bet, "bet", "Blackjack game bet")

        player_cards = [deal_card(), deal_card()]
        dealer_cards = [deal_card(), deal_card()]

        game_states[user_id] = {
            "blackjack_player_cards": player_cards,
            "blackjack_dealer_cards": dealer_cards,
            "blackjack_bet": bet,
            "blackjack_game_over": False
        }

        player_total = sum(player_cards)
        dealer_total = sum(dealer_cards)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Взять карту", callback_data="blackjack_hit")],
            [InlineKeyboardButton(text="Остановиться", callback_data="blackjack_stand")]
        ])

        await state.set_state(UserState.blackjack)
        formatted_player_cards = display_blackjack_hand(player_cards)
        formatted_dealer_cards = display_blackjack_hand(dealer_cards, hide_one=True)
        await message.reply(
            f"🃏 Игра '21 очко' началась!\n\n"
            f"Ваши карты: {formatted_player_cards} (Сумма: {player_total})\n"
            f"Карты дилера: {formatted_dealer_cards} (Сумма: {dealer_total if dealer_total <=10 else '?'})\n\n"
            f"Что делаем?",
            reply_markup=keyboard
        )

    except ValueError:
        await message.reply("⌨️ Пожалуйста, введите число.")

async def blackjack_handler(query: types.CallbackQuery, state: FSMContext):
    user_id = query.from_user.id
    user = get_user(user_id)

    if not user:
        await query.answer("⚠️ Пожалуйста, начните с команды /start")
        return

    player_cards = game_states[user_id]["blackjack_player_cards"]
    dealer_cards = game_states[user_id]["blackjack_dealer_cards"]
    bet = game_states[user_id]["blackjack_bet"]

    if query.data == "blackjack_hit":
        player_cards.append(deal_card())
        game_states[user_id]["blackjack_player_cards"] = player_cards
        player_total = sum(player_cards)

        if player_total > 21:
            await query.answer("Перебор!")
            formatted_player_cards = display_blackjack_hand(player_cards)
            formatted_dealer_cards = display_blackjack_hand(dealer_cards)
            await query.message.edit_text(
                f"🃏 У вас перебор! (Сумма: {player_total})\n"
                f"Карты дилера: {formatted_dealer_cards} (Сумма: {sum(dealer_cards)})\n\n"
                f"Вы проиграли {bet:.2f} {CURRENCY}!",
                reply_markup=None
            )
            update_user_stats(user_id, games_played=1)
            await state.set_state(UserState.menu)
        else:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Взять карту", callback_data="blackjack_hit")],
                [InlineKeyboardButton(text="Остановиться", callback_data="blackjack_stand")]
            ])
            formatted_player_cards = display_blackjack_hand(player_cards)
            formatted_dealer_cards = display_blackjack_hand(dealer_cards, hide_one=True)
            await query.answer("Взяли карту.")
            await query.message.edit_text(
                f"🃏 Ваши карты: {formatted_player_cards} (Сумма: {player_total})\n"
                f"Карты дилера: {formatted_dealer_cards} (Сумма: {sum(dealer_cards) if sum(dealer_cards) <= 10 else '?'})\n\n"
                f"Что делаем?",
                reply_markup=keyboard
            )

    elif query.data == "blackjack_stand":
        player_total = sum(player_cards)
        dealer_total = sum(dealer_cards)

        while dealer_total < 17:
            dealer_cards.append(deal_card())
            dealer_total = sum(dealer_cards)
            game_states[user_id]["blackjack_dealer_cards"] = dealer_cards

        if dealer_total > 21 or player_total > dealer_total:
            win_amount = bet * BLACKJACK_WIN_MULTIPLIER
            update_user_balance(user_id, win_amount, "win", "Blackjack game win")
            update_user_stats(user_id, games_played=1)
            await query.answer("Вы выиграли!")
            formatted_player_cards = display_blackjack_hand(player_cards)
            formatted_dealer_cards = display_blackjack_hand(dealer_cards)
            user = get_user(user_id)
            await query.message.edit_text(
                f"🎉 Вы выиграли! (Сумма: {player_total})\n"
                f"Карты дилера: {formatted_dealer_cards} (Сумма: {dealer_total})\n\n"
                f"Вы получили {win_amount:.2f} {CURRENCY}!\n"
                f"💰 Ваш баланс: {user['balance']:.2f} {CURRENCY}",
                reply_markup=None
            )

        elif player_total == dealer_total:
            update_user_balance(user_id, bet, "refund", "Blackjack game draw")
            update_user_stats(user_id, games_played=1)
            await query.answer("Ничья!")
            formatted_player_cards = display_blackjack_hand(player_cards)
            formatted_dealer_cards = display_blackjack_hand(dealer_cards)
            user = get_user(user_id)
            await query.message.edit_text(
                f"🤝 Ничья! (Сумма: {player_total})\n"
                f"Карты дилера: {formatted_dealer_cards} (Сумма: {dealer_total})\n\n"
                f"Ставка возвращена.\n"
                f"💰 Ваш баланс: {user['balance']:.2f} {CURRENCY}",
                reply_markup=None
            )
        else:
            update_user_stats(user_id, games_played=1)
            await query.answer("Вы проиграли.")
            formatted_player_cards = display_blackjack_hand(player_cards)
            formatted_dealer_cards = display_blackjack_hand(dealer_cards)
            user = get_user(user_id)
            await query.message.edit_text(
                f"😭 Вы проиграли! (Сумма: {player_total})\n"
                f"Карты дилера: {formatted_dealer_cards} (Сумма: {dealer_total})\n\n"
                f"Вы потеряли {bet:.2f} {CURRENCY}!\n"
                f"💰 Ваш баланс: {user['balance']:.2f} {CURRENCY}",
                reply_markup=None
            )

        await state.set_state(UserState.menu)

# --- Профиль и топ игроков ---
async def show_profile(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    user = get_user(user_id)

    if user:
        message_text = (
            "<b>📊 Ваш профиль:</b>\n\n"
            f"<b>💰 Баланс:</b> {user['balance']} {CURRENCY}\n"
            f"<b>🎮 Сыграно игр:</b> {user['games_played']}\n"
            f"<b>🏆 Лучший результат в 'Сапере':</b> {user['best_score']}\n"
            f"<b>👵 Игр 'Разминируй бабку':</b> {user['granny_games']['played']} (побед: {user['granny_games']['won']})\n\n"
            "✨ Удачи в новых играх! ✨"
        )
        await message.reply(
            message_text,
            parse_mode="HTML",
            reply_markup=get_menu_keyboard(message.from_user.id == ADMIN_ID)
        )
    else:
        await message.reply(
            "⚠️ Произошла ошибка при получении данных профиля.",
            reply_markup=get_menu_keyboard(message.from_user.id == ADMIN_ID)
        )
    await state.set_state(UserState.menu)

async def show_top_players(message: types.Message, state: FSMContext):
    top_players = get_top_players()
    message_text = "<b>🏆 Топ игроков:</b>\n\n"
    for i, (user_id, username, balance) in enumerate(top_players):
        message_text += f"<b>{i + 1}.</b> {username}: {balance} {CURRENCY}\n"
    await message.reply(
        message_text,
        parse_mode="HTML",
        reply_markup=get_menu_keyboard(message.from_user.id == ADMIN_ID)
    )
    await state.set_state(UserState.menu)

# --- Админ-панель ---
async def admin_panel(message: types.Message, state: FSMContext):
    if message.from_user.id == ADMIN_ID:
        await message.reply("⚙️ Админ-панель", reply_markup=get_admin_keyboard())
        await state.set_state(UserState.admin_panel)
    else:
        await message.reply(
            "⛔ У вас нет прав доступа к админ-панели.",
            reply_markup=get_menu_keyboard(message.from_user.id == ADMIN_ID)
        )

async def admin_handler(message: types.Message, state: FSMContext):
    if message.text == "➕ Выдать Коины":
        await message.reply("⌨️ Введите ID пользователя и сумму через пробел (например, 123456789 100)")
        await state.set_state(UserState.admin_give)
    elif message.text == "➖ Забрать Коины":
        await message.reply("⌨️ Введите ID пользователя и сумму через пробел (например, 123456789 50)")
        await state.set_state(UserState.admin_take)
    elif message.text == "👁️ Посмотреть профиль":
        await message.reply("⌨️ Введите ID пользователя для просмотра профиля")
        await state.set_state(UserState.admin_profile)
    elif message.text == "🔙 Назад в меню":
        await start_command(message, state)
    else:
        await message.reply("🤔 Неизвестная команда админ-панели.")

async def admin_give_handler(message: types.Message, state: FSMContext):
    try:
        user_id, amount = map(float, message.text.split())
        user_id = int(user_id)
        amount = float(amount)
        
        user = get_user(user_id)
        if user:
            update_user_balance(user_id, amount, "admin_deposit", f"Admin deposit by {message.from_user.id}")
            await message.reply(
                f"✅ Успешно начислено {amount} {CURRENCY} пользователю {user_id}.", 
                reply_markup=get_admin_keyboard()
            )
            try:
                bot = Bot.get_current()
                await bot.send_message(user_id, f"🎉 Администратор начислил вам {amount} {CURRENCY}!")
            except Exception as e:
                logger.error(f"Не удалось отправить уведомление пользователю {user_id}: {e}")
        else:
            await message.reply("⛔ Пользователь не найден.", reply_markup=get_admin_keyboard())
    except ValueError:
        await message.reply(
            "⚠️ Неверный формат. Введите ID пользователя и сумму через пробел (например, 123456789 100)", 
            reply_markup=get_admin_keyboard()
        )
    await state.set_state(UserState.admin_panel)

async def admin_take_handler(message: types.Message, state: FSMContext):
    try:
        user_id, amount = map(float, message.text.split())
        user_id = int(user_id)
        amount = float(amount)
        
        user = get_user(user_id)
        if user:
            update_user_balance(user_id, -amount, "admin_withdrawal", f"Admin withdrawal by {message.from_user.id}")
            await message.reply(
                f"✅ Успешно снято {amount} {CURRENCY} у пользователя {user_id}.", 
                reply_markup=get_admin_keyboard()
            )
        else:
            await message.reply("⛔ Пользователь не найден.", reply_markup=get_admin_keyboard())
    except ValueError:
        await message.reply(
            "⚠️ Неверный формат. Введите ID пользователя и сумму через пробел (например, 123456789 50)", 
            reply_markup=get_admin_keyboard()
        )
    await state.set_state(UserState.admin_panel)

async def admin_profile_handler(message: types.Message, state: FSMContext):
    try:
        user_id = int(message.text)
        user = get_user(user_id)
        
        if user:
            message_text = (
                f"<b>👤 Профиль пользователя {user_id}:</b>\n\n"
                f"<b>💰 Баланс:</b> {profile['balance']} {CURRENCY}\n"
                f"<b>🎮 Сыграно игр:</b> {profile['games_played']}\n"
                f"<b>🏆 Лучший результат в 'Сапере':</b> {profile['best_score']}\n"
                f"<b>👵 Игр 'Разминируй бабку':</b> {granny_stats['played']} (побед: {granny_stats['won']})"
            )
            await message.reply(message_text, parse_mode="HTML", reply_markup=get_admin_keyboard())
        else:
            await message.reply("⛔ Пользователь не найден.", reply_markup=get_admin_keyboard())
    except ValueError:
        await message.reply("⚠️ Неверный формат. Введите ID пользователя (например, 123456789)", reply_markup=get_admin_keyboard())
    await state.set_state(UserState.admin_panel)

# --- Регистрация обработчиков ---
def register_handlers(dp: Dispatcher):
    dp.message.register(start_command, CommandStart(), StateFilter(None))
    dp.message.register(menu_handler, StateFilter(UserState.menu))
    dp.message.register(betting_handler, StateFilter(UserState.betting))
    dp.callback_query.register(handle_callback_query, StateFilter(UserState.playing))
    dp.message.register(show_profile, F.text == "👤 Профиль", StateFilter(UserState.menu))
    dp.message.register(show_top_players, F.text == "🏆 Топ игроков", StateFilter(UserState.menu))
    dp.message.register(admin_panel, F.text == "⚙️ Админ-панель", StateFilter(UserState.menu), F.from_user.id == ADMIN_ID)
    dp.message.register(admin_handler, StateFilter(UserState.admin_panel), F.from_user.id == ADMIN_ID)
    dp.message.register(admin_give_handler, StateFilter(UserState.admin_give), F.from_user.id == ADMIN_ID)
    dp.message.register(admin_take_handler, StateFilter(UserState.admin_take), F.from_user.id == ADMIN_ID)
    dp.message.register(admin_profile_handler, StateFilter(UserState.admin_profile), F.from_user.id == ADMIN_ID)
    dp.message.register(blackjack_betting_handler, StateFilter(UserState.blackjack_betting))
    dp.callback_query.register(blackjack_handler, StateFilter(UserState.blackjack))
    dp.message.register(start_blackjack, F.text == "🃏 21 Очко", StateFilter(UserState.menu))
    dp.message.register(start_granny_game, F.text == "👵 Разминируй бабку 18+", StateFilter(UserState.menu))
    dp.callback_query.register(handle_granny_game, StateFilter(UserState.granny_game))

# --- Запуск бота ---
async def main():
    storage = MemoryStorage()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=storage)
    register_handlers(dp)

    commands = [
        BotCommand(command="start", description="Начать работу с ботом"),
    ]
    await bot.set_my_commands(commands=commands)

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())