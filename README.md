# Sherlock Quotes

A Bluesky bot that posts a quote from the Sherlock Holmes canon paired with period
art. When Sidney Paget illustrated the very story the quote came from, the post
carries his Strand Magazine illustration of it. Paget's surviving Strand work covers
15 of the canon's works, so for the other three quotes in four the post carries a
Victorian British photograph from the Library of Congress instead.

Posts to [@sherlockquotes.bsky.social](https://bsky.app/profile/sherlockquotes.bsky.social).

## How it works

Four scripts. The three harvesters build the pools; `holmes_post.py` posts from them:

| Script | Role |
|---|---|
| `holmes_harvest.py` | Harvests quotes and prose passages from the public-domain Holmes canon (Project Gutenberg) into `holmes_quotes.json`. Extracts dialogue attributed to Holmes and other characters, plus Watson's narrative prose. Re-running merges into the existing file. |
| `holmes_scenes_harvest.py` | Harvests Sidney Paget's Strand Magazine illustrations from Wikimedia Commons into `holmes_scenes.json`, tagged with the story and book each one illustrated. |
| `holmes_post.py` | Picks an unposted quote, then its art: a Paget illustration when one exists for that quote's own story or novel, otherwise a Library of Congress photograph. Posts both to Bluesky. Posted-state is tracked in `holmes_state.json`. |
| `holmes_images_harvest.py` | Harvests Victorian British photographs from the Library of Congress into `holmes_images.json`. This was the original image source, retired in July 2026 when the Paget pool arrived and brought back in August 2026 to cover the works Paget never illustrated. |

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
python3 holmes_scenes_harvest.py   # build/refresh the Paget illustration pool
python3 holmes_images_harvest.py   # build/refresh the photograph pool
python3 holmes_post.py             # post one quote + image
python3 holmes_post.py --dry-run   # print the post without publishing
```

## Avatar

`avatar/make_avatar.py` draws the account's avatar: a cream "221B" on Strand Magazine
blue, inside a single ruled ring.

```sh
python3 avatar/make_avatar.py            # writes avatar/avatar.png at 1024px
python3 avatar/make_avatar.py --proof    # also writes the 40px feed-size proofs
```

It is drawn rather than photographed, and the script's docstring records the three
measurements behind it: why one ring and not two, why Baskerville and not Georgia,
and why the whole thing is rendered at 4x and downsampled. Setting it on the account
is manual; the script only produces the file.

## Notes

- The harvested data pools (`holmes_quotes.json`, `holmes_scenes.json`,
  `holmes_images.json`) and the runtime state (`holmes_state.json`) are gitignored -
  rebuild them locally with the harvest scripts. A fresh clone therefore holds only
  the code.
- The Bluesky credential lives in the macOS Keychain, never in the repo.
- Everything posted is public domain: the source texts from Project Gutenberg;
  Paget's illustrations, published in The Strand Magazine in the 1890s, via Wikimedia
  Commons; and the Library of Congress photographs. Each post credits the
  illustrator or the photograph's holding library.
- The post is a single Bluesky post (quote, attribution, credit, `#SherlockHolmes`),
  falling back to a thread only when it would exceed the character limit.

## License

MIT — see [LICENSE](LICENSE).
