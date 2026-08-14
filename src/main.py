from urllib.parse import urlparse

from workers import Request, Response, WorkerEntrypoint

from webhook import handle_webhook
from update import handle_update
from user import handle_user
from serv import handle_serv, cron_serv
from util import consume_front, get_date_now
from http_code import not_found, unauthorized, ok
from env import Env

from user import UserDurableObject  # noqa: F401, required by workerd.


async def main(env, request: Request) -> Response:
    url = urlparse(request.url)
    path = url.path.strip('/').split('/')

    env = Env.wrap(env)

    if consume_front(path) != env.ACCESS_ENDPOINT:
        return unauthorized()

    action = consume_front(path)
    match action:
        case 'webhook':
            return await handle_webhook(env, path)
        case 'message_update':
            if request.headers.get('x-telegram-bot-api-secret-token') != env.BOT_SECRET:
                return unauthorized()
            return await handle_update(env, await request.json())
        case 'user':
            return await handle_user(env, path)
        case 'serv':
            return await handle_serv(env, path)
        case 'cron':
            await run_cronjob(env)
            return ok('Cronjob triggered.')
        case _:
            return not_found()


async def run_cronjob(env):
    if not isinstance(env, Env):
        env = Env.wrap(env)
    date = get_date_now()

    await cron_serv(env, date)


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return await main(self.env, request)

    # see: https://github.com/cloudflare/workerd/issues/6624
    # async def scheduled(self, controller, env, ctx):
    #     return await run_cronjob(self.env)
