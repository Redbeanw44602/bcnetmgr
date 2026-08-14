from datetime import datetime, timedelta

from workers import Response
from telegram import Update, Chat, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import TelegramError, BadRequest

from user import (
    UserDurableObject,
    UserLanguage,
    UserGroup,
    UserCapability,
    UserNotification,
    FlattenNode,
    FlattenNodeCache,
)
from serv import (
    ServList,
    InboundReality,
    InboundXHttp,
    AddUserResult,
    RemoveUserResult,
)
from http_code import ok
from constant import TERM_OF_SERVICE
from env import Env
from util import emoji_from_country, b2mb, s2dhms, get_date_now, date2str, str2date
from cf import cf_api_client


async def handle_update(env: Env, payload) -> Response:
    # WORKAROUND FOR CF LIMITS: 'Cannot get entropy outside of request context...'

    from telegram.ext import (
        Application,
        ContextTypes,
        ApplicationHandlerStop,
        CommandHandler,
        TypeHandler,
        CallbackQueryHandler,
    )

    def print_debug_payload_content():
        import json

        print(json.dumps(payload))

    # UTILITIES

    def get_user(update: Update) -> UserDurableObject:
        return UserDurableObject.get_stub(env, update.effective_user.id)

    async def is_registered(update: Update, user: UserDurableObject = None) -> bool:
        user = user or get_user(update)
        return await user.is_registered()

    async def is_idle(update: Update, user: UserDurableObject = None) -> bool:
        user = user or get_user(update)
        if await user.get_allowed_callback():
            await update.message.reply_text(
                'You currently have other active processes running. If necessary, please /cancel them first.'
            )
            return False
        return True

    async def is_registered_and_idle(update: Update, user: UserDurableObject = None) -> bool:
        user = user or get_user(update)
        return await is_registered(update, user) and await is_idle(update, user)

    async def check_allowed_callback(update: Update, user: UserDurableObject = None) -> bool:
        user = user or get_user(update)
        return update.callback_query.data in await user.get_allowed_callback()

    def extract_last(callback: str) -> str:
        return callback[callback.rfind('_') + 1 :]

    def extract_last_number(callback: str) -> int:
        return int(extract_last(callback))

    # HANDLERS

    async def private_message_only(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type != Chat.PRIVATE:
            raise ApplicationHandlerStop

    async def eligibility_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            await context.bot.get_chat_member(env.USER_CHANNEL_ID, update.effective_user.id)
        except TelegramError:
            raise ApplicationHandlerStop

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = get_user(update)
        if await user.is_registered():
            await update.message.reply_text(
                '>_ <i>Available Commands</i>\n'
                '<b>Account</b>\n'
                '/me - Show account information.\n'
                '/rotate - Rotate your UUID.\n'
                '/notification - Manage notifications.\n'
                '/language - Change the chat language.\n'
                '/delete - Delete the account.\n'
                '/tos - Show the terms of use.\n'
                '<b>Connection</b>\n'
                '/link - Get available VLESS links.\n'
                '/status - Check the service status.\n'
                '/usage - Check usage statistics.\n'
                '<b>General</b>\n'
                '/start - Show this help.\n'
                '/cancel - Reset the context.\n'
                '/version - Show bot version.',
                parse_mode=ParseMode.HTML,
            )
        elif await is_idle(update, user):
            await update.message.reply_text(
                'Before starting the conversation, please select your preferred language.',
                reply_markup=InlineKeyboardMarkup.from_column(
                    [
                        InlineKeyboardButton(
                            lang.label, callback_data=f'registration_step_1_{lang.value}'
                        )
                        for lang in UserLanguage
                    ]
                ),
            )
            await user.set_allowed_callback(
                *[f'registration_step_1_{lang.value}' for lang in UserLanguage]
            )

    async def registration_step_1(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await check_allowed_callback(update):
            return
        user = get_user(update)
        query = update.callback_query
        await user.set_language(UserLanguage(extract_last_number(query.data)))
        await query.edit_message_text(
            f"Hello {update.effective_user.name}, it's great to see you!\n"
            'You are eligible to connect to the BigCat Network, but not yet registered. '
            'To begin registration, click the button below.',
            reply_markup=InlineKeyboardMarkup.from_button(
                InlineKeyboardButton('Register', callback_data='registration_step_2')
            ),
        )
        await user.set_allowed_callback('registration_step_2')

    async def registration_step_2(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await check_allowed_callback(update):
            return
        query = update.callback_query
        await query.edit_message_text(
            "Okay, I'll sign you up!\n"
            'To register with BigCat Network, you must understand and agree to a few simple terms.'
            '<blockquote>' + TERM_OF_SERVICE + '</blockquote>'
            '<blockquote>You can use /tos to view this again.</blockquote>',
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup.from_row(
                [
                    InlineKeyboardButton('Agree', callback_data='registration_step_3_agree'),
                    InlineKeyboardButton('Disagree', callback_data='registration_step_3_disagree'),
                ]
            ),
        )
        await get_user(update).set_allowed_callback(
            'registration_step_3_agree', 'registration_step_3_disagree'
        )

    async def registration_step_3(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await check_allowed_callback(update):
            return
        user = get_user(update)
        query = update.callback_query
        if query.data.endswith('_agree'):
            await user.register()
            await query.edit_message_text(
                'Congratulations! Your registration is complete. Try sending /start again.'
            )
        else:
            await query.edit_message_text(
                'Registration has been canceled. You can /start the registration process again.'
            )
        await user.set_allowed_callback(None)

    async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = get_user(update)
        await user.set_allowed_callback(None)
        await update.message.reply_text('Your context has been cleared.')

    async def version(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await is_registered(update):
            return
        client = cf_api_client(env)
        response = client.get(
            f'https://api.cloudflare.com/client/v4/accounts/{env.CF_ACCOUNT_ID}/workers/scripts/{env.CF_SCRIPT_NAME}/deployments'
        ).json()
        deployment = response['result']['deployments'][0]
        ver_id = deployment['versions'][0]['version_id'][:8]
        time = deployment['created_on']
        time = datetime.strptime(time, '%Y-%m-%dT%H:%M:%S.%fZ')
        time = time.strftime('%Y%m%dT%H%M%S') + 'Z'
        await update.message.reply_text(
            f'<b>bcnetmgr</b> v0.0.1+cfid.{ver_id}.tm.{time}', parse_mode=ParseMode.HTML
        )

    async def me(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await is_registered(update):
            return
        user = get_user(update)
        await update.message.reply_text(
            f'👤 User <code>{update.effective_user.id}</code>\n'
            '<blockquote>'
            f'<b>Language:</b> {UserLanguage(await user.get_language()).label}\n'
            f'<b>Group:</b> {UserGroup(await user.get_group()).label}\n'
            f'<b>Capabilities:</b> `{UserCapability(await user.get_capability()).name}`\n'
            '</blockquote>',
            parse_mode=ParseMode.HTML,
        )

    async def rotate(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await is_registered_and_idle(update):
            return
        await update.message.reply_text(
            '<b>Are you sure you want to rotate your UUID?</b>\n'
            'This will be helpful if your link is accidentally shared with a stranger. '
            'Once you rotate the UUID, the old one will become invalid immediately, so please proceed with caution.',
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup.from_row(
                [
                    InlineKeyboardButton('Yes', callback_data='rotation_step_1_yes'),
                    InlineKeyboardButton('No', callback_data='rotation_step_1_no'),
                ]
            ),
        )
        await get_user(update).set_allowed_callback('rotation_step_1_yes', 'rotation_step_1_no')

    async def rotation_step_1(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await check_allowed_callback(update):
            return
        user = get_user(update)
        query = update.callback_query
        if query.data.endswith('_yes'):
            new_uuid = await user.prepare_reset_uuid()
            await query.edit_message_text(
                'Your new UUID will be:'
                f'<pre><code class="language-UUID">{new_uuid}</code></pre>'
                'Please note it somewhere and make sure you know how to change the UUID in your VPN client. You can also retrieve the link again later.\n'
                '<b>The old UUID will be invalidated immediately, which may cause you to lose your internet connection.</b>\n'
                'Have you noted it already?',
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup.from_row(
                    [
                        InlineKeyboardButton('No', callback_data='rotation_step_2_no'),
                        InlineKeyboardButton('Yes', callback_data='rotation_step_2_yes'),
                    ]
                ),
            )
            await user.set_allowed_callback('rotation_step_2_no', 'rotation_step_2_yes')
        else:
            await query.edit_message_text('Your UUID has not been changed.')
            await user.set_allowed_callback(None)

    async def rotation_step_2(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await check_allowed_callback(update):
            return
        query = update.callback_query
        user_id = update.effective_user.id
        user = get_user(update)
        if query.data.endswith('_yes'):
            serv_mgr = ServList(env)
            uuid = await user.apply_reset_uuid()
            await user.clear_flatten_node_cache()
            for server, inbound in serv_mgr.get_inbounds():
                result = inbound.remove_user(user_id)
                if result not in (RemoveUserResult.SUCCESS, RemoveUserResult.NOT_FOUND):
                    await update.message.reply_text(
                        f'Something went wrong, RemoveUserResult = {result}'
                    )
                    return
                if await user.is_suspended():
                    continue
                result = inbound.add_user(user_id, uuid, inbound.default_flow)
                if result != AddUserResult.SUCCESS:
                    await update.message.reply_text(
                        f'Something went wrong, AddUserResult = {result}'
                    )
                    return
            await query.edit_message_text('Your UUID has been rotated.')
        else:
            await query.edit_message_text('Your UUID has not been changed.')
        await user.set_allowed_callback(None)

    async def _notification_make_keyboard(user: UserDurableObject) -> list:
        keyboard = []
        disabled_notification = UserNotification(await user.get_disabled_notification())
        for no in UserNotification:
            keyboard.append(
                [
                    InlineKeyboardButton(
                        f'{no.label} {no in disabled_notification and "🚫" or "🔔"}',
                        callback_data=f'configure_notification_{no.value}',
                    )
                ]
            )
        keyboard.append(
            [
                InlineKeyboardButton('🔄 Reset', callback_data='configure_notification_reset'),
                InlineKeyboardButton('✅ Done', callback_data='configure_notification_done'),
            ]
        )
        return keyboard

    async def notification(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await is_registered_and_idle(update):
            return
        user = get_user(update)
        keyboard = await _notification_make_keyboard(user)
        await update.message.reply_text(
            '📢 <b><i>Notification Settings</i></b>'
            '<blockquote>'
            'Tap to turn specific notifications on or off. You can also turn off all notifications.'
            '</blockquote>',
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        await user.set_allowed_callback(
            *[f'configure_notification_{no.value}' for no in UserNotification]
            + ['configure_notification_reset', 'configure_notification_done']
        )

    async def configure_notification(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await check_allowed_callback(update):
            return
        user = get_user(update)
        query = update.callback_query
        callback = query.data
        if callback.endswith('_done'):
            await query.edit_message_text('Notification settings have been saved.')
            await user.set_allowed_callback(None)
            return
        if callback.endswith('_reset'):
            await user.reset_notification()
        else:
            notification = extract_last_number(callback)
            await user.toggle_notification(notification)
        keyboard = await _notification_make_keyboard(user)
        await query.answer()
        try:
            await query.edit_message_reply_markup(InlineKeyboardMarkup(keyboard))
        except BadRequest:
            pass  # SILENT ERROR: 'Message not modified...'

    async def language(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await is_registered_and_idle(update):
            return
        user = get_user(update)
        await update.message.reply_text(
            f'🌍 Current language: <code>{UserLanguage(await user.get_language()).name}</code>, change to...',
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup.from_column(
                [
                    InlineKeyboardButton(lang.label, callback_data=f'change_language_{lang.value}')
                    for lang in UserLanguage
                ]
                + [InlineKeyboardButton('❌ Cancel', callback_data='change_language_cancel')]
            ),
        )
        await user.set_allowed_callback(
            *[f'change_language_{lang.value}' for lang in UserLanguage] + ['change_language_cancel']
        )

    async def change_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await check_allowed_callback(update):
            return
        user = get_user(update)
        query = update.callback_query
        if query.data.endswith('_cancel'):
            await query.edit_message_text('Language settings have not been changed.')
        else:
            language = UserLanguage(extract_last_number(query.data))
            await user.set_language(language)
            await query.edit_message_text(
                f'Language settings have been changed to <b>{language.label}</b>.',
                parse_mode=ParseMode.HTML,
            )
        await user.set_allowed_callback(None)

    async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await is_registered_and_idle(update):
            return
        await update.message.reply_text(
            '<b>Are you sure you want to delete your account?</b>\n'
            'Most of the information will be deleted immediately. '
            'However, we may still retain your usage data for statistical analysis '
            '(for up to one month).\n'
            'You can re-register at any time. If you wish to forfeit your eligibility, '
            'please unsubscribe from the channel after the deletion is complete.',
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup.from_row(
                [
                    InlineKeyboardButton('Yes', callback_data='deletion_step_1_yes'),
                    InlineKeyboardButton('No', callback_data='deletion_step_1_no'),
                ]
            ),
        )
        await get_user(update).set_allowed_callback('deletion_step_1_yes', 'deletion_step_1_no')

    async def deletion_step_1(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await check_allowed_callback(update):
            return
        user = get_user(update)
        query = update.callback_query
        if query.data.endswith('_yes'):
            await query.edit_message_text(
                'Final confirmation. To delete your account, click <b>Yes</b>.',
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup.from_row(
                    [
                        InlineKeyboardButton('No', callback_data='deletion_step_2_no'),
                        InlineKeyboardButton('Yes', callback_data='deletion_step_2_yes'),
                    ]
                ),
            )
            await user.set_allowed_callback('deletion_step_2_no', 'deletion_step_2_yes')
        else:
            await query.edit_message_text('Account deletion has been canceled.')
            await user.set_allowed_callback(None)

    async def deletion_step_2(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await check_allowed_callback(update):
            return
        query = update.callback_query
        user = get_user(update)
        if query.data.endswith('_yes'):
            serv_mgr = ServList(env)
            for server, inbound in serv_mgr.get_inbounds():
                result = inbound.remove_user(update.effective_user.id)
                if result not in (RemoveUserResult.SUCCESS, RemoveUserResult.NOT_FOUND):
                    await update.message.reply_text(
                        f'Something went wrong, RemoveUserResult = {result}'
                    )
                    return
            await user.delete()
            await query.edit_message_text('Your account has been deleted.')
        else:
            await query.edit_message_text('Account deletion has been canceled.')
        await user.set_allowed_callback(None)

    async def tos(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await is_registered(update):
            return
        await update.message.reply_text(TERM_OF_SERVICE, parse_mode=ParseMode.HTML)

    async def link(update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Check-up
        query = update.callback_query
        if query:
            if not await check_allowed_callback(update):
                return
        else:
            if not await is_registered_and_idle(update):
                return

        # Get/Construct flatten nodes
        user_id = update.effective_user.id
        user = get_user(update)

        cache = FlattenNodeCache()
        cache_saved = await user.get_flatten_node_cache()
        if cache_saved:
            cache.deserialize(cache_saved)
        nodes = cache.nodes
        if cache.version < env.NODE_CONFIG_VERSION:
            nodes.clear()
            serv_mgr = ServList(env)
            for server, inbound in serv_mgr.get_inbounds():
                if not inbound.has_user(user_id) and not await user.is_suspended():
                    result = inbound.add_user(user_id, await user.get_uuid(), inbound.default_flow)
                    if result != AddUserResult.SUCCESS:
                        await update.message.reply_text(
                            f'Something went wrong, AddUserResult = {result}'
                        )
                        return
                if isinstance(inbound, InboundReality):
                    v4_link, v6_link = inbound.generate_share_url(user_id)
                    if v4_link:
                        nodes.append(FlattenNode.create(inbound, v4_link))
                    if v6_link:
                        nodes.append(FlattenNode.create(inbound, v6_link, inbound.name + '-IPv6'))
                if isinstance(inbound, InboundXHttp):
                    link = inbound.generate_share_url(user_id)
                    nodes.append(FlattenNode.create(inbound, link))
            cache.version = env.NODE_CONFIG_VERSION
            await user.set_flatten_node_cache(cache.serialize())
        if not nodes:
            await update.message.reply_text('Sorry, there are currently no available nodes.')
            return

        # Handler for cancel
        current = nodes[0]
        if query:
            if query.data.endswith('_cancel'):
                await query.edit_message_text('Message destroyed.')
                await user.set_allowed_callback(None)
                return
            index = extract_last_number(query.data)
            if index < len(nodes):
                current = nodes[index]

        # Make keyboard & text
        text = (
            f'{emoji_from_country(current.region)} <b>Region:</b> {current.region} '
            f'{"<i>(Recommended)</i>" if current.recommended else ""}\n'
            f'<blockquote>{current.description}</blockquote>\n'
            f'<pre><code class="language-VLESS">{current.link}</code></pre>\n'
            '⚠️ This is your personal link; please do not share with others.'
        )
        keyboard = []
        for index, node in enumerate(nodes):
            recommended_mark = '✨' if node.recommended else ''
            current_mark = '➡️' if node == current else ''
            keyboard.append(
                InlineKeyboardButton(
                    f'{current_mark} {node.name} {recommended_mark}', callback_data=f'link_{index}'
                )
            )
        keyboard.append(InlineKeyboardButton('❌ Cancel', callback_data='link_cancel'))

        # Reply
        kwargs = {
            'text': text,
            'parse_mode': ParseMode.HTML,
            'reply_markup': InlineKeyboardMarkup.from_column(keyboard),
        }
        if not query:
            await update.message.reply_text(**kwargs)
        else:
            await query.answer()
            try:
                await query.edit_message_text(**kwargs)
            except BadRequest:
                pass  # SILENT ERROR: 'Message not modified...'

        await user.set_allowed_callback(*[f'link_{x}' for x in range(len(nodes))] + ['link_cancel'])

    async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await is_registered(update):
            return
        check_result = {}
        try:
            serv_mgr = ServList(env)
            for server in serv_mgr.get_servers():
                check_result[server.name] = ()
                try:
                    is_ok = server.health_check()
                    check_result[server.name] = (is_ok, server.get_sys_stats() if is_ok else None)
                except Exception:
                    raise
                    # pass
        except Exception:
            raise
            # pass
        if not check_result:
            await update.message.reply_text(
                '🔴 <b>The system may be down.</b>', parse_mode=ParseMode.HTML
            )
            return
        has_error = False
        text = ''
        for name, result in check_result.items():
            if not result:
                has_error = True
                text += f'\n<b>|NOT OK|</b> {name}\n'
                continue
            is_ok, sys_stats = result
            d, h, m, s = s2dhms(sys_stats.uptime)
            text += (
                f'\n<b>|{"OK" if is_ok else "NOT OK"}|</b> {name}\n'
                '<pre><code class="language-Stat">'
                f'Uptime               : {d}d {h}h {m}m {s}s\n'
                f'Number of Goroutines : {sys_stats.number_of_goroutines}\n'
                f'Number of GC cycles  : {sys_stats.number_of_gc_cycles}\n'
                f'Heap allocated       : {b2mb(sys_stats.heap_allocated):.2f} MiB\n'
                f'Virtual allocated    : {b2mb(sys_stats.virtual_allocated):.2f} MiB\n'
                f'Live objects         : {sys_stats.live_objects}\n'
                f'GC pause duration    : {sys_stats.gc_pause_duration:,} ns\n'
                '</code></pre>'
            )
        if has_error:
            text = '🟡 <b>Some nodes are malfunctioning.</b>\n' + text
        else:
            text = '🟢 <b>All nodes are currently operational.</b>\n' + text
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

    async def usage(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        if query:
            if not await check_allowed_callback(update):
                return
        else:
            if not await is_registered_and_idle(update):
                return
        user = get_user(update)
        serv_mgr = ServList(env)
        tags, names = serv_mgr.get_tags(), serv_mgr.get_names()
        assert len(tags) == len(names)
        today = get_date_now()
        YMD = '%B %d, %Y'  # TODO: Set global constants.
        YM = '%B %Y'
        current_date = today
        current_mode = 'daily'
        current_date_str = current_date.strftime(YMD)
        if query:
            callback = query.data.split('_')
            assert len(callback) == 4
            action = callback[1]
            current_mode = callback[2]
            current_date = str2date(callback[3])
            match action:
                case 'backward' | 'forward':
                    sign = 1 if action == 'forward' else -1
                    match current_mode:
                        case 'daily':
                            current_date += timedelta(days=sign)
                        case 'weekly':
                            current_date += timedelta(weeks=sign)
                        case 'monthly':
                            if sign > 0 and current_date.month == 12:
                                current_date = current_date.replace(month=1)
                                current_date = current_date.replace(year=current_date.year + sign)
                            if sign < 0 and current_date.month == 1:
                                current_date = current_date.replace(month=12)
                                current_date = current_date.replace(year=current_date.year + sign)
                            else:
                                current_date = current_date.replace(month=current_date.month + sign)
                case 'reset':
                    current_date = today
                    current_mode = 'daily'
                case 'cancel':
                    await query.edit_message_text('Message destroyed.')
                    await user.set_allowed_callback(None)
                    return
                case 'daily' | 'weekly' | 'monthly':
                    current_mode = action
                case _:
                    assert False
            no_data = False
            match current_mode:
                case 'daily':
                    current_date_str = current_date.strftime(YMD)
                    no_data = current_date > today
                case 'weekly':
                    start = current_date - timedelta(days=current_date.weekday())
                    end = start + timedelta(days=7)
                    current_date_str = f'{start.strftime(YMD)} - {end.strftime(YMD)}'
                    no_data = start > today
                case 'monthly':
                    current_date_str = current_date.strftime(YM)
                    no_data = current_date.replace(day=1) > today
                case _:
                    assert False
            if no_data:
                await query.answer('We cannot foresee the future.', show_alert=False)
                return
        text = (
            '📊 <i><b>Usage Statistics</b></i>\n\n'
            f'{current_date_str} {"(Today)" if current_mode == "daily" and current_date == today else ""}'
        )
        query_stats = None
        match current_mode:
            case 'daily':
                query_stats = user.query_usage_stats_daily
            case 'weekly':
                query_stats = user.query_usage_stats_weekly
            case 'monthly':
                query_stats = user.query_usage_stats_monthly
            case _:
                assert False
        for index, result in enumerate(await query_stats(date2str(current_date), list(tags))):
            up, down = result['up'], result['down']
            up = f'{b2mb(up):.2f}' if up else '?'
            down = f'{b2mb(down):.2f}' if down else '?'
            text += f'\n<blockquote><b>{names[index]}</b> ⬆️ {up} MiB ⬇️ {down} MiB</blockquote>'

        allowed_callbacks = []

        def button(title: str, callback: str, mode_button: bool = False):
            mode_emoji = ''
            if mode_button:
                mode_emoji = '📌' if callback == current_mode else '📅'
                mode_emoji += ' '
            callback = f'usage_{callback}_{current_mode}_{date2str(current_date)}'
            allowed_callbacks.append(callback)
            return InlineKeyboardButton(mode_emoji + title, callback_data=callback)

        kwargs = {
            'text': text,
            'parse_mode': ParseMode.HTML,
            'reply_markup': InlineKeyboardMarkup(
                [
                    [
                        button('◀', 'backward'),
                        button('▶', 'forward'),
                        button('🔁', 'reset'),
                        button('❌', 'cancel'),
                    ],
                    [
                        button('Daily', 'daily', True),
                        button('Weekly', 'weekly', True),
                        button('Monthly', 'monthly', True),
                    ],
                ]
            ),
        }
        if not query:
            await update.message.reply_text(**kwargs)
        else:
            await query.answer()
            try:
                await query.edit_message_text(**kwargs)
            except BadRequest:
                pass  # SILENT ERROR: 'Message not modified...'

        await user.set_allowed_callback(*allowed_callbacks)

    # print_debug_payload_content()  # DEBUG USE ONLY

    app = Application.builder().token(env.BOT_TOKEN).build()
    app.add_handler(TypeHandler(Update, private_message_only), -1)
    app.add_handler(TypeHandler(Update, eligibility_check), -1)
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CallbackQueryHandler(registration_step_1, r'^registration_step_1.*'))
    app.add_handler(CallbackQueryHandler(registration_step_2, r'^registration_step_2.*'))
    app.add_handler(CallbackQueryHandler(registration_step_3, r'^registration_step_3.*'))
    app.add_handler(CommandHandler('cancel', cancel))
    app.add_handler(CommandHandler('version', version))
    app.add_handler(CommandHandler('link', link))
    app.add_handler(CallbackQueryHandler(link, r'^link.*'))
    app.add_handler(CommandHandler('status', status))
    app.add_handler(CommandHandler('usage', usage))
    app.add_handler(CallbackQueryHandler(usage, r'^usage.*'))
    app.add_handler(CommandHandler('me', me))
    app.add_handler(CommandHandler('rotate', rotate))
    app.add_handler(CallbackQueryHandler(rotation_step_1, r'^rotation_step_1.*'))
    app.add_handler(CallbackQueryHandler(rotation_step_2, r'^rotation_step_2.*'))
    app.add_handler(CommandHandler('notification', notification))
    app.add_handler(CallbackQueryHandler(configure_notification, r'^configure_notification.*'))
    app.add_handler(CommandHandler('language', language))
    app.add_handler(CallbackQueryHandler(change_language, r'^change_language.*'))
    app.add_handler(CommandHandler('delete', delete))
    app.add_handler(CallbackQueryHandler(deletion_step_1, r'^deletion_step_1.*'))
    app.add_handler(CallbackQueryHandler(deletion_step_2, r'^deletion_step_2.*'))
    app.add_handler(CommandHandler('tos', tos))

    update = Update.de_json(payload, app.bot)

    async with app:
        await app.process_update(update)

    return ok()
