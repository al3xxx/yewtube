# Improvements and Modernization of yewtube

This document tracks the significant refactoring and modernizations applied to the yewtube (mps-youtube fork) terminal application.

## 1. Dependency Modernization (Completed)
- **Metadata Management**: Replaced legacy `pkg_resources` (via `pip._vendor`) with `importlib.metadata`, ensuring compatibility with Python 3.12+ and environments without `pip` internals.
- **Environment Cleanup**: Removed unused or redundant pip modules including `gnureadline` (replaced by `prompt_toolkit`), `requests`, `charset-normalizer`, and `urllib3`, reducing the application footprint.

## 2. CLI & Interactive Shell (Completed)
- **Framework Migration**: Migrated from manual argument parsing to `click`, providing robust help generation and standard CLI patterns.
- **Shell Upgrade**: Replaced standard `readline` with `prompt_toolkit` for all interactive input.
- **Intelligent Completion**: Implemented a `CommandCompleter` with context-aware logic:
    *   Suggests `true`/`false` only for boolean configuration items.
    *   Provides playlist name completions for `open`, `view`, etc.
    *   Provides command-specific sub-argument completions (e.g., for `sort`).
- **Enhanced Exit Flow**: Improved the Ctrl-C exit confirmation prompt to support command history and tab completion.

## 3. Playback Engine & mpv Integration (Completed)
- **Robust IPC Communication**: Rewrote the `mpv` integration to use IPC sockets with improved connection retry logic and secure temporary directory management.
- **Asynchronous Error Capture**: Implemented a background thread to capture `mpv` stderr, allowing the application to detect and report fatal initialization errors (e.g., driver failures) to the UI.
- **Persistent Error Reporting**: Modified the playback loop to display captured errors for 3 seconds before refreshing, ensuring users can diagnose failures.
- **Output Management**:
    *   Muted standard diagnostic output (stdout) to keep the terminal clean.
    *   Unified video suppression logic (fixed crashes when passing both `--no-video` and `--vid=no`).
    *   Removed redundant double-quoting in subprocess arguments.
- **Extensibility**: Added the `aux_mpv_cli_config` setting to allow users to pass arbitrary additional command-line arguments to `mpv`.

## 4. Metadata & Sorting (Completed)
- **Search Metadata**: Patched `youtubesearchpython` to correctly extract `publishedTime` from YouTube's varying renderer formats.
- **Chronological Sorting**: Implemented a weight-based heuristic to convert relative upload strings (e.g., "2 weeks ago", "5 months ago") into sortable numeric values, fixing alphabetical sorting bugs.
- **Matching Accuracy**: Improved the `best_song_match` algorithm in `album_search` and `spotify_playlist` by fixing duration variance scoring and preventing negative score components.

## 5. Core Utility & Reliability (Completed)
- **Terminal Detection**: Modernized `util.getxy` to use `shutil.get_terminal_size()` as the primary detection method.
- **Input Robustness**: Fixed a potential `IndexError` in the duration parser when handling short time tokens.
- **Global State Synchronization**: Ensured that fixes applied to the root source code are automatically mirrored to the active virtual environment during development.

## 6. In human digestable format
- **Search errors**: No more annoying 'proxy error' we've made it compatible with httpx 0.28.1 I hope
- **Tab completion**: I'm just lazy and I like it. Also saves time listing saved playlists - you can just type 'open' and tab through it
- **List sorting**: I think this is huge - you can sort search results by name, age duration, you can sort also playlists, both ways, just repeat sort command second time to reverse sort
- **MPV aux command line**: added because I felt like it.
---
*Last updated: February 18, 2026*
