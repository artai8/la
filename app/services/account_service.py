"""账号服务 - 养号/双向限制检测 — 100 账号增强版"""
import asyncio
import logging
import random
from datetime import datetime

from telethon.tl.functions.messages import GetHistoryRequest, SearchGlobalRequest
from telethon.tl.functions.channels import GetChannelsRequest
from telethon.tl.functions.contacts import GetContactsRequest, SearchRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.functions.account import UpdateProfileRequest, UpdateStatusRequest
from telethon.errors import FloodWaitError, UserBannedInChannelError, ChatWriteForbiddenError

from app.services.telegram_client import get_client

logger = logging.getLogger(__name__)

# 丰富的搜索关键词池 — 模拟真实用户行为
_SEARCH_TERMS = [
    "news", "music", "tech", "crypto", "game", "travel", "food",
    "weather", "sports", "movie", "TV show", "podcast", "book",
    "shopping", "finance", "health", "fitness", "coding",
    "photography", "cooking", "education", "science",
    "fashion", "art", "history", "nature", "pets",
]

# 公开频道用于养号阅读（热门频道）
_PUBLIC_CHANNELS = [
    "@telegram", "@durov", "@tikiichannel", "@bbcnews",
    "@caborunda", "@devdojo", "@techcrunch",
]


async def nurture_account(account_id: str, duration_minutes: int = 5) -> dict:
    """
    养号: 模拟正常用户行为
    - 浏览已加入的群/频道消息
    - 搜索联系人
    - 阅读公开频道
    - 更新在线状态
    - 随机等待
    """
    client = get_client(account_id)
    if not client:
        return {"success": False, "message": "客户端未连接"}

    actions_done = []
    try:
        # 1. 更新在线状态
        try:
            await client(UpdateStatusRequest(offline=False))
            actions_done.append("更新为在线状态")
        except Exception:
            pass

        await asyncio.sleep(random.uniform(1, 3))

        # 2. 获取对话列表
        dialogs = await client.get_dialogs(limit=30)
        actions_done.append(f"获取了 {len(dialogs)} 个对话")

        # 3. 随机浏览几个对话的消息
        browsed = 0
        sample_size = min(random.randint(3, 8), len(dialogs))
        for dialog in random.sample(dialogs, sample_size):
            try:
                messages = await client.get_messages(dialog.entity, limit=random.randint(5, 20))
                browsed += 1
                # 随机"阅读"一些消息 (mark as read)
                if messages:
                    try:
                        await client.send_read_acknowledge(dialog.entity, messages[0])
                    except Exception:
                        pass
                await asyncio.sleep(random.uniform(2, 6))
            except Exception:
                pass
        actions_done.append(f"浏览了 {browsed} 个对话的消息")

        # 4. 搜索联系人
        try:
            await client(GetContactsRequest(hash=0))
            actions_done.append("获取了联系人列表")
        except Exception:
            pass

        await asyncio.sleep(random.uniform(2, 5))

        # 5. 全局搜索多个常见词
        search_count = random.randint(1, 3)
        terms = random.sample(_SEARCH_TERMS, min(search_count, len(_SEARCH_TERMS)))
        for term in terms:
            try:
                await client(SearchRequest(q=term, limit=5))
                actions_done.append(f"搜索了 '{term}'")
            except Exception:
                pass
            await asyncio.sleep(random.uniform(1, 4))

        # 6. 随机阅读一个公开频道
        try:
            channel_username = random.choice(_PUBLIC_CHANNELS)
            entity = await client.get_entity(channel_username)
            messages = await client.get_messages(entity, limit=random.randint(5, 15))
            actions_done.append(f"阅读了频道 {channel_username} 的 {len(messages)} 条消息")
        except Exception:
            pass

        await asyncio.sleep(random.uniform(2, 5))

        # 7. 查看自己的信息
        try:
            me = await client.get_me()
            actions_done.append(f"查看了自己的信息: {me.first_name}")
        except Exception:
            pass

        # 8. 更新为离线状态
        try:
            await client(UpdateStatusRequest(offline=True))
            actions_done.append("更新为离线状态")
        except Exception:
            pass

        logger.info(f"养号完成 (account={account_id}): {', '.join(actions_done)}")
        return {"success": True, "actions": actions_done}

    except FloodWaitError as e:
        msg = f"FloodWait: 需要等待 {e.seconds} 秒"
        logger.warning(f"养号中断 (account={account_id}): {msg}")
        return {"success": False, "message": msg, "actions": actions_done}
    except Exception as e:
        logger.error(f"养号失败 (account={account_id}): {e}")
        return {"success": False, "message": str(e), "actions": actions_done}


async def batch_nurture(account_ids: list[str], concurrency: int = 3) -> dict:
    """批量养号 — 用于定时任务"""
    semaphore = asyncio.Semaphore(concurrency)
    results = {}

    async def _worker(aid: str):
        async with semaphore:
            result = await nurture_account(aid)
            results[aid] = result
            # 随机间隔避免模式被检测
            await asyncio.sleep(random.uniform(5, 15))

    tasks = [asyncio.create_task(_worker(aid)) for aid in account_ids]
    await asyncio.gather(*tasks, return_exceptions=True)

    success_count = sum(1 for r in results.values() if r.get("success"))
    logger.info(f"批量养号完成: {success_count}/{len(account_ids)} 成功")
    return {"results": results, "success_count": success_count, "total": len(account_ids)}


async def check_restriction(account_id: str) -> dict:
    """
    检测双向限制
    返回: {"restricted": True/False, "details": {...}}
    """
    client = get_client(account_id)
    if not client:
        return {"restricted": None, "details": {"error": "客户端未连接"}}

    details = {
        "can_send_message": None,
        "can_search": None,
        "can_get_contacts": None,
        "can_join_channel": None,
        "account_restricted": False,
    }

    try:
        # 1. 获取自己的信息,检查是否有限制标记
        me = await client.get_me()
        if hasattr(me, "restricted") and me.restricted:
            details["account_restricted"] = True

        # 2. 尝试搜索
        try:
            await client(SearchRequest(q="test", limit=1))
            details["can_search"] = True
        except Exception as e:
            details["can_search"] = False
            logger.debug(f"搜索受限: {e}")

        # 3. 尝试获取联系人
        try:
            await client(GetContactsRequest(hash=0))
            details["can_get_contacts"] = True
        except Exception as e:
            details["can_get_contacts"] = False
            logger.debug(f"获取联系人受限: {e}")

        # 4. 尝试向 SpamBot 发送消息检测
        try:
            spam_bot = await client.get_entity("@SpamBot")
            await client.send_message(spam_bot, "/start")
            await asyncio.sleep(2)
            messages = await client.get_messages(spam_bot, limit=1)
            if messages:
                response_text = messages[0].text or ""
                if "limited" in response_text.lower() or "restricted" in response_text.lower() or "限制" in response_text:
                    details["can_send_message"] = False
                    details["spam_bot_response"] = response_text[:200]
                else:
                    details["can_send_message"] = True
                    details["spam_bot_response"] = response_text[:200]
        except Exception as e:
            details["can_send_message"] = None
            logger.debug(f"SpamBot 检测失败: {e}")

        is_restricted = (
            details["account_restricted"]
            or details["can_send_message"] is False
            or details["can_search"] is False
        )

        logger.info(f"限制检测完成 (account={account_id}): restricted={is_restricted}")
        return {"restricted": is_restricted, "details": details}

    except FloodWaitError as e:
        return {"restricted": None, "details": {"error": f"FloodWait: 等待 {e.seconds} 秒"}}
    except Exception as e:
        logger.error(f"限制检测失败: {e}")
        return {"restricted": None, "details": {"error": str(e)}}
