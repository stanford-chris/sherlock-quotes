# Sherlock Quotes

A Bluesky bot that posts a quote from the Sherlock Holmes canon paired with one of
Sidney Paget's original illustrations for The Strand Magazine, matched to the story
the quote came from.

Posts to [@sherlockquotes.bsky.social](https://bsky.app/profile/sherlockquotes.bsky.social).

## How it works

Four scripts. `holmes_harvest.py` and `holmes_scenes_harvest.py` build the pools;
`holmes_post.py` posts from them:

| Script | Role |
|---|---|
| `holmes_harvest.py` | Harvests quotes and prose passages from the public-domain Holmes canon (Project Gutenberg) into `holmes_quotes.json`. Extracts dialogue attributed to Holmes and other characters, plus Watson's narrative prose. Re-running merges into the existing file. |
| `holmes_scenes_harvest.py` | Harvests Sidney Paget's Strand Magazine illustrations from Wikimedia Commons into `holmes_scenes.json`, tagged with the story and book each one illustrated. |
| `holmes_post.py` | Picks an unposted quote, finds a matching illustration (same story first, then same book, then any Paget scene), and posts both to Bluesky. Posted-state is tracked in `holmes_state.json`. |
| `holmes_images_harvest.py` | **Retired.** Harvested Victorian-London photographs from the Library of Congress, the original image source. Kept for reference; `holmes_post.py` no longer reads its output. |

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

- The harvested data pools (`holmes_quotes.json`, `holmes_scenes.json`) and the
  runtime state (`holmes_state.json`) are gitignored — rebuild them locally with
  the harvest scripts. A fresh clone therefore holds only the code.
- The Bluesky credential lives in the macOS Keychain, never in the repo.
- Everything posted is public domain: the source texts from Project Gutenberg, and
  Paget's illustrations, published in The Strand Magazine in the 1890s, via Wikimedia
  Commons. Each post credits the illustrator.
- The post is a single Bluesky post (quote, attribution, credit, `#SherlockHolmes`),
  falling back to a thread only when it would exceed the character limit.

## License

MIT — see [LICENSE](LICENSE).
