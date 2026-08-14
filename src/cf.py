import httpx

from env import Env


def cf_api_client(env: Env) -> httpx.Client:
    return httpx.Client(headers={'Authorization': f'Bearer {env.CF_TOKEN}'})
