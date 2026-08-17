"""プロトコル層の例外."""


class ProtocolError(Exception):
    """プロトコル違反全般."""


class ConnectionClosed(ProtocolError):
    """相手が接続を閉じた."""


class MessageTooLarge(ProtocolError):
    """メッセージが上限を超えた."""
