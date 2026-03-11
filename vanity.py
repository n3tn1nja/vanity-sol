#!/usr/bin/env python3

from multiprocessing import Process, Queue, current_process
import argparse
import json
import os
from solders.keypair import Keypair
import re
import sys
import base58
import signal

# Solana addresses use base58: no 0, O, I, l to avoid confusion
BASE58_ALPHABET = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")


def validate_vanity_text(vanity_text: str, ignore_case: bool) -> None:
    """Raise SystemExit if vanity contains characters that can't appear in a Solana address."""
    if not vanity_text:
        sys.exit("Error: Vanity text cannot be empty.")
    invalid = [c for c in vanity_text if c not in BASE58_ALPHABET]
    if invalid:
        bad = ", ".join(sorted(set(invalid)))
        sys.exit(
            f"Error: Vanity text contains characters not used in Solana addresses: {bad}. \n"
            f"Solana uses base58 (no 0, O, I, l). Use only: 1-9, A-H, J-N, P-Z, a-k, m-z."
        )


def main(vanity_text, max_matches, ignore_case, match_end, num_processes):
    validate_vanity_text(vanity_text, ignore_case)

    pluralized = "es" if max_matches > 1 else ""
    filename = f"{vanity_text}-vanity-address{pluralized}.json"

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Prepend '^' if matching at the start, append '$' if matching at the end
    if match_end:
        pattern = f"{vanity_text}$"
    else:
        pattern = f"^{vanity_text}"

    flags = re.IGNORECASE if ignore_case else 0
    print(
        f"Searching for vanity: {vanity_text}, ignoring case: {'yes' if ignore_case else 'no'}, match end: {'yes' if match_end else 'no'}, processes: {num_processes}"
    )
    start_processes(pattern, flags, filename, max_matches, num_processes)


def generate_vanity_addresses(pattern_str, pattern_flags, filename, max_matches, report_interval, queue=None):
    pattern_compiled = re.compile(pattern_str, pattern_flags)
    found = 0
    searched = 0
    process_id = current_process().name

    try:
        while found < max_matches:
            keypair = Keypair()
            pubkey = str(keypair.pubkey())
            searched += 1

            if searched % report_interval == 0:
                queue.put(("progress", process_id, searched))

            if pattern_compiled.search(pubkey):
                secret_b58 = base58.b58encode(bytes(keypair)).decode("utf-8")
                found += 1
                queue.put(("match", process_id, searched, pubkey, secret_b58))
                if found >= max_matches:
                    break
    finally:
        queue.put(("done", process_id, searched))


def signal_handler(sig, frame):
    print("Exiting gracefully")
    sys.exit(0)


def start_processes(pattern_str, pattern_flags, filename, max_matches, num_processes):
    processes = []
    queue = Queue()

    # Report progress less often with many processes to avoid queue bottleneck
    report_interval = max(10, (num_processes * 5) // 2)  # e.g. 10 for 4 procs, 50 for 20, 250 for 100

    base, remainder = divmod(max_matches, num_processes)
    matches_per_process = [base + (1 if i < remainder else 0) for i in range(num_processes)]

    for i in range(num_processes):
        p = Process(
            target=generate_vanity_addresses,
            args=(pattern_str, pattern_flags, filename, matches_per_process[i], report_interval, queue),
        )
        processes.append(p)
        p.start()

    active_processes = num_processes
    per_process_searched = {}
    try:
        while active_processes > 0:
            message = queue.get()
            kind = message[0]
            process_id, searched = message[1], message[2]
            per_process_searched[process_id] = searched

            if kind == "done":
                active_processes -= 1
            elif kind == "match":
                _, _, _, pubkey, secret_b58 = message
                vanity_address = {"public_key": pubkey, "secret_key": secret_b58}
                with open(filename, "a+") as f:
                    f.seek(0)
                    try:
                        data = json.load(f)
                    except json.JSONDecodeError:
                        data = []
                    data.append(vanity_address)
                    f.seek(0)
                    f.truncate()
                    json.dump(data, f, indent=4)
                print(f"{process_id} found: {pubkey} after {searched} searches")
            else:
                total = sum(per_process_searched.values())
                print(f"Searched {total} addresses", end="\r")
    finally:
        for p in processes:
            p.join()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Vanity-Sol - Generate Vanity Solana Wallet addresses."
    )
    parser.add_argument(
        "--vanity-text",
        "--vanity",
        "-v",
        type=str,
        required=True,
        help="The text to search for in the wallet address.",
    )
    parser.add_argument(
        "--max-matches",
        "--max",
        "-m",
        type=int,
        default=1,
        help="The number of matches to find before exiting",
    )
    parser.add_argument(
        "--match-end",
        "--end",
        "-e",
        action="store_true",
        help="Match the vanity text at the end of the address instead of the beginning",
    )
    parser.add_argument(
        "--ignore-case",
        "--ignore",
        "-i",
        action="store_true",
        help="Ignore case in text matching",
    )
    parser.add_argument(
        "--num-processes",
        "-n",
        type=int,
        default=None,
        help="Number of processes (default: CPU count). Using more than CPU count usually doesn't speed things up.",
    )

    args = parser.parse_args()
    num_processes = args.num_processes if args.num_processes is not None else (os.cpu_count() or 4)

    main(
        args.vanity_text,
        args.max_matches,
        args.ignore_case,
        args.match_end,
        num_processes,
    )
