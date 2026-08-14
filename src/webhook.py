from urllib.parse import urljoin

from workers import Response
from telegram import Bot, Update

from util import consume_front
from http_code import ok, not_found, bad_request
from env import Env


async def handle_webhook(env: Env, path: list) -> Response:
    async def register():
        result = await Bot(env.BOT_TOKEN).set_webhook(
            url=urljoin(env.WEBHOOK_URL, f'{env.ACCESS_ENDPOINT}/message_update'),
            allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY],
            secret_token=env.BOT_SECRET,
        )
        return result and (await status()) or bad_request('Telegram server returned an error.')

    async def unregister():
        result = await Bot(env.BOT_TOKEN).delete_webhook()
        return result and (await status()) or bad_request('Telegram server returned an error.')

    async def status():
        return ok(str(await Bot(env.BOT_TOKEN).get_webhook_info()))

    action = consume_front(path)
    match action:
        case 'register':
            return await register()
        case 'unregister':
            return await unregister()
        case 'status':
            return await status()
        case _:
            return not_found()
