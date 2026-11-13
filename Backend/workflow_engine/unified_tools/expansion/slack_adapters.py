"""Scoped Slack message delivery. The bot token never enters a saved workflow."""
from __future__ import annotations

import json
import re
import socket
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


SLACK_POST_URL = "https://slack.com/api/chat.postMessage"
CHANNEL_ID = re.compile(r"[CGD][A-Z0-9]{8,24}\Z")


class SlackDeliveryUnknown(RuntimeError):
    """The request may have reached Slack; a blind retry could duplicate it."""


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class SlackJsonTransport:
    def __init__(self, token_provider, *, timeout=12, max_bytes=256_000, opener=None):
        self.token_provider = token_provider
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.opener = opener or build_opener(NoRedirects())

    def post_message(self, channel_id, text):
        if not isinstance(channel_id, str) or not CHANNEL_ID.fullmatch(channel_id):
            raise ValueError("Slack 채널 ID(C/G/D로 시작)를 확인해주세요.")
        if not isinstance(text, str) or not text.strip() or len(text) > 40_000 or "\x00" in text:
            raise ValueError("Slack 메시지는 비어 있지 않은 40,000자 이하의 텍스트여야 합니다.")
        token = self.token_provider()
        if not isinstance(token, str) or not token or len(token) > 32_000 or any(ord(c) < 33 or ord(c) == 127 for c in token):
            raise ValueError("Slack 계정을 다시 연결해주세요.")
        request = Request(
            SLACK_POST_URL,
            data=json.dumps({"channel": channel_id, "text": text, "mrkdwn": False,
                             "parse": "none", "unfurl_links": False, "unfurl_media": False},
                            ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": "Bearer " + token, "Accept": "application/json", "Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                body = response.read(self.max_bytes + 1)
        except HTTPError as error:
            if error.code == 429:
                raise RuntimeError("Slack 전송 한도에 도달했습니다. 잠시 후 결과를 확인하고 다시 실행해주세요.") from None
            if error.code in (401, 403):
                raise RuntimeError("Slack 연결 권한을 확인해주세요.") from None
            raise SlackDeliveryUnknown("Slack 전송 결과를 확인할 수 없습니다. 채널에서 메시지를 확인한 뒤 재실행해주세요.") from None
        except (URLError, TimeoutError, socket.timeout, OSError):
            raise SlackDeliveryUnknown("Slack 전송 결과를 확인할 수 없습니다. 채널에서 메시지를 확인한 뒤 재실행해주세요.") from None
        if len(body) > self.max_bytes:
            raise SlackDeliveryUnknown("Slack 응답이 너무 커서 전송 결과를 확인할 수 없습니다. 채널을 확인해주세요.")
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError):
            raise SlackDeliveryUnknown("Slack 전송 결과를 확인할 수 없습니다. 채널에서 메시지를 확인해주세요.") from None
        if not isinstance(payload, dict):
            raise SlackDeliveryUnknown("Slack 전송 결과를 확인할 수 없습니다. 채널에서 메시지를 확인해주세요.")
        if payload.get("ok") is not True:
            errors = {
                "channel_not_found": "Slack 채널 ID와 봇의 채널 참여 상태를 확인해주세요.",
                "not_in_channel": "Slack 봇을 해당 채널에 초대해주세요.",
                "invalid_auth": "Slack 계정을 다시 연결해주세요.",
                "token_revoked": "Slack 계정을 다시 연결해주세요.",
                "missing_scope": "Slack 앱의 메시지 전송 권한을 확인해주세요.",
                "is_archived": "보관된 Slack 채널에는 메시지를 보낼 수 없습니다.",
            }
            error_code = payload.get("error")
            raise RuntimeError(errors.get(error_code if isinstance(error_code, str) else "", "Slack 메시지를 보내지 못했습니다. 채널과 연결 권한을 확인해주세요."))
        channel = payload.get("channel")
        ts = payload.get("ts")
        if not isinstance(channel, str) or not CHANNEL_ID.fullmatch(channel) or not isinstance(ts, str) or not re.fullmatch(r"\d+\.\d+", ts):
            raise SlackDeliveryUnknown("Slack 전송 결과를 확인할 수 없습니다. 채널에서 메시지를 확인해주세요.")
        return {"status": "accepted_by_provider", "channel_id": channel, "ts": ts}


class SlackAdapter:
    def __init__(self, transport):
        self.transport = transport

    def __call__(self, operation, arguments):
        if operation != "slack.post_message":
            raise ValueError("지원하지 않는 Slack 동작입니다.")
        return self.transport.post_message(arguments["channel_id"], arguments["text"])
