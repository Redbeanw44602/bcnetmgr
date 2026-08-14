import os
import sys

# PROTOC QUIRK
sys.path.append(os.path.join(os.path.dirname(__file__), 'xray/gen'))

import app.proxyman.config_pb2  # noqa: F401
import app.proxyman.command.command_pb2  # noqa: F401
import proxy.vless.inbound.config_pb2  # noqa: F401
import proxy.vless.account_pb2  # noqa: F401
import transport.internet.reality.config_pb2  # noqa: F401
import transport.internet.splithttp.config_pb2  # noqa: F401

# ------

import base64

from google.protobuf import symbol_database
from google.protobuf.json_format import MessageToDict, ParseDict


def decode_typed_message(data):
    return MessageToDict(
        symbol_database.Default()
        .GetSymbol(data['type'])
        .FromString(base64.b64decode(data['value']))
    )


def encode_typed_message(ttype, data):
    ttype = f'xray.{ttype}'
    msg = symbol_database.Default().GetSymbol(ttype)()
    ParseDict(data, msg)
    return {'type': ttype, 'value': base64.b64encode(msg.SerializeToString()).decode('utf-8')}
