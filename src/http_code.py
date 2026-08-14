from http import HTTPStatus
from workers import Response


def unauthorized():
    return Response(status=HTTPStatus.UNAUTHORIZED)


def not_found():
    return Response(status=HTTPStatus.NOT_FOUND)


def ok(message=None):
    return Response(message, status=HTTPStatus.OK)


def bad_request(message=None):
    return Response(message, status=HTTPStatus.BAD_REQUEST)


def internal_server_error(message=None):
    return Response(message, status=HTTPStatus.INTERNAL_SERVER_ERROR)
