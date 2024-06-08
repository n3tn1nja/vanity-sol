import argparse
import json
from solders.keypair import Keypair  # type: ignore
import re
import sys
import base58
import signal


def main(vanity_text, max_matches, ignore_case, match_end):
    pluralized = "es" if max_matches > 1 else ""
    filename = f"{vanity_text}-vanity-address{pluralized}.json"

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Prepend '^' if matching at the start, append '$' if matching at the end
    if match_end:
        pattern = f"{vanity_text}$"
    else:
        pattern = f"^{vanity_text}"

    pattern_compiled = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    print(
        f"Searching for vanity: {vanity_text}, ignoring case: {'yes' if ignore_case else 'no'}, match end: {'yes' if match_end else 'no'}"
    )
    generate_vanity_addresses(pattern_compiled, filename, max_matches)


def generate_vanity_addresses(pattern_compiled, filename, max_matches):
    found = 0
    searched = 0

    while found < max_matches:
        keypair = Keypair()
        pubkey = str(keypair.pubkey())
        searched += 1

        print(f"Generated & Searched wallets: {searched}", end="\r")

        if pattern_compiled.search(pubkey):
            vanity_address = {
                "public_key": pubkey,
                "secret_key": base58.b58encode(bytes(keypair)).decode("utf-8"),
            }

            with open(filename, "a+") as file:
                file.seek(0)
                try:
                    data = json.load(file)
                except json.JSONDecodeError:
                    data = []

                data.append(vanity_address)
                file.seek(0)
                file.truncate()
                json.dump(data, file, indent=4)

            found += 1
            print(f"Match found: {pubkey}")

            if found >= max_matches:
                print("Found enough matches, exiting.")
                break
    print(f"Total Wallets Searched: {searched}")


def signal_handler(sig, frame):
    print("Exiting gracefully")
    sys.exit(0)


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
    args = parser.parse_args()

    main(args.vanity_text, args.max_matches, args.ignore_case, args.match_end)
