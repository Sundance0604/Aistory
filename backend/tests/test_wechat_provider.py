import json
import sqlite3

import pytest

from gpt_activity.analytics import conversation_rankings, refresh_analytics, summary
from gpt_activity.config import load_settings
from gpt_activity.db import connect
from gpt_activity.providers.wechat.common import md5_username
from gpt_activity.providers.wechat.sync import run_wechat_sync
from gpt_activity.topics import classify_prompts
from gpt_activity.usage_time import _events


class FakeWeChatDB:
    def __init__(self, root, self_wxid="wxid_self"):
        self.root = root
        self.self_wxid = self_wxid
        self._db_files = [("contact.db", str(root / "contact.db"), None)]
        with self._open("contact.db") as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS contact(username TEXT PRIMARY KEY,nick_name TEXT,remark TEXT)")
            conn.executemany(
                "INSERT OR REPLACE INTO contact VALUES(?,?,?)",
                [
                    ("wxid_teacher", "Zhang", "章老师"),
                    ("wxid_alice", "Alice", ""),
                    ("group@chatroom", "同学群", ""),
                ],
            )
        with self._open("message_0.db") as conn:
            for wxid in ("wxid_teacher", "group@chatroom"):
                conn.execute(
                    f'CREATE TABLE IF NOT EXISTS "Msg_{md5_username(wxid)}"('
                    "local_id INTEGER PRIMARY KEY,local_type INTEGER,real_sender_id INTEGER,"
                    "create_time INTEGER,message_content BLOB,source BLOB,packed_info_data BLOB,"
                    "compress_content BLOB,sort_seq INTEGER)"
                )
            conn.execute(
                f'CREATE TABLE IF NOT EXISTS "Msg_{md5_username("filehelper")}"(real_sender_id INTEGER)'
            )
            conn.execute(f'DELETE FROM "Msg_{md5_username("filehelper")}"')
            conn.execute(f'INSERT INTO "Msg_{md5_username("filehelper")}" VALUES(8)')

    def _message_dbs(self):
        return ["message_0.db"]

    def _open(self, rel):
        return sqlite3.connect(self.root / rel)

    def get_self_info(self):
        return {"username": self.self_wxid, "nickname": "Lau"}

    def get_groups(self):
        return [{"username": "group@chatroom", "name": "同学群"}]

    def get_group_members(self, _wxid):
        return [{"username": "wxid_alice", "nick_name": "Alice"}]

    def add_private(self, start, count, *, outbound=False):
        with self._open("message_0.db") as conn:
            conn.executemany(
                f'INSERT INTO "Msg_{md5_username("wxid_teacher")}" VALUES(?,?,?,?,?,?,?,?,?)',
                [
                    (index, 1, 8 if outbound else 7, 1700000000 + index, f"消息 {index}", None, None, None, index)
                    for index in range(start, start + count)
                ],
            )

    def add_group(self, local_id=1):
        with self._open("message_0.db") as conn:
            conn.execute(
                f'INSERT INTO "Msg_{md5_username("group@chatroom")}" VALUES(?,?,?,?,?,?,?,?,?)',
                (local_id, 1, 7, 1700100000 + local_id, "wxid_alice:\n群消息", None, None, None, local_id),
            )


def settings_for(tmp_path, accounts):
    config = tmp_path / "config.local.json"
    config.write_text(
        json.dumps(
            {
                "storage": {"database_path": "aistory.db", "raw_conversations_dir": "raw"},
                "accounts": accounts,
                "usage_time": {"min_model_samples": 1000},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return load_settings(config)


def account(account_id, data_dir, alias="主号"):
    return {
        "id": account_id,
        "name": "Lau",
        "alias": alias,
        "provider": "wechat",
        "wechat_data_dir": str(data_dir),
        "enabled": True,
    }


def test_private_group_incremental_and_sender_display(tmp_path, monkeypatch):
    source_dir = tmp_path / "wx"
    source_dir.mkdir()
    fake = FakeWeChatDB(source_dir)
    fake.add_private(1, 100)
    fake.add_group()
    settings = settings_for(tmp_path, [account("wechat_main", source_dir)])
    monkeypatch.setattr("gpt_activity.providers.wechat.sync.open_wechat", lambda _path: fake)

    first = run_wechat_sync(settings, settings.accounts[0])
    assert first["messages"] == 101
    fake.add_private(101, 20)
    second = run_wechat_sync(settings, settings.accounts[0])
    assert second["messages"] == 20
    assert run_wechat_sync(settings, settings.accounts[0])["messages"] == 0

    refresh_analytics(settings.database_path, settings.timezone, settings.values["analytics"])
    rows = conversation_rankings(settings.database_path, provider="wechat", sort="total_messages")
    private = next(row for row in rows if row["conversation_type"] == "private")
    group = next(row for row in rows if row["conversation_type"] == "group")
    assert private["title"] == "章老师"
    assert private["inbound_messages"] == 120
    assert group["title"] == "同学群"
    with connect(settings.database_path) as conn:
        speaker = conn.execute(
            "SELECT sender_display_name FROM messages WHERE conversation_id=?", (group["id"],)
        ).fetchone()["sender_display_name"]
        checkpoint = conn.execute(
            "SELECT state_json FROM provider_sync_state WHERE provider='wechat' AND account_id='wechat_main' AND source_id='wxid_teacher'"
        ).fetchone()
    assert speaker == "Alice"
    assert json.loads(checkpoint["state_json"])["shards"]


def test_same_named_accounts_and_message_ids_do_not_collide(tmp_path, monkeypatch):
    main_dir, alt_dir = tmp_path / "main", tmp_path / "alt"
    main_dir.mkdir(); alt_dir.mkdir()
    main = FakeWeChatDB(main_dir, "wxid_A")
    alt = FakeWeChatDB(alt_dir, "wxid_B")
    main.add_private(1, 1); alt.add_private(1, 1)
    accounts = [account("wechat_main", main_dir, "主号"), account("wechat_alt", alt_dir, "副号")]
    settings = settings_for(tmp_path, accounts)
    monkeypatch.setattr(
        "gpt_activity.providers.wechat.sync.open_wechat",
        lambda path: main if str(path) == str(main_dir) else alt,
    )
    for item in settings.accounts:
        run_wechat_sync(settings, item)
    with connect(settings.database_path) as conn:
        assert conn.execute("SELECT COUNT(*) n FROM accounts WHERE provider='wechat'").fetchone()["n"] == 2
        assert conn.execute("SELECT COUNT(DISTINCT id) n FROM messages").fetchone()["n"] == 2
        assert conn.execute("SELECT COUNT(*) n FROM conversations").fetchone()["n"] == 2
        assert conn.execute("SELECT COUNT(DISTINCT id) n FROM conversations WHERE remote_id='wxid_teacher'").fetchone()["n"] == 2


def test_wrong_data_directory_is_rejected(tmp_path, monkeypatch):
    source_dir = tmp_path / "wx"
    source_dir.mkdir()
    original = FakeWeChatDB(source_dir, "wxid_A")
    settings = settings_for(tmp_path, [account("wechat_main", source_dir)])
    monkeypatch.setattr("gpt_activity.providers.wechat.sync.open_wechat", lambda _path: original)
    run_wechat_sync(settings, settings.accounts[0])
    wrong = FakeWeChatDB(source_dir, "wxid_B")
    monkeypatch.setattr("gpt_activity.providers.wechat.sync.open_wechat", lambda _path: wrong)
    with pytest.raises(RuntimeError, match="另一个微信账号"):
        run_wechat_sync(settings, settings.accounts[0])


def test_topics_skip_wechat_and_inbound_does_not_create_usage_event(tmp_path, monkeypatch):
    source_dir = tmp_path / "wx"
    source_dir.mkdir()
    fake = FakeWeChatDB(source_dir)
    fake.add_private(1, 3)
    settings = settings_for(tmp_path, [account("wechat_main", source_dir)])
    monkeypatch.setattr("gpt_activity.providers.wechat.sync.open_wechat", lambda _path: fake)
    run_wechat_sync(settings, settings.accounts[0])

    calls = []
    class Classifier:
        classifier_name = "fake"
        classifier_version = "fake:v1"
        def __init__(self, _settings): pass
        def classify(self, request, should_cancel=None):
            calls.append(request)
            raise AssertionError("WeChat content reached classifier")
        def close(self): pass
    monkeypatch.setattr("gpt_activity.topics.DeepSeekTopicClassifier", Classifier)
    result = classify_prompts(settings)
    assert result["queued"] == 0
    assert calls == []
    assert _events(settings.database_path) == []
    refresh_analytics(settings.database_path, settings.timezone, settings.values["analytics"])
    metrics = summary(settings.database_path, provider="wechat")
    assert metrics["total_messages"] == 3
    assert metrics["inbound_messages"] == 3
    assert metrics["outbound_messages"] == 0
