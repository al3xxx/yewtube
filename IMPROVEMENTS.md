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
- **Keyboard Interactivity**: Restored standard input flow to `mpv` while maintaining clean terminal output, ensuring Space (pause) and Arrow keys (seek) work during playback.

## 4. Metadata & Sorting (Completed)
- **Search Metadata**: Patched `youtubesearchpython` to correctly extract `publishedTime` from YouTube's varying renderer formats.
- **Chronological Sorting**: Implemented a weight-based heuristic to convert relative upload strings (e.g., "2 weeks ago", "5 months ago") into sortable numeric values, fixing alphabetical sorting bugs.
- **Matching Accuracy**: Improved the `best_song_match` algorithm in `album_search` and `spotify_playlist` by fixing duration variance scoring and preventing negative score components.

## 5. Security & Reliability Fixs (Completed)
- **Resource Management**: Implemented context managers for file downloads to prevent file handle leaks during network interruptions.
- **Insecure Temp Files**: Replaced `tempfile.mktemp` with secure `tempfile.mkdtemp` in both `mpv` and `mplayer` backends to prevent race conditions.
- **Safe Process Control**: Refactored the `VLC` backend to use granular process termination (`terminate()`) instead of aggressive system-wide `pkill`.
- **API Modernization**: Replaced all remaining legacy `pafy.call_gdata` calls with modern search and metadata retrieval methods across `generate_playlist`, `spotify_playlist`, and `description_parser`.
- **Input Robustness**: Fixed potential `IndexError` in the duration parser and `ZeroDivisionError` in metadata matching logic.
- **Shadowing Fixes**: Resolved variable shadowing (e.g., `c` loop variable vs `c` color module) to prevent subtle logic errors.

## 6. Code Quality (Completed)
- **Linting & Formatting**: Adopted `ruff` for fast linting and formatting.
- **Consistency**: Standardized on an 80-character line limit and project-wide formatting.
- **Type Safety**: Converted bare `except` blocks to explicit `except Exception:` to prevent catching system signals or masking fatal errors.

## 7. User Interface & Playback Control (Completed)
- **Clean Playback UI**: Streamlined the status bar by replacing the scrolling track size and YouTube ID with a static track title (truncated to 20 characters), providing a cleaner and more readable playback experience.
- **Sequential Autoplay**: Overhauled the `AUTOPLAY` logic to implement a predictable sequential flow through the current song list. This removed redundant random-related-video recursion in favor of stable list traversal.
- **Advanced Navigation**:
    - **Skip to Next**: Re-mapped the `q` key to skip to the next track when `AUTOPLAY` is enabled, rather than stopping playback.
    - **Previous Track ('p')**: Added the `p` key for previous track navigation in both `mpv` and `mplayer` backends.
    - **Help Legend**: Updated the in-player help text to dynamically show `[p] prev [q] next` when autoplay is active.
- **Context-Aware Playback**: Refactored the playback command to provide the player with full list context and a `start_index`. This ensures that navigation keys (`p`/`q`) can correctly traverse the entire original list even when a single track is selected for play.
- **MPlayer Modernization**: Restored standard terminal interactivity (`stdin`) to the `mplayer` backend, ensuring parity with `mpv` for keyboard controls like pause and seek.
- **Diagnostic Transparency**: Improved exception handling in metadata retrieval to surface original error messages, aiding in the diagnosis of private or geo-restricted content.

## 8. Extractor Modernization (yt-dlp Transition)
- **Unified Extraction Layer**: Replaced the legacy `pafy` module with a modern `extractor.py` abstraction. This layer directly interfaces with `yt-dlp` and `youtubesearchpython`, providing a stable and high-performance foundation for all media operations.
- **VideoInfo Abstraction**: Implemented a standardized `VideoInfo` class that ensures consistent metadata access across the entire application, eliminating brittle dictionary-key dependencies.
- **Enhanced Stream Selection**:
    - Leveraged `yt-dlp`'s detailed format metadata for more accurate stream identification (audio vs mixed vs video-only).
    - Improved resolution-based filtering and bitrate-aware sorting for higher quality playback.
    - Added resilient expiry parsing for stream URLs to optimize playback start times.
- **Global terminology migration**: Conducted a project-wide cleanup to remove all legacy `pafy` naming from imports, variables, and global caches (e.g., `g.pafs` -> `g.metadata_cache`).
- **Improved Metadata Retrieval**: Unified search and playlist metadata processing to use modern scraping techniques, significantly reducing "no results" or "extraction failed" errors.
- **Search Fallback Mechanism**: Implemented a robust fallback to `yt-dlp` for video searches when `youtubesearchpython` crashes or fails (common with high-profile artists like 'Lana Del Rey' due to complex shelf renderers).

---
*Last updated: February 25, 2026*
