# Sherlock Quotes

A Bluesky bot that posts a quote from the Sherlock Holmes canon paired with a
matching Victorian-London photograph from the Library of Congress.

Posts to [@sherlockquotes.bsky.social](https://bsky.app/profile/sherlockquotes.bsky.social).

## How it works

Three scripts, run in sequence:

| Script | Role |
|---|---|
| `holmes_harvest.py` | Harvests quotes and prose passages from the public-domain Holmes canon (Project Gutenberg) into `holmes_quotes.json`. Extracts dialogue attributed to Holmes and other characters, plus Watson's narrative prose. Re-running merges into the existing file. |
| `holmes_images_harvest.py` | Harvests Victorian-London photo metadata (1870–1910) from the Library of Congress into `holmes_images.json`. |
| `holmes_post.py` | Picks an unposted quote, finds a thematically matching image, and posts both to Bluesky. Posted-state is tracked in `holmes_state.json`. |

Harvesting shells out to `curl`; posting uses [`atproto`](https://pypi.org/project/atproto/).

## Setup

```sh
pip install -r requirements.txt

# Store the Bluesky app password in the macOS Keychain:
security add-generic-password -a "sherlockquotes.bsky.social" -s "holmesbot-bluesky" -w
```

## Usage

```sh
python3 holmes_harvest.py          # build/refresh the quote pool
python3 holmes_images_harvest.py   # build/refresh the image pool
python3 holmes_post.py             # post one quote + image
python3 holmes_post.py --dry-run   # print the post without publishing
```

## Notes

- The harvested data pools (`holmes_quotes.json`, `holmes_images.json`) and the
  runtime state (`holmes_state.json`) are gitignored — rebuild them locally with
  the harvest scripts. A fresh clone therefore holds only the code.
- The Bluesky credential lives in the macOS Keychain, never in the repo.
- Source texts are public domain (Project Gutenberg); photographs are from the
  Library of Congress.

## License

MIT — see [LICENSE](LICENSE).
