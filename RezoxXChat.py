# -*- coding: utf-8 -*-
# meta developer: @Rezoxss
# scope: hikka_only

from .. import loader, utils
import logging
import time
import asyncio
import re
from typing import List, Dict, Optional, Tuple
from telethon.tl.types import Message, User, ChannelParticipantsAdmins, ChatBannedRights, Channel
from telethon.tl.functions.channels import EditBannedRequest, EditPhotoRequest, TogglePreHistoryHiddenRequest
from telethon.tl.functions.messages import EditChatDefaultBannedRightsRequest
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

@loader.tds
class RezoChatMod(loader.Module):
    """RezoChat - премиум админ-панель с улучшенным анти-рейдом"""
    
    strings = {
        "name": "RezoChat",
        "version": "RezoChat v17.0.0 by @Rezoxss",
        "not_admin": "❌ Требуются права администратора",
        "no_reply": "❌ Ответь на сообщение",
        "no_user": "❌ Укажи пользователя",
        "success": "✅ Успешно выполнено",
        "error": "❌ Ошибка выполнения",
        "no_fed": "❌ Федерация не найдена",
        "user_not_found": "❌ Пользователь не найден",
        "no_warns": "⚠️ Предупреждений нет",
        "chat_locked": "🔒 Чат закрыт от рейда",
        "chat_unlocked": "🔓 Чат открыт",
        "raid_stopped": "🛡️ Рейд остановлен"
    }

    def __init__(self):
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "log_chat",
                None,
                "ID чата для логов",
                validator=loader.validators.Integer()
            ),
            loader.ConfigValue(
                "warn_limit",
                3,
                "Лимит предупреждений перед баном",
                validator=loader.validators.Integer(minimum=1, maximum=10)
            ),
            loader.ConfigValue(
                "anti_raid",
                True,
                "Включить анти-рейд систему",
                validator=loader.validators.Boolean()
            ),
            loader.ConfigValue(
                "raid_timeout",
                60,
                "Таймаут анти-рейда (секунды)",
                validator=loader.validators.Integer(minimum=10, maximum=300)
            ),
            loader.ConfigValue(
                "raid_threshold", 
                500,
                "Порог пользователей для анти-рейда",
                validator=loader.validators.Integer(minimum=2, maximum=500)
            ),
            loader.ConfigValue(
                "lock_on_raid",
                True,  # НОВАЯ ФУНКЦИЯ: закрывать чат при рейде
                "Закрывать чат при обнаружении рейда",
                validator=loader.validators.Boolean()
            ),
            loader.ConfigValue(
                "auto_unlock",
                300,
                "Авто-разблокировка чата (секунды)",
                validator=loader.validators.Integer(minimum=60, maximum=3600)
            ),
            loader.ConfigValue(
                "premium_stickers",
                True,
                "Использовать премиум стикеры для действий",
                validator=loader.validators.Boolean()
            )
        )
        self.warns = {}
        self.federations = {}
        self.notes = {}
        self.last_joins = {}
        self.fed_admins = {}
        self.fed_bans = {}
        self.locked_chats = set()
        self.premium_stickers = {
            "ban": "CAACAgIAAxkBAAE",  # Премиум стикер бана
            "kick": "CAACAgIAAxkBAAE",  # Премиум стикер кика
            "mute": "CAACAgIAAxkBAAE",  # Премиум стикер мута
            "warn": "CAACAgIAAxkBAAE",  # Премиум стикер варна
            "success": "CAACAgIAAxkBAAE"  # Премиум стикер успеха
        }

    async def client_ready(self, client, db):
        self._client = client

    def get_premium_sticker(self, action: str) -> Optional[str]:
        """Возвращает премиум стикер для действия"""
        if self.config["premium_stickers"]:
            return self.premium_stickers.get(action)
        return None

    async def send_action_message(self, message: Message, action: str, username: str):
        """Отправляет сообщение о действии со стикером"""
        action_messages = {
            "ban": f"🔨 {username} был забанен.",
            "kick": f"🍃 {username} был кикнут.",  # Изменено на "кикнут"
            "mute": f"🔇 {username} был замучен.",
            "warn": f"⚠️ {username} получил предупреждение.",
            "unban": f"✅ {username} был разбанен.",
            "unmute": f"✅ {username} был размучен."
        }
        
        sticker = self.get_premium_sticker(action)
        if sticker:
            await message.reply(file=sticker)
        
        await message.reply(action_messages.get(action, f"✅ Действие выполнено для {username}"))

    async def lock_chat(self, chat_id: int):
        """Закрывает чат от вступлений"""
        try:
            # Закрываем историю чата
            await self._client(TogglePreHistoryHiddenRequest(
                channel=await self._client.get_entity(chat_id),
                enabled=True
            ))
            
            # Устанавливаем ограничения на отправку сообщений
            banned_rights = ChatBannedRights(
                until_date=None,
                send_messages=True,
                send_media=True,
                send_stickers=True,
                send_gifs=True,
                send_games=True,
                send_inline=True,
                send_polls=True,
                change_info=True,
                invite_users=True,
                pin_messages=True,
            )
            
            await self._client(EditChatDefaultBannedRightsRequest(
                peer=await self._client.get_entity(chat_id),
                banned_rights=banned_rights
            ))
            
            self.locked_chats.add(chat_id)
            logger.info(f"Чат {chat_id} закрыт от рейда")
            
            # Авто-разблокировка через указанное время
            if self.config["auto_unlock"] > 0:
                asyncio.create_task(self.auto_unlock_chat(chat_id))
                
        except Exception as e:
            logger.error(f"Ошибка блокировки чата: {e}")

    async def auto_unlock_chat(self, chat_id: int):
        """Автоматически разблокирует чат через время"""
        await asyncio.sleep(self.config["auto_unlock"])
        await self.unlock_chat(chat_id)

    async def unlock_chat(self, chat_id: int):
        """Разблокирует чат"""
        try:
            # Открываем историю чата
            await self._client(TogglePreHistoryHiddenRequest(
                channel=await self._client.get_entity(chat_id),
                enabled=False
            ))
            
            # Снимаем ограничения
            banned_rights = ChatBannedRights(
                until_date=None,
                send_messages=False,
                send_media=False,
                send_stickers=False,
                send_gifs=False,
                send_games=False,
                send_inline=False,
                send_polls=False,
                change_info=False,
                invite_users=False,
                pin_messages=False,
            )
            
            await self._client(EditChatDefaultBannedRightsRequest(
                peer=await self._client.get_entity(chat_id),
                banned_rights=banned_rights
            ))
            
            self.locked_chats.discard(chat_id)
            logger.info(f"Чат {chat_id} разблокирован")
            
        except Exception as e:
            logger.error(f"Ошибка разблокировки чата: {e}")

    # НОВАЯ ФУНКЦИЯ: Умный анти-рейд
    @loader.command()
    async def smartraid(self, message):
        """Умная система анти-рейда - .smartraid <on/off/status>"""
        args = utils.get_args_raw(message).lower()
        
        if not args:
            status = "✅ Вкл" if self.config["anti_raid"] else "❌ Выкл"
            lock_status = "✅ Вкл" if self.config["lock_on_raid"] else "❌ Выкл"
            
            status_text = (
                f"🛡️ Умный анти-рейд:\n"
                f"• Система: {status}\n"
                f"• Блокировка чата: {lock_status}\n"
                f"• Порог: {self.config['raid_threshold']} users\n"
                f"• Таймаут: {self.config['raid_timeout']}s\n"
                f"• Авто-разблокировка: {self.config['auto_unlock']}s\n\n"
                f"🔒 Заблокированные чаты: {len(self.locked_chats)}\n"
                f"⚡ Использование: .smartraid on/off/lock/unlock"
            )
            await utils.answer(message, status_text)
            return
            
        if args == "on":
            self.config["anti_raid"] = True
            await utils.answer(message, "✅ Умный анти-рейд включен")
        elif args == "off":
            self.config["anti_raid"] = False
            await utils.answer(message, "❌ Умный анти-рейд выключен")
        elif args == "lock":
            self.config["lock_on_raid"] = True
            await utils.answer(message, "✅ Блокировка чата при рейде включена")
        elif args == "unlock":
            self.config["lock_on_raid"] = False
            await utils.answer(message, "❌ Блокировка чата при рейде выключена")
        elif args == "status":
            locked_chats_info = "\n".join([f"• {chat}" for chat in list(self.locked_chats)[:5]])
            if len(self.locked_chats) > 5:
                locked_chats_info += f"\n• ... и еще {len(self.locked_chats) - 5}"
                
            await utils.answer(message, f"🔒 Заблокированные чаты:\n{locked_chats_info}")

    # Переопределенные команды с премиум стикерами
    @loader.command()
    async def rkick(self, message):
        """Кикнуть пользователя - .rkick [юзер]"""
        user = await self.get_user(message)
        if not user:
            await utils.answer(message, self.strings("no_user"))
            return
            
        try:
            await self._client.kick_participant(message.chat_id, user.id)
            await self.send_action_message(message, "kick", f"@{user.username}" if user.username else user.first_name)
            await self.log_action("KICK", f"Пользователь {user.id} кикнут из чата")
        except Exception as e:
            await utils.answer(message, f"❌ Ошибка: {e}")

    @loader.command()
    async def rban(self, message):
        """Забанить пользователя - .rban [юзер]"""
        user = await self.get_user(message)
        if not user:
            await utils.answer(message, self.strings("no_user"))
            return
            
        try:
            await self._client.edit_permissions(
                message.chat_id,
                user.id,
                view_messages=False
            )
            await self.send_action_message(message, "ban", f"@{user.username}" if user.username else user.first_name)
            await self.log_action("BAN", f"Пользователь {user.id} забанен в чате")
        except Exception as e:
            await utils.answer(message, f"❌ Ошибка: {e}")

    @loader.command()
    async def rmute(self, message):
        """Замутить пользователя - .rmute [юзер] [время]"""
        args = utils.get_args_raw(message).split()
        user = await self.get_user(message, args[0] if args else None)
        
        if not user:
            await utils.answer(message, self.strings("no_user"))
            return
            
        # Парсим время (например: 1h, 30m, 2d)
        mute_time = 3600  # По умолчанию 1 час
        if len(args) > 1:
            mute_time = self.parse_time(args[1])
            
        try:
            until_date = datetime.now() + timedelta(seconds=mute_time)
            await self._client.edit_permissions(
                message.chat_id,
                user.id,
                send_messages=False,
                until_date=until_date
            )
            await self.send_action_message(message, "mute", f"@{user.username}" if user.username else user.first_name)
            await self.log_action("MUTE", f"Пользователь {user.id} замучен на {mute_time} сек")
        except Exception as e:
            await utils.answer(message, f"❌ Ошибка: {e}")

    def parse_time(self, time_str: str) -> int:
        """Парсит время из строки в секунды"""
        time_units = {
            's': 1, 'sec': 1, 'сек': 1,
            'm': 60, 'min': 60, 'мин': 60, 
            'h': 3600, 'hour': 3600, 'час': 3600,
            'd': 86400, 'day': 86400, 'день': 86400
        }
        
        match = re.match(r'(\d+)\s*([a-zA-Zа-яА-Я]+)', time_str.lower())
        if match:
            amount, unit = match.groups()
            return int(amount) * time_units.get(unit, 60)
        
        return 3600  # По умолчанию 1 час

    @loader.watcher()
    async def anti_raid_watcher(self, message):
        """Умный анти-рейд ватчер с блокировкой чата"""
        if not self.config["anti_raid"]:
            return
            
        if hasattr(message, 'action') and message.action:
            chat_id = utils.get_chat_id(message)
            current_time = time.time()
            
            if chat_id not in self.last_joins:
                self.last_joins[chat_id] = []
            
            self.last_joins[chat_id].append(current_time)
            self.last_joins[chat_id] = [
                t for t in self.last_joins[chat_id] 
                if current_time - t < self.config["raid_timeout"]
            ]
            
            # Проверяем порог рейда
            if len(self.last_joins[chat_id]) >= self.config["raid_threshold"]:
                await self.log_action("RAID_DETECTED", 
                                   f"Обнаружен рейд в чате {chat_id}\n"
                                   f"Участников: {len(self.last_joins[chat_id])}")
                
                # Блокируем чат если включена настройка
                if self.config["lock_on_raid"] and chat_id not in self.locked_chats:
                    await self.lock_chat(chat_id)
                    await message.reply("🛡️ Обнаружен рейд! Чат временно закрыт.")

    async def on_unload(self):
        """Разблокируем все чаты при выгрузке"""
        for chat_id in self.locked_chats.copy():
            await self.unlock_chat(chat_id)
