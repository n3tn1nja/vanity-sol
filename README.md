# vanity-sol
Vanity Solana Address Generator is a python utility for generating Solana wallet addresses that contain specific text patterns either at the beginning or the end of the address.

## Features

- Generate Solana addresses that start or end with specified text.

## Requirements

- `Python 3.6+`
- `solders` library
- `base58` library

## Usage

Run the script with the following command:

```bash
python vanity_sol.py -v [vanity_text] -m [max_matches] -e -i
```
### Options
-v, --vanity-text: Text to search for in the wallet address (required).
-m, --max-matches: Maximum number of matches to find before exiting (default: 1, not required).
-e, --match-end: Search for text at the end of the address instead of the beginning. (not required)
-i, --ignore-case: Ignore case when matching text. (not required)

#### Example
```bash
python vanity.py -v "Sol" -m 5 -i
```
This command will generate up to 5 Solana addresses that start with "Sol", ignoring case differences.