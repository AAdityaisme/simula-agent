# simula-agent

Mobile app → product model → 1:1 mock → rewarded-ad proposal. Simula take-home #2. The assignment, verbatim: [docs/ASSIGNMENT.md](docs/ASSIGNMENT.md).

**Status: PR 0 (foundation).** Every stage command exists and stops at a clear "not built yet (PR n)". Contracts are frozen in [docs/CONTRACTS.md](docs/CONTRACTS.md).

## Setup

Needs: macOS or Linux, Python 3.12 + [uv](https://docs.astral.sh/uv/), Node 20+, an Android emulator with the test apps installed (`adb` on the path or `ANDROID_HOME` set), and API keys.

```sh
uv sync
chflags -R nohidden .venv 2>/dev/null || true   # macOS only; no-op elsewhere
npm ci                                   # pins @mobilenext/mobile-mcp 1.0.5
uv run playwright install chromium
cp .env.example .env                     # then fill in the three keys
uv run simula doctor --keys              # checks everything; the key checks cost about a cent
```

| Service | Used for | Key |
|---|---|---|
| Anthropic | Opus 5.5 (model meaning, mock, proposer, fixer), Sonnet 5.5 (screenshots, QA critic, judge 1), Haiku 4.5 (dev profile, Jev fallback) | `ANTHROPIC_API_KEY` |
| OpenAI | gpt-6-sol (judge 2), gpt-6-luna (dev profile) | `OPENAI_API_KEY` |
| TypeSafe | Jev (`jev-latest`): ranks the explorer's next tap | `TYPESAFE_API_KEY` |

## Commands

```sh
uv run simula doctor [--keys]
uv run simula explore|model|mock|qa|propose|judge|flows APP [--run ID]
uv run simula run APP [--new] [--from STAGE] [--budget deep|transfer]
uv run simula note "what I fixed by hand" [--usd 0.40]
```

`APP` is a file in `config/apps/`; its only field is the package name. Every stage command also takes `--profile real|dev`, `--no-cache`, `--replay` (cache only, no network, no device), `--usd-cap`, `--allow-account-create`, and `--allow-fixtures`.

## Tests

```sh
uv run pytest                    # offline, what CI runs; pyproject.toml adds -m "not live"
uv run pytest -m live            # real API calls, a few cents (needs .env keys; the emulator for explore)
```

Golden product models for JanitorAI, Luzia, and AOL live in `tests/fixtures/golden/`. They are test data only; rebuild them with `uv run python tests/fixtures/golden/build_golden.py`.

## Troubleshooting

`ModuleNotFoundError: No module named 'simula'` on a Mac with iCloud Desktop & Documents sync: iCloud marks files under `.venv` hidden, and Python 3.12.2+ skips hidden `.pth` files, which breaks the editable install. Run the `chflags` line from Setup again, or clone outside `~/Desktop`.
