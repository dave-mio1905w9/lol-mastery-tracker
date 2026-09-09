import argparse
import os
import sqlite3
import sys
from pathlib import Path

from riot_api import RiotAPI, RiotAPIError

def get_db_path():
    env = os.environ.get("TRACKER_DB")
    if env:
        return Path(env)
    return Path.home() / ".local" / "share" / "lol-tracker" / "accounts.db"

def init_db(db_path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY,
            game_name TEXT NOT NULL,
            tag_line TEXT NOT NULL,
            puuid TEXT NOT NULL UNIQUE,
            region TEXT NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mastery (
            id INTEGER PRIMARY KEY,
            account_id INTEGER NOT NULL,
            champion_id INTEGER NOT NULL,
            champion_name TEXT,
            mastery_level INTEGER NOT NULL,
            mastery_points INTEGER NOT NULL,
            tokens_earned INTEGER DEFAULT 0,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(account_id, champion_id)
        )
    """)
    conn.commit()
    return conn

def add_account(args):
    api_key = os.environ.get("RIOT_API_KEY")
    if not api_key:
        print("set RIOT_API_KEY env var", file=sys.stderr)
        sys.exit(2)

    db_path = get_db_path()
    conn = init_db(db_path)

    api = RiotAPI(api_key, args.region)
    try:
        puuid = api.get_puuid(args.game_name, args.tag_line)
    except RiotAPIError as e:
        print(f"riot api error: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        conn.execute(
            "INSERT INTO accounts (game_name, tag_line, puuid, region) VALUES (?, ?, ?, ?)",
            (args.game_name, args.tag_line, puuid, args.region)
        )
        conn.commit()
        print(f"added {args.game_name}#{args.tag_line} ({args.region})")
    except sqlite3.IntegrityError:
        print("account already tracked", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()

def list_accounts(args):
    db_path = get_db_path()
    conn = init_db(db_path)
    cur = conn.execute("SELECT game_name, tag_line, region, added_at FROM accounts ORDER BY added_at")
    rows = cur.fetchall()
    conn.close()

    if not rows:
        print("no accounts tracked. add one with: tracker add-account <name> <tag> <region>")
        return 0

    for name, tag, region, added in rows:
        print(f"{name}#{tag}  ({region})  added {added}")

def fetch(args):
    api_key = os.environ.get("RIOT_API_KEY")
    if not api_key:
        print("set RIOT_API_KEY env var", file=sys.stderr)
        sys.exit(2)

    db_path = get_db_path()
    conn = init_db(db_path)

    cur = conn.execute("SELECT id, game_name, tag_line, puuid, region FROM accounts")
    accounts = cur.fetchall()

    if not accounts:
        print("no accounts tracked. add one with --add-account")
        conn.close()
        return 0

    for acc_id, name, tag, puuid, region in accounts:
        api = RiotAPI(api_key, region)
        try:
            mastery_data = api.get_champion_mastery(puuid)
        except RiotAPIError as e:
            print(f"failed to fetch {name}#{tag}: {e}", file=sys.stderr)
            continue

        for entry in mastery_data:
            conn.execute("""
                INSERT INTO mastery (account_id, champion_id, champion_name, mastery_level, mastery_points, tokens_earned)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, champion_id) DO UPDATE SET
                    mastery_level=excluded.mastery_level,
                    mastery_points=excluded.mastery_points,
                    tokens_earned=excluded.tokens_earned,
                    last_updated=CURRENT_TIMESTAMP
            """, (
                acc_id,
                entry["championId"],
                entry.get("championName"),
                entry["championLevel"],
                entry["championPoints"],
                entry.get("tokensEarned", 0)
            ))
        conn.commit()
        print(f"fetched {len(mastery_data)} champions for {name}#{tag}")

    conn.close()

def progress(args):
    db_path = get_db_path()
    conn = init_db(db_path)

    query = """
        SELECT a.game_name, a.tag_line, m.champion_name, m.mastery_level, m.mastery_points, m.tokens_earned
        FROM mastery m
        JOIN accounts a ON m.account_id = a.id
        WHERE m.mastery_level >= 5
    """
    if args.near_m7:
        query += " AND m.mastery_level = 6 AND m.tokens_earned < 3"
    query += " ORDER BY a.game_name, m.mastery_level DESC, m.mastery_points DESC"

    cur = conn.execute(query)
    rows = cur.fetchall()
    conn.close()

    if not rows:
        if args.near_m7:
            print("no champions close to mastery 7. run 'tracker progress' to see all m5+")
        else:
            print("no mastery data yet. run 'tracker fetch' first")
        return 0

    current_account = None
    for name, tag, champ, level, points, tokens in rows:
        account_label = f"{name}#{tag}"
        if account_label != current_account:
            print(f"\n{account_label}")
            current_account = account_label

        if level == 5:
            status = "needs S ranks"
        elif level == 6:
            remaining = 3 - tokens
            if remaining <= 0:
                continue
            status = f"needs {remaining} more S rank{'s' if remaining > 1 else ''}"
        else:
            continue

        print(f"  {champ}: mastery {level} ({points} pts) - {status}")

def main():
    parser = argparse.ArgumentParser(description="track lol mastery across accounts")
    sub = parser.add_subparsers(dest="command")

    add_parser = sub.add_parser("add-account", help="add a riot account")
    add_parser.add_argument("game_name")
    add_parser.add_argument("tag_line")
    add_parser.add_argument("region")
    add_parser.set_defaults(func=add_account)

    list_parser = sub.add_parser("list", help="list tracked accounts")
    list_parser.set_defaults(func=list_accounts)

    fetch_parser = sub.add_parser("fetch", help="pull latest mastery data from riot")
    fetch_parser.set_defaults(func=fetch)

    progress_parser = sub.add_parser("progress", help="show champions close to mastery 7")
    progress_parser.add_argument("--near-m7", action="store_true", dest="near_m7",
                                 help="only show mastery 6 champions still needing tokens")
    progress_parser.set_defaults(func=progress)

    args = parser.parse_args()
    if not args.command:
        parser.print_usage()
        sys.exit(2)

    args.func(args)

if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except KeyboardInterrupt:
        sys.exit(130)
