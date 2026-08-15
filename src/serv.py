from enum import Enum, auto
from dataclasses import dataclass
from urllib.parse import urlencode, urlunparse
from datetime import date as date_

import httpx
from telegram import Bot
from telegram.constants import ParseMode

from user import (
    get_all_user_durable_object_ids,
    suspend_user,
    resume_user,
    UserDurableObject,
    UserGroup,
    UserCapability,
    UserNotification,
    SuspendUserResult,
    ResumeUserResult,
)
from proto import encode_typed_message, decode_typed_message
from env import Env
from http_code import ok, not_found, internal_server_error
from util import consume_front, date2str, to_integer


async def handle_serv(env: Env, path: list):
    serv_mgr = ServList(env)

    async def sync():
        obj_ids = get_all_user_durable_object_ids(env)
        inbounds = serv_mgr.get_inbounds()
        server_filter = consume_front(path)

        response = ''
        has_error = False
        for obj_id in obj_ids:
            user = UserDurableObject.get_stub_from_raw(env, obj_id)
            user_id = await user.get_user_id()
            uuid = await user.get_uuid()
            response += f'User (user_id = {user_id}, uuid = {uuid})\n'
            if not user_id:  # TODO: REMOVE IT
                continue
            if await user.is_suspended():
                continue
            for server, inbound in inbounds:
                if server_filter and server.tag != server_filter:
                    continue
                try:
                    result = inbound.add_user(user_id, uuid, inbound.default_flow)
                    if result not in (AddUserResult.SUCCESS, AddUserResult.ALREADY_EXISTS):
                        has_error = True
                    response += f'\t{server.name} {inbound.name} -> {result.name}\n'
                except httpx.RequestError as e:
                    response += f'\t{server.name} {inbound.name} -> {e}\n'

        return ok(response) if not has_error else internal_server_error(response)

    action = consume_front(path)
    match action:
        case 'sync':
            return await sync()
        case _:
            return not_found()


async def cron_serv(env: Env, date: date_):
    obj_ids = get_all_user_durable_object_ids(env)
    user_map = {}

    bot = Bot(env.BOT_TOKEN)

    async def send_notification(user_id: int, notification: 'UserNotification', text: str):
        user = user_map[user_id]
        if (
            user_id < 0
            or not await user.is_notification_enabled(notification.value)
            or not await user.deduplicate_notification_day_once(text)
        ):
            return
        await bot.send_message(user_id, text, parse_mode=ParseMode.HTML)

    for obj_id in obj_ids:
        user = UserDurableObject.get_stub_from_raw(env, obj_id)
        user_id = await user.get_user_id()
        if not user_id:  # TODO: REMOVE IT
            continue

        user_map[user_id] = user

        if await user.push_today_to_daily_storage_area():
            if await user.get_group() == UserGroup.SUSPENDED_EXCESSIVE_USE:
                result = await resume_user(env, user_id)
                if result != ResumeUserResult.SUCCESS:
                    print(f'Something went wrong, ResumeUserResult = {result}')
                    continue
                await send_notification(
                    user_id,
                    UserNotification.SERVICE_RESTORATION_NOTICE,
                    (
                        '🕓 <b>Service Restored</b> » '
                        'Service for your account has been restored, and you can now connect to all nodes as usual.'
                    ),
                )

    # Consume usage stats
    serv_mgr = ServList(env)
    servers = serv_mgr.get_servers_with_tag()
    for tag, server in servers:
        try:
            stats = server.query_usage_stats(reset=True)
        except httpx.RequestError as e:
            print(f'Failed to query usage stats, error message: {e}')
            continue
        for user_id, data in stats.items():
            if user_id not in user_map:
                continue
            user = user_map[user_id]
            up, down = await user.record_usage_stats(date2str(date), tag, data['up'], data['down'])
            if server.daily_usage_limit:
                used = up + down
                if (
                    not used
                    or await user.is_suspended()
                    or await user.has_capability(UserCapability.UNLIMITED_DATA.value)
                ):
                    continue
                if used > server.daily_usage_limit:
                    result = await suspend_user(env, user_id)
                    if result != SuspendUserResult.SUCCESS:
                        print(f'Something went wrong, SuspendUserResult = {result}')
                    await send_notification(
                        user_id,
                        UserNotification.SERVICE_SUSPENSION_NOTICE,
                        (
                            '⚠️ <b>Service Suspended</b> » '
                            'Due to excessive usage, your service has been temporarily suspended. Please wait until tomorrow for service to resume.'
                        ),
                    )
                elif used > server.daily_usage_limit * 0.8:
                    await send_notification(
                        user_id,
                        UserNotification.BEFORE_EXCESSIVE_ALERT,
                        (
                            '⏳ <b>Excessive Use Alert</b> » '
                            'Your usage has exceeded <u>80%</u> of your daily quota. Please be aware.'
                        ),
                    )
                elif used > server.daily_usage_limit * 0.6:
                    await send_notification(
                        user_id,
                        UserNotification.BEFORE_EXCESSIVE_ALERT,
                        (
                            '⏳ <b>Excessive Use Alert</b> » '
                            'Your usage has exceeded <u>60%</u> of your daily quota. Please be aware.'
                        ),
                    )


def _email_from_telegram_user_id(user_id: int) -> str:
    # import random

    # if not user_id:
    #     return f'{random.randint(10000000, 99999999)}@nonuserid.org'
    return f'{user_id}@telegram.org'


def _telegram_user_id_from_email(email: str) -> int | None:
    email = email.removesuffix('@telegram.org')
    return to_integer(email)


def _generate_vless_share_url(
    uuid: str,
    address: str,
    port: int,
    name: str,
    mode: str = None,
    path: str = None,
    security: str = None,
    host: str = None,
    pbk: str = None,
    fp: str = None,
    ttype: str = None,
    flow: str = None,
    sni: str = None,
):
    query = {}

    def add_param(key, value):
        if value is not None:
            query[key] = value

    assert (
        mode is None
        or mode in ('gun', 'multi', 'guna')  # gRPC
        or mode in ('auto', 'packet-up', 'stream-up', 'stream-one')  # XHTTP
    )
    assert security in (None, 'none', 'tls', 'reality')
    if security == 'reality':
        assert isinstance(pbk, str)
        assert isinstance(fp, str)
    assert ttype in (None, 'tcp', 'kcp', 'ws', 'http', 'grpc', 'httpupgrade', 'xhttp')
    assert flow in (None, '', 'xtls-rprx-vision')
    if sni is not None:
        assert sni != ''

    add_param('mode', mode)
    add_param('path', path)
    add_param('security', security)
    add_param('host', host)
    add_param('pbk', pbk)
    add_param('fp', fp)
    add_param('type', ttype)
    add_param('flow', flow)
    add_param('sni', sni)

    return urlunparse(('vless', f'{uuid}@{address}:{port}', '', '', urlencode(query), name))


class AddUserResult(Enum):
    SUCCESS = auto()
    UNKNOWN_ERROR = auto()
    ALREADY_EXISTS = auto()


class RemoveUserResult(Enum):
    SUCCESS = auto()
    UNKNOWN_ERROR = auto()
    NOT_FOUND = auto()


class GetUsageStatsResult(Enum):
    SUCCESS = auto()
    UNKNOWN_ERROR = auto()
    NOT_FOUND = auto()


@dataclass
class SysStats:
    uptime: int  # seconds
    number_of_goroutines: int
    number_of_gc_cycles: int
    heap_allocated: int  # bytes
    virtual_allocated: int  # bytes
    live_objects: int
    gc_pause_duration: int  # nanoseconds


@dataclass
class Inbound:
    _parent: 'Serv'
    tag: str

    # Common

    name: str
    ttype: str
    region: str
    description: str
    recommended: bool  # OPT
    port: int  #  OPT, OVERRIDE
    disable_ipv4: bool  # OPT
    disable_ipv6: bool  # OPT

    # VLESS-specific

    default_flow: str  # OPT
    security: str  # OPT

    def get_inbound_config(self) -> dict | None:
        """
        Raises:
            httpx.RequestError: Requests to the backend service may fail.
        """
        inbounds = self._parent._get_inbounds_config()
        for inbound in inbounds:
            if inbound['tag'] == self.tag:
                return inbound
        return None

    def get_user(self, user_id: int) -> dict:
        """
        Raises:
            httpx.RequestError: Requests to the backend service may fail.
        """
        response = self._parent.post(
            '/xray.app.proxyman.command.HandlerService/GetInboundUsers',
            payload={
                'tag': self.tag,
                'email': _email_from_telegram_user_id(user_id),
            },
        )
        # If we query for a nonexistent user, the response is as follows: (no error)
        # {'users': [{'level': 0, 'email': '', 'account': None}]}
        return response['users'][0]

    def has_user(self, user_id: int) -> bool:
        """
        Raises:
            httpx.RequestError: Requests to the backend service may fail.
        """
        return self.get_user(user_id)['account'] is not None

    def add_user(self, user_id: int, uuid: str, flow: str = None) -> AddUserResult:
        """
        Raises:
            httpx.RequestError: Requests to the backend service may fail.
        """
        account = {'id': uuid}
        if flow:
            account['flow'] = flow
        operation = encode_typed_message(
            'app.proxyman.command.AddUserOperation',
            {
                'user': {
                    'email': _email_from_telegram_user_id(user_id),
                    'account': encode_typed_message('proxy.vless.Account', account),
                },
            },
        )
        response = self._parent.post(
            '/xray.app.proxyman.command.HandlerService/AlterInbound',
            payload={
                'tag': self.tag,
                'operation': operation,
            },
        )
        if isinstance(response, dict) and not response:
            return AddUserResult.SUCCESS
        if response['code'] == 2 and 'already exists' in response['message']:
            return AddUserResult.ALREADY_EXISTS
        return AddUserResult.UNKNOWN_ERROR

    def remove_user(self, user_id: int) -> RemoveUserResult:
        """
        Raises:
            httpx.RequestError: Requests to the backend service may fail.
        """
        operation = encode_typed_message(
            'app.proxyman.command.RemoveUserOperation',
            {
                'email': _email_from_telegram_user_id(user_id),
            },
        )
        response = self._parent.post(
            '/xray.app.proxyman.command.HandlerService/AlterInbound',
            payload={
                'tag': self.tag,
                'operation': operation,
            },
        )
        if isinstance(response, dict) and not response:
            return RemoveUserResult.SUCCESS
        if response['code'] == 2 and 'not found' in response['message']:
            return RemoveUserResult.NOT_FOUND
        return RemoveUserResult.UNKNOWN_ERROR


@dataclass
class InboundReality(Inbound):
    fingerprint: str  # TLS-specific
    public_key: str

    def generate_share_url(self, user_id: int):
        """
        Raises:
            httpx.RequestError: Requests to the backend service may fail.
        """
        inbound = self.get_inbound_config()
        account = self.get_user(user_id)['account']
        if not inbound or account is None:
            return None

        account = decode_typed_message(account)
        receiver_settings = decode_typed_message(inbound['receiverSettings'])
        security_settings = decode_typed_message(
            receiver_settings['streamSettings']['securitySettings'][0]
        )

        server_names = [x for x in security_settings['serverNames'] if x]
        assert len(server_names) > 0

        kwargs = {
            'uuid': account['id'],
            'port': self.port or receiver_settings['portList']['range'][0]['From'],
            'name': self.name,
            'security': 'reality',
            'pbk': self.public_key,
            'fp': self.fingerprint,
            'ttype': receiver_settings['streamSettings']['protocolName'],
            'flow': 'flow' in account and account['flow'] or None,
            'sni': server_names[0],
        }

        v4_link = None
        if not self.disable_ipv4:
            kwargs['address'] = self._parent.ipv4
            v4_link = _generate_vless_share_url(**kwargs)
        v6_link = None
        if not self.disable_ipv6:
            kwargs['name'] += '-IPv6'
            kwargs['address'] = f'[{self._parent.ipv6}]'
            v6_link = _generate_vless_share_url(**kwargs)

        return v4_link, v6_link


@dataclass
class InboundXHttp(Inbound):
    fingerprint: str  # TLS-specific

    def generate_share_url(self, user_id: int):
        inbound = self.get_inbound_config()
        account = self.get_user(user_id)['account']
        if not inbound or account is None:
            return None

        account = decode_typed_message(account)
        receiver_settings = decode_typed_message(inbound['receiverSettings'])
        transport_settings = decode_typed_message(
            receiver_settings['streamSettings']['transportSettings'][0]['settings']
        )

        return _generate_vless_share_url(
            uuid=account['id'],
            address=transport_settings['host'],
            port=443,
            name=self.name,
            mode=transport_settings['mode'],
            path=transport_settings['path'],
            security=self.security,
            host=transport_settings['host'],
            fp=self.fingerprint,
            ttype='xhttp',
        )


class ServList:
    servers: dict[str, 'Serv']

    def __init__(self, env: Env):
        self.servers = {}
        node_tags = env.NODE_LIST.split(';')
        for tag in node_tags:
            self.servers[tag] = Serv(env, tag)

    def get_tags(self) -> tuple[str, ...]:
        return tuple(self.servers.keys())

    def get_names(self) -> tuple[str, ...]:
        return tuple(x.name for x in self.servers.values())

    def get_servers(self) -> tuple['Serv', ...]:
        return tuple(self.servers.values())

    def get_servers_with_tag(self) -> tuple[tuple[str, 'Serv'], ...]:
        return tuple(self.servers.items())

    def get_inbounds(self) -> list[tuple['Serv', 'Inbound']]:
        ret = []
        for server in self.get_servers():
            for inbound in server.get_inbounds():
                ret.append((server, inbound))
        return ret


class Serv:
    tag: str
    name: str
    ipv4: str
    ipv6: str
    grpc_gateway: str
    inbounds: dict[str, Inbound]
    daily_usage_limit: int

    # runtime
    _client = httpx.Client

    def __init__(self, env: Env, tag: str):
        dyn_env = env.DYNAMIC

        def opt_env(key, default_value):
            return key in dyn_env and dyn_env[key] or default_value

        def opt_env_bool(key, expect, default_value):
            if key not in dyn_env:
                return default_value
            return dyn_env[key] == expect

        pfx = f'_{tag}_'
        self.tag = tag
        self.name = dyn_env[f'{pfx}NAME']
        self.ipv4 = opt_env(f'{pfx}IPV4', None)
        self.ipv6 = opt_env(f'{pfx}IPV6', None)
        self.grpc_gateway = dyn_env[f'{pfx}GRPC_GATEWAY']
        self.daily_usage_limit = int(opt_env(f'{pfx}DAILY_USAGE_LIMIT', 0))
        self.inbounds = {}

        auth_token = dyn_env[f'{pfx}GRPC_AUTH_TOKEN']
        self._client = httpx.Client(headers={'X-Auth-Token': auth_token})

        inbounds = dyn_env[f'{pfx}INBOUND_LIST'].split(';')
        for tag in inbounds:
            pfx_ = f'{pfx}INBOUND__{tag}_'
            name = dyn_env[f'{pfx_}NAME']
            ttype = dyn_env[f'{pfx_}TYPE']
            region = dyn_env[f'{pfx_}REGION']
            description = dyn_env[f'{pfx_}DESCRIPTION']
            recommended = opt_env_bool(f'{pfx_}RECOMMENDED', '1', False)
            port = int(opt_env(f'{pfx_}PORT', 0))
            disable_ipv4 = opt_env_bool(f'{pfx_}DISABLE_IPV4', '1', False)
            disable_ipv6 = opt_env_bool(f'{pfx_}DISABLE_IPV6', '1', False)
            if not self.ipv4:
                disable_ipv4 = True
            if not self.ipv6:
                disable_ipv6 = True
            default_flow = opt_env(f'{pfx_}DEFAULT_FLOW', '')
            security = opt_env(f'{pfx_}SECURITY', 'none')
            inbound_args = (
                self,
                tag,
                name,
                ttype,
                region,
                description,
                recommended,
                port,
                disable_ipv4,
                disable_ipv6,
                default_flow,
                security,
            )
            match ttype:
                case 'reality':
                    fingerprint = dyn_env[f'{pfx_}FINGERPRINT']
                    public_key = dyn_env[f'{pfx_}PUBLIC_KEY']
                    self.inbounds[tag] = InboundReality(*inbound_args, fingerprint, public_key)
                case 'xhttp':
                    fingerprint = dyn_env[f'{pfx_}FINGERPRINT']
                    self.inbounds[tag] = InboundXHttp(*inbound_args, fingerprint)
                case _:
                    assert False, 'Unsupported node type'

    def _get_inbounds_config(self):
        response = self.post(
            '/xray.app.proxyman.command.HandlerService/ListInbounds',
            payload={'isOnlyTags': False},
        )
        return response['inbounds']

    def post(self, path: str, payload: dict = None):
        """
        Raises:
            httpx.RequestError: Requests to the backend service may fail.
        """
        assert path.startswith('/')
        response = self._client.post(f'{self.grpc_gateway}{path}', json=payload)
        return response.json()

    def get_inbounds(self) -> list[Inbound]:
        return self.inbounds.values()

    def get_usage_stats(
        self, user_id: int, up=False, down=False, reset=False
    ) -> tuple[GetUsageStatsResult, int]:
        """
        Raises:
            httpx.RequestError: Requests to the backend service may fail.
        """
        assert up != down
        updown = 'up' if up else 'down'
        response = self.post(
            '/xray.app.stats.command.StatsService/GetStats',
            payload={
                'name': f'user>>>{_email_from_telegram_user_id(user_id)}>>>traffic>>>{updown}link',
                'reset': reset,
            },
        )
        if 'stat' in response:
            return GetUsageStatsResult.SUCCESS, int(response['stat']['value'])
        if 'message' in response and 'not found' in response['message']:
            return GetUsageStatsResult.NOT_FOUND, None
        return GetUsageStatsResult.UNKNOWN_ERROR, None

    def query_usage_stats(self, reset: bool = False):
        """
        Raises:
            httpx.RequestError: Requests to the backend service may fail.
        """
        response = self.post(
            '/xray.app.stats.command.StatsService/QueryStats',
            payload={'pattern': 'user>>>', 'reset': reset},
        )
        ret = {}
        for stat in response['stat']:
            data = stat['name'].split('>>>')
            if data[0] != 'user' or data[2] != 'traffic' or data[3] not in ('downlink', 'uplink'):
                continue
            user_id = _telegram_user_id_from_email(data[1])
            updown = 'up' if data[3] == 'uplink' else 'down'
            if not user_id:
                continue
            if user_id not in ret:
                ret[user_id] = {}
            ret[user_id][updown] = int(stat['value'])
        for check in ret.values():
            assert 'up' in check and 'down' in check
        return ret

    def health_check(self) -> bool:
        return True  # TODO: DO A REAL HEALTH CHECK

    def get_sys_stats(self) -> SysStats:
        """
        Raises:
            httpx.RequestError: Requests to the backend service may fail.
        """
        response = self.post('/xray.app.stats.command.StatsService/GetSysStats')
        return SysStats(
            response['Uptime'],
            response['NumGoroutine'],
            response['NumGC'],
            int(response['Alloc']),
            int(response['Sys']),
            int(response['LiveObjects']),
            int(response['PauseTotalNs']),
        )
