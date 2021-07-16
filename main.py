import asyncio
import io
import os
import sys
import textwrap
import traceback
from contextlib import redirect_stderr, redirect_stdout
from enum import IntEnum
from typing import Any, Dict, List, Optional, Tuple
import discord
from sanic import Sanic, Request, response


class ESpamLevel(IntEnum):
    NormalLv1 = 6
    NormalLv2 = 10
    NormalLv3 = 15
    MultiChannelLv1 = 3
    MultiChannelLv2 = 5
    MultiChannelLv3 = 8


GUILD = 861882851753984001
WHITELIST = (
    689264659085394054  # NOBU
)
SPAM_TIMEOUT = 15
MULTI_CHANNEL_FLAG = 3
MENTION = f'<@{WHITELIST[0]}>'

messages: Dict[int, List[Tuple[discord.Message, asyncio.events.TimerHandle]]] = {}
detected: Dict[int, ESpamLevel] = {}
intents = discord.Intents.all()
client = discord.Client(intents=intents)
app = Sanic(__name__)


@app.route('/')
async def index(request: Request):
    return response.text('Running')


def format_exception(exc: Exception) -> str:
    return ''.join(list(traceback.TracebackException.from_exception(exc).format()))


def cleanup_code(content: str) -> str:
    if content.startswith('```') and content.endswith('```'):
        return '\n'.join(content.split('\n')[1:-1])
    return content.strip(' \n')


async def aexec(body: str, variables: dict) -> Tuple[Any, str, str]:
    body = cleanup_code(body)
    stdout = io.StringIO()
    stderr = io.StringIO()

    exc = f'async def __exc__():\n{textwrap.indent(body, "  ")}'
    exec(exc, variables)

    func = variables['__exc__']
    with redirect_stdout(stdout), redirect_stderr(stderr):
        return await func(), stdout.getvalue(), stderr.getvalue()


async def delete_messages(channel: discord.TextChannel, messages: List[discord.Message]) -> None:
    for m in [messages[i:i+100] for i in range(0, len(messages), 100)]:
        await channel.delete_messages(m)


async def spam_check(message: discord.Message) -> None:
    if message.author.bot or message.guild is None or message.guild.id != GUILD or not isinstance(message.author, discord.Member):
        return False
    if (message.author.guild_permissions.administrator or message.author.guild_permissions.manage_messages
            or message.author.permissions_in(message.channel).manage_messages):
        return False

    def callback(message):
        def inner():
            messages[message.author.id].remove(
                discord.utils.find(lambda pair: pair[0] is message, messages[message.author.id])
            )
            if len(messages[message.author.id]) == 0 and message.author.id in detected:
                detected.pop(message.author.id)
        
        return inner

    if message.author.id not in messages:
        messages[message.author.id] = []
    messages[message.author.id].append((
        message,
        client.loop.call_later(SPAM_TIMEOUT, callback(message))
    ))
    message_count = len(messages[message.author.id])

    level: Optional[ESpamLevel] = None
    channels = tuple(map(lambda pair: pair[0].channel, messages[message.author.id]))
    if (message_count >= ESpamLevel.MultiChannelLv1
            and len(set(channels)) >= MULTI_CHANNEL_FLAG):
        levels = (ESpamLevel.MultiChannelLv3, ESpamLevel.MultiChannelLv2, ESpamLevel.MultiChannelLv1)
        for lv in levels:
            if message_count >= lv:
                level = lv
                break
    elif (message_count >= ESpamLevel.NormalLv1):
        levels = (ESpamLevel.NormalLv3, ESpamLevel.NormalLv2, ESpamLevel.NormalLv1)
        for lv in levels:
            if message_count >= lv:
                level = lv
                break

    if level is not None:
        for count, (m, timer) in enumerate(messages[message.author.id]):
            timer.cancel()
            messages[m.author.id][count] = (
                m,
                client.loop.call_later(SPAM_TIMEOUT, callback(m))
            )

        detected_level = detected.get(message.author.id)
        if (detected_level is None
                or detected_level in (ESpamLevel.NormalLv3, ESpamLevel.MultiChannelLv3)
                or detected_level is not level):
            detected[message.author.id] = level
            if level in (ESpamLevel.NormalLv1, ESpamLevel.MultiChannelLv1):
                mes = await message.channel.send(f'{message.author.mention} Stop spamming!')

                async def task():
                    await asyncio.sleep(10)
                    await mes.delete()

                client.loop.create_task(task())
            elif level in (ESpamLevel.NormalLv2, ESpamLevel.MultiChannelLv2):
                try:
                    await delete_messages(message.channel, tuple(map(lambda pair: pair[0], messages[message.author.id])))
                except Exception as e:
                    print(format_exception(e), file=sys.stderr)
                mes = await message.channel.send(
                    f'{message.author.mention} Stop spamming or you will be KICKED!\n'
                    f'If this was a mistake, please wait {SPAM_TIMEOUT + 3} seconds'
                )

                async def task():
                    await asyncio.sleep(10)
                    await mes.delete()

                client.loop.create_task(task())
            else:
                try:
                    await message.author.kick()
                except Exception as e:
                    print(format_exception(e), file=sys.stderr)
                    mes = await message.channel.send(
                        f'{MENTION}\n'
                        f'Failed to kick {message.author.mention}\n'
                        f'{e.__class__!r}: {e}'
                    )

                    async def task():
                        await asyncio.sleep(10)
                        await mes.delete()

                    client.loop.create_task(task())
                else:
                    await message.channel.send(f'Kicked {message.author.mention}')
                try:
                    await delete_messages(message.channel, tuple(map(lambda pair: pair[0], messages[message.author.id])))
                except Exception as e:
                    print(format_exception(e), file=sys.stderr)
            return True
        return False


@client.event
async def on_ready() -> None:
    print('Ready')
    await app.create_server('0.0.0.0', 8080, access_log=False, return_asyncio_server=True)


@client.event
async def on_message(message: discord.Message) -> None:
    if await spam_check(message):
        return
    if message.author.bot:
        return
    
    args = message.content.split(' ')

    if message.author.id in WHITELIST:
        if args[0] == '!exec':
            variables = globals()
            variables.update(locals())

            try:
                result, out, err = await aexec(' '.join(args[1:]), variables)
                if out:
                    print(out, file=sys.stdout)
                if err:
                    print(err, file=sys.stderr)
                await message.channel.send(str(result))
            except Exception as e:
                await message.channel.send(format_exception(e))


client.run(os.getenv('TOKEN'))
