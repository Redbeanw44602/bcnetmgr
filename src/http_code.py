from http import HTTPStatus
from workers import Response


def unauthorized(message: str = None):
    return Response(message, status=HTTPStatus.UNAUTHORIZED)


def not_found(message: str = None):
    return Response(message, status=HTTPStatus.NOT_FOUND)


def ok(message: str = None):
    return Response(message, status=HTTPStatus.OK)


def bad_request(message: str = None):
    return Response(message, status=HTTPStatus.BAD_REQUEST)


def internal_server_error(message: str = None):
    return Response(message, status=HTTPStatus.INTERNAL_SERVER_ERROR)
