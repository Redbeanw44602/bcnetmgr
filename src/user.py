import json
import uuid
import asyncio
from enum import Enum, IntEnum, Flag, auto
from dataclasses import dataclass, field, asdict
from datetime import date as date_

from workers import DurableObject
from pyodide.ffi import JsException

from cf import cf_api_client
from util import (
    consume_front,
    get_date_now,
    get_weekdays,
    get_monthdays,
    str2date,
    date2str,
    to_integer,
)
from http_code import ok, not_found, bad_request
from env import Env


class AddUserResult(Enum):
    SUCCESS = auto()
    ILLEGAL_USER_ID = auto()
    ALREADY_EXISTS = auto()


class SuspendUserResult(Enum):
    SUCCESS = auto()
    NOT_EXISTS = auto()
    ALREADY_SUSPENDED = auto()
    HAS_ERROR = auto()


class ResumeUserResult(Enum):
    SUCCESS = auto()
    NOT_EXISTS = auto()
    NOT_SUSPENDED = auto()
    HAS_ERROR = auto()


def get_all_user_durable_object_ids(env: Env) -> list[str]:
    client = cf_api_client(env)
    response = client.get(
        f'https://api.cloudflare.com/client/v4/accounts/{env.CF_ACCOUNT_ID}/workers/durable_objects/namespaces/{env.CF_USER_DURABLE_OBJECT_NAMESPACE_ID}/objects'
    ).json()
    return [x['id'] for x in response['result']]


async def add_user(env: Env, user_id: int) -> tuple[AddUserResult, 'UserDurableObject']:
    if user_id not in range(10000000, 99999999):
        return AddUserResult.ILLEGAL_USER_ID, None
    user_id = -user_id  # Manually added users have negative IDs.
    user = UserDurableObject.get_stub(env, user_id)
    if await user.is_registered():
        return AddUserResult.ALREADY_EXISTS, user
    await user.register()
    return AddUserResult.SUCCESS, user


async def suspend_user(env: Env, user_id: int) -> SuspendUserResult:
    from serv import ServList, RemoveUserResult

    user = UserDurableObject.get_stub(env, user_id)
    if not await user.is_registered():
        return SuspendUserResult.NOT_EXISTS
    if await user.is_suspended():
        return SuspendUserResult.ALREADY_SUSPENDED
    await user.set_group(UserGroup.SUSPENDED)
    serv_mgr = ServList(env)
    has_error = False
    for _, inbound in serv_mgr.get_inbounds():
        result = inbound.remove_user(user_id)
        if result not in (RemoveUserResult.SUCCESS, RemoveUserResult.NOT_FOUND):
            has_error = True
    return SuspendUserResult.SUCCESS if not has_error else SuspendUserResult.HAS_ERROR


async def resume_user(env: Env, user_id: int) -> ResumeUserResult:
    from serv import ServList, AddUserResult

    user = UserDurableObject.get_stub(env, user_id)
    if not await user.is_registered():
        return ResumeUserResult.NOT_EXISTS
    if not await user.is_suspended():
        return ResumeUserResult.NOT_SUSPENDED
    await user.set_group(UserGroup.NORMAL_USER)
    serv_mgr = ServList(env)
    has_error = False
    for _, inbound in serv_mgr.get_inbounds():
        result = inbound.add_user(user_id, await user.get_uuid(), inbound.default_flow)
        if result not in (AddUserResult.SUCCESS, AddUserResult.ALREADY_EXISTS):
            has_error = True
    return ResumeUserResult.SUCCESS if not has_error else ResumeUserResult.HAS_ERROR


async def handle_user(env: Env, path: list):
    # client = _cf_api_client(env)

    async def llist():
        obj_id = consume_front(path)
        if obj_id:
            user = UserDurableObject.get_stub_from_raw(env, obj_id)
            if not user:
                return bad_request('Object not found.')
            return ok(json.dumps(await user.dump(), indent=2))
        obj_ids = get_all_user_durable_object_ids(env)
        response = f'Count: {len(obj_ids)}\n\n'
        for obj_id in obj_ids:
            user = UserDurableObject.get_stub_from_raw(env, obj_id)
            response += f'{obj_id} {await user.get_uuid()} {await user.get_user_id()}\n'
        return ok(response)

    async def add():
        user_id = to_integer(consume_front(path))
        if not user_id:
            return bad_request('Illegal user id.')
        result, user = await add_user(env, user_id)
        match result:
            case AddUserResult.ILLEGAL_USER_ID:
                return bad_request('Illegal user id.')
            case AddUserResult.ALREADY_EXISTS:
                return ok(f'Already exists, UUID = {await user.get_uuid()}.')
            case AddUserResult.SUCCESS:
                return ok(f'Completed, UUID = {await user.get_uuid()}.')

    async def suspend():
        user_id = to_integer(consume_front(path))
        if not user_id:
            return bad_request('Illegal user id.')
        result = await suspend_user(env, user_id)
        match result:
            case SuspendUserResult.NOT_EXISTS:
                return not_found('User does not exists.')
            case SuspendUserResult.ALREADY_SUSPENDED:
                return ok('User has been suspended.')
            case SuspendUserResult.HAS_ERROR:
                return ok('Completed, hasErr = True.')  # FIXME: Return OK?
            case SuspendUserResult.SUCCESS:
                return ok('Completed, hasErr = False.')

    async def resume():
        user_id = to_integer(consume_front(path))
        if not user_id:
            return bad_request('Illegal user id.')
        result = await resume_user(env, user_id)
        match result:
            case ResumeUserResult.NOT_EXISTS:
                return not_found('User does not exists.')
            case ResumeUserResult.NOT_SUSPENDED:
                return ok('User is not been suspended.')
            case ResumeUserResult.HAS_ERROR:
                return ok('Completed, hasErr = True.')  # FIXME: Return OK?
            case ResumeUserResult.SUCCESS:
                return ok('Completed, hasErr = False.')

    action = consume_front(path)
    match action:
        case 'list':
            return await llist()
        case 'add':
            return await add()
        case 'suspend':
            return await suspend()
        case 'resume':
            return await resume()
        case _:
            return not_found()


class UserLanguage(IntEnum):
    ENGLISH = auto()
    # CHINESE = auto()

    @property
    def label(self) -> str:
        return {
            UserLanguage.ENGLISH: '🇺🇸 English (US)',
            # UserLanguage.CHINESE: '🇨🇳 Chinese (Simplified)',
        }[self]


class UserGroup(IntEnum):
    UNKNOWN = 0
    NOT_REGISTERED = 10
    SUSPENDED_EXCESSIVE_USE = 18
    SUSPENDED = 19  # Keep sync with is_suspended() utility.
    NORMAL_USER = 20
    ADMIN = 99

    @property
    def label(self):
        mapping = {
            UserGroup.SUSPENDED_EXCESSIVE_USE: 'Normal User (Service suspended due to excessive use)',
            UserGroup.SUSPENDED: 'Normal User (Service suspended)',
            UserGroup.NORMAL_USER: 'Normal User',
            UserGroup.ADMIN: 'Administrator',
        }
        return self in mapping and mapping[self] or self.name


class UserCapability(Flag):
    DEFAULT = auto()
    UNLIMITED_DATA = auto()


class UserNotification(Flag):
    ALL = auto()
    DAILY_USAGE_REMINDER = auto()
    WEEKLY_USAGE_REMINDER = auto()
    MONTHLY_USAGE_REMINDER = auto()
    BEFORE_EXCESSIVE_ALERT = auto()
    SERVICE_SUSPENSION_NOTICE = auto()
    SERVICE_RESTORATION_NOTICE = auto()

    @property
    def label(self):
        return {
            UserNotification.ALL: 'All notifications',
            UserNotification.DAILY_USAGE_REMINDER: 'Daily usage reminder',
            UserNotification.WEEKLY_USAGE_REMINDER: 'Weekly usage reminder',
            UserNotification.MONTHLY_USAGE_REMINDER: 'Monthly usage reminder',
            UserNotification.BEFORE_EXCESSIVE_ALERT: 'Before excessive notice',
            UserNotification.SERVICE_SUSPENSION_NOTICE: 'Service suspension notice',
            UserNotification.SERVICE_RESTORATION_NOTICE: 'Service restoration notice',
        }[self]


@dataclass
class FlattenNode:
    name: str
    recommended: bool
    region: str
    description: str
    link: str

    @staticmethod
    def create(inbound, link: str, override_name: str = None) -> 'FlattenNode':
        return FlattenNode(
            override_name or inbound.name,
            inbound.recommended,
            inbound.region,
            inbound.description,
            link,
        )


@dataclass
class FlattenNodeCache:
    version: int = 0
    nodes: list[FlattenNode] = field(default_factory=list)

    def serialize(self) -> str:
        return json.dumps({'version': self.version, 'nodes': [asdict(x) for x in self.nodes]})

    def deserialize(self, data: str):
        obj = json.loads(data)
        self.version = obj['version']
        self.nodes = [FlattenNode(**x) for x in obj['nodes']]


class UserDurableObject(DurableObject):
    @staticmethod
    def get_stub(env: Env, user_id: int) -> 'UserDurableObject':  # STUB
        obj_id = env.USER_DURABLE_OBJECT.idFromName(
            f'{"user" if user_id > 0 else "manual_user"}:{user_id}'
        )
        obj = env.USER_DURABLE_OBJECT.get(obj_id)
        asyncio.run(obj.set_user_id(user_id))
        return obj

    @staticmethod
    def get_stub_from_raw(env: Env, object_id: str) -> 'UserDurableObject':  # STUB
        try:
            object_id = env.USER_DURABLE_OBJECT.idFromString(object_id)
        except JsException:  # EXCEPT: Invalid Durable Object ID
            return None
        return env.USER_DURABLE_OBJECT.get(object_id)

    def __init__(self, ctx, env):
        super().__init__(ctx, env)

    async def get(self, key):
        return await self.ctx.storage.get(key)

    async def put(self, key, value):
        return await self.ctx.storage.put(key, value)

    async def dump(self):
        return await self.ctx.storage.list()

    async def is_registered(self) -> bool:
        return await self.get('version') is not None

    async def register(self):
        await self.put('version', 1)

        # v1
        await self.set_group(UserGroup.NORMAL_USER)
        await self.set_capability(UserCapability.DEFAULT)
        await self.reset_uuid()
        await self.reset_notification()
        await self.push_today_to_daily_storage_area()

    async def delete(self):
        # TODO: KEEP SOME FIELD?
        # TODO: USE BATCHED DELETE?
        for key in await self.ctx.storage.list():
            await self.ctx.storage.delete(key)

    async def get_user_id(self) -> int:
        return await self.get('user_id')

    async def set_user_id(self, user_id: int):
        await self.put('user_id', user_id)

    async def get_allowed_callback(self) -> list[str]:
        callbacks = await self.get('allowed_callback')
        return callbacks and json.loads(callbacks) or []

    async def set_allowed_callback(self, *callbacks):
        filtered = tuple(x for x in callbacks if x is not None)
        await self.put('allowed_callback', json.dumps(filtered))

    async def get_language(self) -> int:
        return await self.get('language')

    async def set_language(self, language: UserLanguage):
        await self.put('language', language)

    async def get_group(self) -> int:
        return await self.get('group')

    async def is_suspended(self) -> bool:
        return (await self.get_group()) in (UserGroup.SUSPENDED, UserGroup.SUSPENDED_EXCESSIVE_USE)

    async def set_group(self, group: UserGroup):
        await self.put('group', group)

    async def get_capability(self) -> int:
        return await self.get('capability')

    async def set_capability(self, capability: int):
        capability = UserCapability(capability)
        await self.put('capability', capability.value)

    async def has_capability(self, capability: int) -> bool:
        return UserCapability(capability) in UserCapability(await self.get_capability())

    async def reset_uuid(self):
        await self.put('uuid', str(uuid.uuid4()))

    async def prepare_reset_uuid(self) -> str:
        new_uuid = str(uuid.uuid4())
        await self.put('pending_reset_uuid', new_uuid)
        return new_uuid

    async def apply_reset_uuid(self) -> str:
        new_uuid = await self.get('pending_reset_uuid')
        await self.put('uuid', new_uuid)
        return new_uuid

    async def get_uuid(self) -> str:
        return await self.get('uuid')

    async def reset_notification(self):
        await self.put(
            'disabled_notification',
            (
                UserNotification.WEEKLY_USAGE_REMINDER
                | UserNotification.MONTHLY_USAGE_REMINDER
                | UserNotification.SERVICE_RESTORATION_NOTICE
            ).value,
        )

    async def get_disabled_notification(self) -> int:
        return await self.get('disabled_notification')

    async def _set_disabled_notification(self, notifications: UserNotification):
        await self.put('disabled_notification', notifications.value)

    async def disable_notification(self, notification: int):
        notification = UserNotification(notification)
        await self._set_disabled_notification(
            UserNotification(await self.get_disabled_notification()) | notification
        )

    async def enable_notification(self, notification: int):
        notification = UserNotification(notification)
        await self._set_disabled_notification(
            UserNotification(await self.get_disabled_notification()) & ~notification
        )

    async def toggle_notification(self, notification: int):
        notification = UserNotification(notification)
        if await self.is_notification_enabled(notification):
            await self.disable_notification(notification)
        else:
            await self.enable_notification(notification)

    async def is_notification_enabled(self, notification: int):
        notification = UserNotification(notification)
        return notification not in UserNotification(await self.get_disabled_notification())

    async def deduplicate_notification_day_once(self, text: str) -> bool:
        today = get_date_now()
        key = 'notification:sent_keys'
        sent_keys = json.loads(await self.get_daily_storage_area(today, key) or '[]')
        if text in sent_keys:
            return False
        sent_keys.append(text)
        await self.set_daily_storage_area(today, key, json.dumps(sent_keys))
        return True

    async def get_flatten_node_cache(self) -> str:
        return await self.get('flatten_node_cache')

    async def set_flatten_node_cache(self, cache: str):
        await self.put('flatten_node_cache', cache)

    async def clear_flatten_node_cache(self):
        cache = FlattenNodeCache()
        await self.set_flatten_node_cache(cache.serialize())

    async def get_daily_storage_area(self, date: str, key: str):
        data = await self.get(f'daily_storage_area:{date}:{key}')
        return json.loads(data) if data else None

    async def get_daily_storage_area_batched(self, dates: list[str], key: str):
        ret = []
        items = (await self.dump()).items()
        for date in dates:
            val = None
            for key_, value in items:
                if key_ == f'daily_storage_area:{date}:{key}':
                    val = json.loads(value)
            ret.append(val)
        return ret

    async def set_daily_storage_area(self, date: str, key: str, value):
        await self.put(f'daily_storage_area:{date}:{key}', json.dumps(value))

    async def push_today_to_daily_storage_area(self) -> bool:
        # for key in await self.ctx.storage.list():
        #    if key.startswith('daily_storage_area:2026-06-29:usage:'):
        #        await self.ctx.storage.delete(key)
        today = get_date_now()
        if await self.get_daily_storage_area(today, '_default') == 1:
            return False
        await self.set_daily_storage_area(today, '_default', 1)
        return True

    async def _record_usage_stats(self, date: str, tag: str, updown: str, value: int) -> int:
        tag = f'usage:{tag}:{updown}'
        value += await self.get_daily_storage_area(date, tag) or 0
        await self.set_daily_storage_area(date, tag, value)
        return value

    async def record_usage_stats(self, date: str, tag: str, up_value: int, down_value: int):
        up = await self._record_usage_stats(date, tag, 'up', up_value)
        down = await self._record_usage_stats(date, tag, 'down', down_value)
        return [up, down]  # Tuple is unpackable.

    async def query_usage_stats_daily(self, date: str, tags: list[str]):
        ret = []
        for tag in tags:
            ret.append(
                {
                    'tag': tag,
                    'up': await self.get_daily_storage_area(date, f'usage:{tag}:up'),
                    'down': await self.get_daily_storage_area(date, f'usage:{tag}:down'),
                }
            )
        return ret

    async def _query_usage_stats_batched(self, dates: list[date_], tags: list[str]):
        ret = []
        dates = [date2str(x) for x in dates]
        for tag in tags:
            up_batched = await self.get_daily_storage_area_batched(dates, f'usage:{tag}:up')
            down_batched = await self.get_daily_storage_area_batched(dates, f'usage:{tag}:down')
            assert len(dates) == len(up_batched) == len(down_batched)
            stat = {'tag': tag, 'up': 0, 'down': 0}
            for index, day_str in enumerate(dates):
                stat['up'] += up_batched[index] or 0
                stat['down'] += down_batched[index] or 0
            ret.append(stat)
        return ret

    async def query_usage_stats_weekly(self, date: str, tags: list[str]):
        return await self._query_usage_stats_batched(get_weekdays(str2date(date)), tags)

    async def query_usage_stats_monthly(self, date: str, tags: list[str]):
        return await self._query_usage_stats_batched(get_monthdays(str2date(date)), tags)
