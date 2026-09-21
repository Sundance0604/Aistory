from __future__ import annotations

import argparse
import json

from .analytics import summary
from .config import load_settings, upsert_account
from .db import connect, ensure_account, migrate
from .importer import import_json


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gpt_activity", description="Local-first ChatGPT activity analytics")
    parser.add_argument("--config", help="path to the single JSON configuration file")
    commands = parser.add_subparsers(dest="command", required=True)

    sync = commands.add_parser("sync", help="incrementally synchronize ChatGPT conversations")
    sync.add_argument("--full-index", action="store_true")
    sync.add_argument("--force-fetch", action="store_true")
    sync.add_argument("--include-files", action="store_true", help="explicitly opt into the account File Library sweep")
    sync.add_argument("--account", action="append", dest="accounts", help="account id; repeat to sync several sequentially")

    importer = commands.add_parser("import-json", help="import existing conversation.json files")
    importer.add_argument("path")
    importer.add_argument("--no-raw-copy", action="store_true")
    importer.add_argument("--account", default="default", help="owner account id")
    importer.add_argument("--account-name", help="display name when creating the account")

    accounts = commands.add_parser("accounts", help="manage sequential ChatGPT account profiles")
    account_commands = accounts.add_subparsers(dest="accounts_command", required=True)
    account_commands.add_parser("list")
    add_account = account_commands.add_parser("add")
    add_account.add_argument("id")
    add_account.add_argument("name")
    add_account.add_argument("--profile")

    commands.add_parser("analyze", help="print deterministic local summary")

    topics = commands.add_parser("topics", help="topic classification commands")
    topic_commands = topics.add_subparsers(dest="topics_command", required=True)
    classify_new = topic_commands.add_parser("classify-new")
    classify_new.add_argument("--limit", type=int)
    reclassify = topic_commands.add_parser("reclassify")
    reclassify.add_argument("--limit", type=int)

    serve = commands.add_parser("serve", help="run the local dashboard")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--reload", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings(args.config)
    migrate(settings.database_path)
    if args.command == "import-json":
        result = import_json(
            settings,
            args.path,
            keep_raw=not args.no_raw_copy,
            account_id=args.account,
            account_name=args.account_name,
        )
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
        return 1 if result.failed else 0
    if args.command == "sync":
        from .sync import run_sync

        result = run_sync(
            settings,
            full_index=args.full_index,
            force_fetch=args.force_fetch,
            include_files=args.include_files,
            account_ids=args.accounts,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result.get("failed_conversations") else 0
    if args.command == "accounts":
        if args.accounts_command == "list":
            print(json.dumps(settings.accounts, ensure_ascii=False, indent=2))
            return 0
        profile = args.profile or f"browser_profiles/{args.id}"
        saved = upsert_account(
            settings,
            {"id": args.id, "name": args.name, "browser_profile": profile, "enabled": True},
        )
        ensure_account(saved.database_path, args.id, args.name)
        print(json.dumps(saved.account(args.id), ensure_ascii=False, indent=2))
        return 0
    if args.command == "analyze":
        print(json.dumps(summary(settings.database_path, settings.timezone), ensure_ascii=False, indent=2))
        return 0
    if args.command == "topics":
        from .topics import classify_prompts

        result = classify_prompts(
            settings,
            reclassify=args.topics_command == "reclassify",
            limit=args.limit,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result["failed"] else 0
    if args.command == "serve":
        import uvicorn

        cfg = settings.values["app"]
        uvicorn.run(
            "gpt_activity.api:create_app",
            factory=True,
            host=args.host or cfg["host"],
            port=args.port or int(cfg["port"]),
            reload=args.reload,
        )
        return 0
    return 2
